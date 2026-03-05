#!/usr/bin/env bash
# entrypoint.sh — seed custom_nodes on the EBS-mounted volume at first boot,
# then hand off to the CMD (ComfyUI main.py).
#
# Each entry in PLUGINS is "directory-name|git-url".
# If the directory already contains a .git folder the clone is skipped,
# making every subsequent container start a no-op for that plugin.
#
# To add a plugin: append a line to PLUGINS and redeploy.
# Plugins installed via ComfyUI-Manager UI persist on EBS untouched.

set -euo pipefail

CUSTOM_NODES_DIR="$(dirname "$0")/custom_nodes"
mkdir -p "$CUSTOM_NODES_DIR"

PLUGINS=(
  "ComfyUI-Manager|https://github.com/Comfy-Org/ComfyUI-Manager"
  "ComfyUI-Hunyuan3d-2-1|https://github.com/visualbruno/ComfyUI-Hunyuan3d-2-1"
)

for entry in "${PLUGINS[@]}"; do
  name="${entry%%|*}"
  url="${entry##*|}"
  dest="$CUSTOM_NODES_DIR/$name"

  if [ -d "$dest/.git" ]; then
    echo "[entrypoint] $name already installed — skipping"
  else
    echo "[entrypoint] Installing $name from $url ..."
    git clone --depth 1 "$url" "$dest"
    if [ -f "$dest/requirements.txt" ]; then
      # Install most requirements normally; packages that inspect torch at build
      # time (e.g. diso) must be excluded here and installed separately below.
      grep -v -E '^\s*diso\b' "$dest/requirements.txt" | \
        pip install --no-cache-dir -r /dev/stdin || true
      # diso builds a CUDA extension and calls `import torch` during setup.py,
      # so it must see the already-installed torch — use --no-build-isolation.
      pip install --no-cache-dir --no-build-isolation diso || true
    fi
    # Patch: guard against None outputs in Hy3D21VAEDecode when marching cubes
    # finds no isosurface, which causes AttributeError: 'NoneType'.mesh_f
#     if [ "$name" = "ComfyUI-Hunyuan3d-2-1" ] && [ -f "$dest/nodes.py" ]; then
#       NODES_PY="$dest/nodes.py" python3 - <<'PYEOF'
# import os, re, pathlib
# p = pathlib.Path(os.environ["NODES_PY"])
# src = p.read_text()
# # Guard against None when marching cubes finds no isosurface
# pattern = r'(        if force_offload==True:\n            vae\.to\(offload_device\)\n\s*\n)(        outputs\.mesh_f = outputs\.mesh_f\[:, ::-1\])'
# replacement = (
#     r'\1'
#     '        if outputs is None:\n'
#     '            raise RuntimeError(\n'
#     '                "VAE decode produced no mesh geometry. The latents may represent "\n'
#     '                "a degenerate shape — try lowering mc_level or increasing octree_resolution."\n'
#     '            )\n'
#     r'\2'
# )
# patched, n = re.subn(pattern, replacement, src)
# if n:
#     p.write_text(patched)
#     print("[entrypoint] Applied nodes.py None-outputs guard patch")
# else:
#     print("[entrypoint] WARNING: nodes.py patch target not found — skipping")
# PYEOF
#     fi
    echo "[entrypoint] $name installed"
  fi
done

echo "[entrypoint] Plugin seeding complete — starting ComfyUI"
exec "$@"

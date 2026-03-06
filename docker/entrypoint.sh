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
  "ComfyUI-Trellis2|https://github.com/visualbruno/ComfyUI-Trellis2"
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
    # Trellis2 ships pre-built CUDA wheels that must be installed BEFORE
    # requirements.txt, because o_voxel's metadata declares cumesh via a git URL
    # which conflicts with the local .whl even though both are v0.0.1.
    # Fix: install cumesh wheel first so pip sees it satisfied, then install
    # o_voxel with --no-deps to skip its conflicting git-sourced requirement.
    if [ "$name" = "ComfyUI-Trellis2" ]; then
      WHEELS_DIR="$dest/wheels/Linux/Torch291"
      if [ -d "$WHEELS_DIR" ]; then
        echo "[entrypoint] Installing Trellis2 wheels from $WHEELS_DIR ..."
        pip install --no-cache-dir "$WHEELS_DIR"/cumesh-*.whl
        pip install --no-cache-dir --no-deps "$WHEELS_DIR"/o_voxel-*.whl
        for whl in "$WHEELS_DIR"/*.whl; do
          case "$(basename "$whl")" in
            cumesh-*|o_voxel-*) continue ;;
          esac
          pip install --no-cache-dir "$whl" || true
        done
        echo "[entrypoint] Trellis2 wheels installed"
      else
        echo "[entrypoint] WARNING: $WHEELS_DIR not found — skipping Trellis2 wheels"
      fi
    fi

    if [ -f "$dest/requirements.txt" ]; then
      # Exclude packages already satisfied by the Trellis2 wheels above, plus
      # diso which must be built against the installed torch (no-build-isolation).
      grep -v -E '^\s*(diso|cumesh|o.?voxel|nvdiffrast|flex.?gemm|nvdiffrec)\b' "$dest/requirements.txt" | \
        pip install --no-cache-dir -r /dev/stdin || true
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

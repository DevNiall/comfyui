#!/usr/bin/env bash
# entrypoint.sh — on first boot:
#   1. clones ComfyUI into the EBS-backed /opt/comfyui volume
#   2. creates a Python venv in /home/comfy/.venv and installs all packages
#   3. seeds declared plugins into /opt/comfyui/custom_nodes/
# Subsequent boots skip steps 1–3 via idempotency guards and start immediately.
#
# To add a plugin: append a "directory-name|git-url" line to PLUGINS and redeploy.
# Plugins installed via ComfyUI-Manager UI persist on EBS untouched.

set -euo pipefail

COMFYUI_DIR=/opt/comfyui
VENV_DIR=/home/comfy/.venv
CUSTOM_NODES_DIR=$COMFYUI_DIR/custom_nodes

# --- 1. Ensure ComfyUI source exists on the persistent volume ---
if [ -f "$COMFYUI_DIR/main.py" ]; then
  echo "[entrypoint] ComfyUI source already present — skipping bootstrap"
else
  mkdir -p "$COMFYUI_DIR"
  if [ -z "$(ls -A "$COMFYUI_DIR" 2>/dev/null)" ]; then
    echo "[entrypoint] Cloning ComfyUI into empty $COMFYUI_DIR ..."
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI "$COMFYUI_DIR"
    echo "[entrypoint] ComfyUI cloned"
  else
    echo "[entrypoint] $COMFYUI_DIR is non-empty; bootstrapping ComfyUI files without deleting existing data ..."
    TMP_CLONE_DIR="$(mktemp -d /tmp/comfyui-src.XXXXXX)"
    git clone --depth 1 https://github.com/comfyanonymous/ComfyUI "$TMP_CLONE_DIR"
    # Merge code into the persistent volume but preserve any existing files.
    cp -an "$TMP_CLONE_DIR"/. "$COMFYUI_DIR"/
    rm -rf "$TMP_CLONE_DIR"
    echo "[entrypoint] ComfyUI source bootstrapped into existing volume"
  fi
fi

if [ ! -f "$COMFYUI_DIR/extra_model_paths.yaml" ]; then
  cp /home/comfy/extra_model_paths.yaml "$COMFYUI_DIR/extra_model_paths.yaml"
fi

mkdir -p "$CUSTOM_NODES_DIR"

# --- 2. Python venv + packages (first boot only) ---
if [ ! -f "$VENV_DIR/.pip_bootstrap_done" ]; then
  echo "[entrypoint] Creating venv and installing packages ..."
  python3.12 -m venv "$VENV_DIR"
  pip install --no-cache-dir --upgrade pip setuptools wheel

  # PyTorch 2.9.1 — must match the Trellis2 Linux/Torch291 wheel ABI
  pip install --no-cache-dir \
      torch==2.9.1 torchvision torchaudio \
      --index-url https://download.pytorch.org/whl/cu130

  pip install --no-cache-dir nvitop "rembg[gpu]"
  pip install --no-cache-dir -r "$COMFYUI_DIR/requirements.txt"

  touch "$VENV_DIR/.pip_bootstrap_done"
  echo "[entrypoint] Package installation complete"
else
  echo "[entrypoint] Venv already bootstrapped — skipping pip install"
fi

# --- 3. Seed plugins (each clone is a no-op after first install) ---

PLUGINS=(
  # "ComfyUI-Manager|https://github.com/Comfy-Org/ComfyUI-Manager"
  # "ComfyUI-Hunyuan3d-2-1|https://github.com/visualbruno/ComfyUI-Hunyuan3d-2-1"
  # "ComfyUI-Trellis2|https://github.com/visualbruno/ComfyUI-Trellis2"
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
    echo "[entrypoint] $name installed"
  fi
done

echo "[entrypoint] Plugin seeding complete — starting ComfyUI"
exec "$@"

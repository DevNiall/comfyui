# FROM nvidia/cuda:12.6.3-runtime-ubuntu22.04
FROM nvidia/cuda:13.1.1-devel-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive TZ=Europe/London

LABEL org.opencontainers.image.title="ComfyUI" \
    org.opencontainers.image.description="ComfyUI — GPU-accelerated generative AI image server" \
    org.opencontainers.image.source="https://github.com/DevNiall/comfyui"

# System packages — includes btop, tmux for monitoring/multiplexing
RUN apt-get update && apt-get install -y \
    git make build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
    libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    git-lfs ffmpeg libsm6 libxext6 cmake libgl1 libopengl0 \
    cuda-nvcc-13-1 libcudart12 \
    libcusparse-dev-13-1 libcublas-dev-13-1 libcusolver-dev-13-1 \
    libcufft-dev-13-1 libcurand-dev-13-1 cuda-cudart-dev-13-1 \
    btop tmux python-is-python3 \
    python3.12 python3.12-dev python3.12-venv \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install

# Rename the default ubuntu user → comfy (preserves UID/GID 1000)
RUN usermod  -l comfy      ubuntu \
    && usermod  -d /home/comfy -m comfy \
    && groupmod -n comfy      ubuntu \
    && mkdir -p /opt/comfyui \
    && chown comfy:comfy /opt/comfyui

USER comfy
ENV HOME=/home/comfy \
    PATH=/home/comfy/.venv/bin:/home/comfy/.local/bin:$PATH

WORKDIR /home/comfy

# Entrypoint — on first boot: clones ComfyUI into the EBS-backed /opt/comfyui,
# creates a venv in /home/comfy/.venv and installs all packages, then seeds
# declared plugins. Both volumes persist across reboots via EBS.
# Subsequent boots skip all setup steps and start ComfyUI immediately.
COPY --chown=comfy:comfy entrypoint.sh /home/comfy/entrypoint.sh
RUN chmod +x /home/comfy/entrypoint.sh

# Model paths config — seeded into /opt/comfyui/ by the entrypoint on first clone
COPY extra_model_paths.yaml /home/comfy/extra_model_paths.yaml

EXPOSE 8181

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8181/ || exit 1

ENTRYPOINT ["/home/comfy/entrypoint.sh"]
CMD ["python", "/opt/comfyui/main.py", \
    "--listen", "0.0.0.0", \
    "--port", "8181", \
    "--output-directory", "/opt/comfyui/output/"]

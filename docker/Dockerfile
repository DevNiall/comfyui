# FROM nvidia/cuda:12.6.3-runtime-ubuntu22.04
FROM nvidia/cuda:13.1.1-devel-ubuntu24.04
ENV DEBIAN_FRONTEND=noninteractive TZ=Europe/London

# System packages — includes btop, tmux for monitoring/multiplexing
RUN apt-get update && apt-get install -y \
    git make build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
    libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    git-lfs ffmpeg libsm6 libxext6 cmake libgl1 libopengl0 \
    cuda-nvcc-13-1 libcudart12 \
    libcusparse-dev-13-1 libcublas-dev-13-1 libcusolver-dev-13-1 \
    libcufft-dev-13-1 libcurand-dev-13-1 cuda-cudart-dev-13-1 \
    btop tmux \
    python3.12 python3.12-dev python3.12-venv python3-pip \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install

# Use existing non-root user from base image
USER ubuntu
ENV HOME=/home/ubuntu PATH=/home/ubuntu/.local/bin:$PATH

# Python — Ubuntu 24.04 ships 3.12 natively; venv satisfies PEP 668
RUN python3.12 -m venv /home/ubuntu/.venv
ENV PATH=/home/ubuntu/.venv/bin:$PATH
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# PyTorch 2.9.1 — must match the Trellis2 Linux/Torch291 wheel ABI
RUN pip install --no-cache-dir \
    torch==2.9.1 torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu130

# nvitop for GPU monitoring
RUN pip install --no-cache-dir nvitop "rembg[gpu]"

# ComfyUI
WORKDIR /home/ubuntu/opt/ComfyUI
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI . && \
    pip install --no-cache-dir -r requirements.txt

# Model paths config
COPY extra_model_paths.yaml ./extra_model_paths.yaml

# Entrypoint — seeds plugins declared in entrypoint.sh into the EBS-mounted
# custom_nodes/ dir on first boot, then execs the CMD.
COPY --chown=ubuntu:ubuntu entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

EXPOSE 8181

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "/home/ubuntu/opt/ComfyUI/main.py", \
     "--listen", "0.0.0.0", \
     "--port", "8181", \
     "--output-directory", "/home/ubuntu/opt/ComfyUI/output/"]

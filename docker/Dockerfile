FROM nvidia/cuda:12.6.3-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive TZ=America/Los_Angeles

# System packages — includes btop, tmux for monitoring/multiplexing
RUN apt-get update && apt-get install -y \
    git make build-essential libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev wget curl llvm \
    libncursesw5-dev xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
    git-lfs ffmpeg libsm6 libxext6 cmake libgl1-mesa-glx \
    btop tmux \
    && rm -rf /var/lib/apt/lists/* \
    && git lfs install

# Non-root user
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user PATH=/home/user/.local/bin:$PATH

# Python via pyenv
RUN curl https://pyenv.run | bash
ENV PATH=$HOME/.pyenv/shims:$HOME/.pyenv/bin:$PATH
ENV PYTHON_VERSION=3.12.0
RUN pyenv install $PYTHON_VERSION && \
    pyenv global $PYTHON_VERSION && \
    pip install --no-cache-dir --upgrade pip setuptools wheel

# PyTorch with CUDA 12.6
RUN pip install --no-cache-dir \
    torch torchvision torchaudio \
    --extra-index-url https://download.pytorch.org/whl/cu126

# nvitop for GPU monitoring
RUN pip install --no-cache-dir nvitop

# ComfyUI
WORKDIR /home/user/opt/ComfyUI
RUN git clone --depth 1 https://github.com/comfyanonymous/ComfyUI . && \
    pip install --no-cache-dir -r requirements.txt

# ComfyUI-Manager
RUN mkdir -p custom_nodes/ComfyUI-Manager && \
    git clone --depth 1 https://github.com/Comfy-Org/ComfyUI-Manager custom_nodes/ComfyUI-Manager && \
    pip install --no-cache-dir -r custom_nodes/ComfyUI-Manager/requirements.txt

# Model paths config
COPY extra_model_paths.yaml ./extra_model_paths.yaml

EXPOSE 8181

CMD ["python", "/home/user/opt/ComfyUI/main.py", \
     "--listen", "0.0.0.0", \
     "--port", "8181", \
     "--output-directory", "/home/user/opt/ComfyUI/output/"]

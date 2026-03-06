#!/bin/bash
set -euo pipefail
exec > >(tee /var/log/comfyui-bootstrap.log) 2>&1
# Ensure AWS CLI and other tools are on PATH (cloud-init runs with minimal PATH)
export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH
echo "=== ComfyUI Bootstrap Starting ==="

# Install AWS CLI v2 if not present (DL Base AMI ships without it)
if ! command -v aws &>/dev/null; then
  echo "AWS CLI not found — installing AWS CLI v2..."
  dnf install -y unzip
  curl -fsSL 'https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip' -o /tmp/awscliv2.zip
  unzip -q /tmp/awscliv2.zip -d /tmp/awscliv2
  /tmp/awscliv2/aws/install --bin-dir /usr/local/bin --install-dir /usr/local/aws-cli --update
  rm -rf /tmp/awscliv2.zip /tmp/awscliv2
  echo "AWS CLI v2 installed: $(aws --version)"
fi

# Ensure Docker is running (DL Base AMI has Docker pre-installed but may need a start)
systemctl enable --now docker 2>/dev/null || true

# Metadata
TOKEN=$(curl -sX PUT 'http://169.254.169.254/latest/api/token' -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')
REGION=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
INSTANCE_ID=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-id)
AZ=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/availability-zone)

STACK_TAG="@@STACK_TAG@@"
VOLUME_SIZE=@@VOLUME_SIZE@@
ECR_IMAGE="@@ECR_IMAGE@@"
HF_TOKEN_PARAM="@@HF_TOKEN_PARAM@@"

# --- Clean up orphaned volumes from previous launches ---
echo "Cleaning up orphaned comfyui-data volumes..."
ORPHANED_VOLS=$(aws ec2 describe-volumes \
  --filters "Name=tag:Name,Values=comfyui-data" \
            "Name=tag:comfyui-stack,Values=$STACK_TAG" \
            "Name=status,Values=available" \
  --query 'Volumes[].VolumeId' --output text \
  --region $REGION)
for vol in $ORPHANED_VOLS; do
  if [ -n "$vol" ] && [ "$vol" != "None" ]; then
    echo "Deleting orphaned volume $vol..."
    aws ec2 delete-volume --volume-id "$vol" --region $REGION || true
  fi
done

# --- Find latest snapshot ---
echo "Looking for latest snapshot with tag comfyui-stack=$STACK_TAG..."
SNAPSHOT_ID=$(aws ec2 describe-snapshots \
  --filters "Name=tag:comfyui-stack,Values=$STACK_TAG" \
  --owner-ids self \
  --query 'sort_by(Snapshots,&StartTime)[-1].SnapshotId' \
  --output text \
  --region $REGION)

# --- Create or restore data volume ---
if [ "$SNAPSHOT_ID" != "None" ] && [ -n "$SNAPSHOT_ID" ]; then
  echo "Restoring from snapshot: $SNAPSHOT_ID"
  VOLUME_ID=$(aws ec2 create-volume \
    --availability-zone $AZ \
    --size $VOLUME_SIZE \
    --volume-type gp3 \
    --encrypted \
    --snapshot-id $SNAPSHOT_ID \
    --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=comfyui-data},{Key=comfyui-stack,Value=$STACK_TAG}]" \
    --query 'VolumeId' --output text \
    --region $REGION)
  FROM_SNAPSHOT=true
else
  echo "No snapshot found — creating fresh volume"
  VOLUME_ID=$(aws ec2 create-volume \
    --availability-zone $AZ \
    --size $VOLUME_SIZE \
    --volume-type gp3 \
    --encrypted \
    --tag-specifications "ResourceType=volume,Tags=[{Key=Name,Value=comfyui-data},{Key=comfyui-stack,Value=$STACK_TAG}]" \
    --query 'VolumeId' --output text \
    --region $REGION)
  FROM_SNAPSHOT=false
fi

# --- Wait for volume to be available ---
echo "Waiting for volume $VOLUME_ID to become available..."
aws ec2 wait volume-available --volume-ids $VOLUME_ID --region $REGION

# --- Attach volume ---
echo "Attaching volume $VOLUME_ID to $INSTANCE_ID as /dev/sdf..."
aws ec2 attach-volume \
  --volume-id $VOLUME_ID \
  --instance-id $INSTANCE_ID \
  --device /dev/sdf \
  --region $REGION

# Wait for attachment
echo 'Waiting for volume attachment...'
for i in $(seq 1 60); do
  STATE=$(aws ec2 describe-volumes --volume-ids $VOLUME_ID --region $REGION \
    --query 'Volumes[0].Attachments[0].State' --output text 2>/dev/null || echo 'none')
  if [ "$STATE" = "attached" ]; then
    echo "Volume attached successfully"
    break
  fi
  echo "Attachment state: $STATE — waiting..."
  sleep 5
done

# Resolve actual device name dynamically using the volume ID.
# On Nitro instances EBS volumes are exposed as NVMe devices whose by-id symlink
# encodes the volume ID (e.g. nvme-Amazon_Elastic_Block_Store_vol0abc123…).
# This avoids accidentally using the instance-store / ephemeral NVMe disk.
echo "Resolving device for volume $VOLUME_ID..."
VOLUME_ID_STRIPPED="${VOLUME_ID/vol-/vol}"   # vol-0abc… → vol0abc…
DEVICE_LINK="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${VOLUME_ID_STRIPPED}"

DEVICE=""
for attempt in $(seq 1 24); do
  # Preferred: by-id symlink (Nitro/NVMe instances)
  if [ -L "$DEVICE_LINK" ]; then
    DEVICE=$(readlink -f "$DEVICE_LINK")
    echo "Found device via by-id symlink: $DEVICE"
    break
  fi
  # Fallback: xen/paravirtual block device names
  for dev in /dev/xvdf /dev/sdf; do
    if [ -b "$dev" ]; then
      DEVICE="$dev"
      echo "Found device at $dev"
      break 2
    fi
  done
  echo "Device not yet visible (attempt $attempt/24), waiting 5 s..."
  sleep 5
done

if [ -z "$DEVICE" ]; then
  echo "ERROR: Could not find attached device for volume $VOLUME_ID"
  exit 1
fi
echo "Using device: $DEVICE"

# --- Format if new volume ---
if [ "$FROM_SNAPSHOT" = "false" ]; then
  echo "Formatting new volume..."
  mkfs.ext4 -m 0 $DEVICE
fi

# --- Mount ---
mkdir -p /data/comfyui
mount $DEVICE /data/comfyui

# --- Swap (32 GB on EBS data volume) ---
# File is placed on the data volume so it persists across snapshot restores.
# The guard + swapon '|| true' make this idempotent on subsequent boots.
SWAPFILE=/data/comfyui/.swapfile
if [ ! -f "$SWAPFILE" ]; then
  echo "Creating 32 GB swap file..."
  fallocate -l 32G "$SWAPFILE"
  chmod 600 "$SWAPFILE"
  mkswap "$SWAPFILE"
fi
swapon "$SWAPFILE" 2>/dev/null || true
echo "Swap enabled: $(swapon --show)"

# Kernel memory tunables for large ML model loading
# vm.swappiness=10:       strongly prefer physical RAM, only use swap as overflow
# vm.overcommit_memory=1: allow malloc to succeed even when physical RAM is low;
#                          without this, the kernel may refuse a large allocation
#                          pre-emptively even though swap would cover it.
sysctl -w vm.swappiness=10
sysctl -w vm.overcommit_memory=1

# Create directory structure if fresh
if [ "$FROM_SNAPSHOT" = "false" ]; then
  mkdir -p /data/comfyui/{models/{checkpoints,clip,clip_vision,configs,controlnet,diffusers,embeddings,gligen,hypernetworks,loras,mmdets,onnx,sams,style_models,ultralytics,unet,upscale_models,vae,vae_approx},custom_nodes,output,input}
  chown -R 1000:1000 /data/comfyui
fi

# --- Retrieve HuggingFace token ---
HF_TOKEN=$(aws ssm get-parameter --name "$HF_TOKEN_PARAM" --with-decryption \
  --query 'Parameter.Value' --output text --region $REGION 2>/dev/null || echo 'not-set')

# --- ECR login and pull ---
echo "Pulling ComfyUI Docker image..."
ECR_REGISTRY=$(echo $ECR_IMAGE | cut -d/ -f1)
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_REGISTRY
docker pull $ECR_IMAGE

# --- Run ComfyUI container ---
echo "Starting ComfyUI container..."
docker run -d \
  --name comfyui \
  --gpus all \
  --restart unless-stopped \
  --ipc=host \
  -v /data/comfyui/models:/home/user/opt/ComfyUI/models \
  -v /data/comfyui/custom_nodes:/home/user/opt/ComfyUI/custom_nodes \
  -v /data/comfyui/output:/home/user/opt/ComfyUI/output \
  -v /data/comfyui/input:/home/user/opt/ComfyUI/input \
  -e HF_TOKEN=$HF_TOKEN \
  -e MALLOC_ARENA_MAX=2 \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  -p 8181:8181 \
  $ECR_IMAGE

echo "=== ComfyUI Bootstrap Complete ==="
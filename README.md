# ComfyUI on AWS — Simplified Single-User Deployment

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A **cost-optimized, single-user** [ComfyUI](https://github.com/comfyanonymous/ComfyUI) deployment on AWS. This is a simplified version of the [aws-samples/cost-effective-aws-deployment-of-comfyui](https://github.com/aws-samples/cost-effective-aws-deployment-of-comfyui) repository, designed for personal use with unnecessary complexity removed.

All infrastructure is defined as code using AWS CDK (Python). The deployment runs on GPU Spot instances with automated EBS snapshot-based persistence.

## What's Different from the Reference Repository?

This implementation simplifies the original AWS sample by:

- **No Application Load Balancer (ALB)** — Access via SSM port-forwarding only, no public endpoints
- **No Amazon Cognito** — No authentication layer (suitable for personal/development use)
- **No AWS WAF** — Simplified security model for single-user scenarios
- **No ECS Service/Task Definitions** — ComfyUI runs directly in Docker on EC2 (ECS agent is disabled)
- **Existing VPC support** — Uses your existing default VPC (configurable in `.env`)
- **Streamlined snapshots** — Three-tiered strategy: periodic (DLM), termination hooks, and manual snapshots
- **Makefile-driven operations** — All common tasks accessible via simple `make` commands

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Auto Scaling Group (0/1 capacity)                  │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  EC2 Spot (g6.2xlarge + fallbacks)           │   │
│  │  ECS GPU-optimized AMI (Docker + NVIDIA)     │   │
│  │                                              │   │
│  │  ┌──────────────────────────────────────┐   │   │
│  │  │  ComfyUI container (port 8181)       │   │   │
│  │  │  + ComfyUI-Manager                   │   │   │
│  │  └──────────────┬───────────────────────┘   │   │
│  │                 │ /data/comfyui              │   │
│  │  ┌──────────────▼───────────────────────┐   │   │
│  │  │  EBS gp3 data volume (500 GB)        │   │   │
│  │  │  models / custom nodes / output      │   │   │
│  │  └──────────────────────────────────────┘   │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘

Access: SSM Session Manager port-forward → localhost:8181
No inbound security group rules. No SSH key required.
```

### Key Design Principles

- **💰 Cost-Optimized** — Spot pricing (~70–90% cheaper than on-demand) with intelligent fallback instance types
- **💾 Data Persistence** — Models and outputs stored on EBS volume, restored from latest snapshot on every boot
- **🔒 Security-First** — No open ports, SSM-only access, no public endpoints
- **📸 Automated Snapshots** — Triple-redundant backup: DLM periodic + lifecycle hooks + manual snapshots
- **🐳 Docker-Native** — ComfyUI runs directly in Docker (ECS agent disabled for simplicity)
- **⚡ Developer-Friendly** — Comprehensive Makefile with all operations accessible via simple commands

## AWS Services Used

This deployment leverages the following AWS services:

- **[Amazon EC2](https://aws.amazon.com/ec2/)** — GPU instances (Spot pricing) for ComfyUI workloads
- **[Amazon EC2 Auto Scaling](https://aws.amazon.com/ec2/autoscaling/)** — 0/1 capacity for on-demand start/stop
- **[Amazon EBS](https://aws.amazon.com/ebs/)** — Persistent gp3 storage for models, custom nodes, and outputs
- **[AWS Systems Manager (SSM)](https://aws.amazon.com/systems-manager/)** — Secure session-based access and parameter storage
- **[Amazon ECR](https://aws.amazon.com/ecr/)** — Docker image registry for ComfyUI container
- **[AWS Lambda](https://aws.amazon.com/lambda/)** — Snapshot orchestration on termination events
- **[Amazon EventBridge](https://aws.amazon.com/eventbridge/)** — Event routing for lifecycle hooks and Spot interruptions
- **[AWS Data Lifecycle Manager (DLM)](https://aws.amazon.com/ebs/data-lifecycle-manager/)** — Automated periodic snapshots
- **[Amazon CloudWatch Logs](https://aws.amazon.com/cloudwatch/)** — Lambda function logging

## Prerequisites

### Required Tools

- **AWS CLI** configured with credentials (`aws configure`)  
  [Installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- **Node.js** (for CDK CLI: `npm install` or `npx cdk`)  
  [Installation guide](https://nodejs.org/)
- **Python 3.11+** with `pip`
- **Docker** (for building the ComfyUI image locally)  
  [Installation guide](https://docs.docker.com/engine/install/)
- **SSM Session Manager plugin** — required for port-forwarding and remote access  
  [Installation guide](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)

### AWS Account Requirements

- **AWS CDK bootstrapped** in your target account/region — run `make bootstrap` if not already done  
  [CDK Bootstrap documentation](https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html)
- **GPU instance quota** — Ensure your account has quota for GPU Spot instances  
  Check [Service Quotas](https://console.aws.amazon.com/servicequotas/home/services/ec2/quotas/L-3819A6DF) and set `All G and VT Spot Instance Requests` to at least 4 vCPUs
- **HuggingFace token** stored in SSM Parameter Store — must be created **before** deploying the stack  
  Run `make set-hf-token` to store your token securely
- **VPC** — Uses your existing VPC by default (configurable via `.env` / `.env.<AWS_PROFILE>`)

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt
npm install

# 2. Create local configuration from template
cp .env.example .env

# 3. (Optional) add profile-specific overrides, e.g. for AWS_PROFILE=dev
cp .env.example .env.dev

# 4. Bootstrap CDK (one-time per account/region)
make bootstrap

# 5. Store your HuggingFace token (required before deployment)
#    Creates an encrypted SecureString in SSM Parameter Store
make set-hf-token

# 6. Deploy
make deploy

# 7. Access ComfyUI in your browser at http://localhost:8181
make comfyui
```

On first deploy, an empty 500 GB data volume is created and the directory structure for all ComfyUI model types is initialised automatically. On subsequent starts the latest snapshot is restored.

## Configuration

Configuration now comes from environment variables and dotenv files.

Precedence is:
1. Shell environment variables
2. `.env.<AWS_PROFILE>`
3. `.env`
4. Defaults in `app.py`

Supported keys in `.env` / `.env.<AWS_PROFILE>`:

| Key | Default | Description |
|---|---|---|
| `AWS_PROFILE` | `default` | AWS profile and profile-specific dotenv selector |
| `CDK_DEFAULT_ACCOUNT` | none | Target AWS account for CDK deploy (required) |
| `CDK_DEFAULT_REGION` | none | Target AWS region for CDK deploy (required) |
| `INSTANCE_TYPE` | `g6.2xlarge` | Primary GPU instance type |
| `FALLBACK_INSTANCE_TYPES` | stack defaults | Comma-separated fallback spot instance types |
| `SPOT_MAX_PRICE` | `1.20` | Maximum Spot price (USD/hr), uncapped if ommitted |
| `DATA_VOLUME_SIZE_GB` | `500` | EBS data volume size in GB |
| `SNAPSHOT_INTERVAL_HOURS` | `12` | DLM periodic snapshot interval in hours |
| `SNAPSHOT_RETAIN_COUNT` | `3` | Number of snapshots retained |
| `HF_TOKEN_PARAM` | `/comfyui/hf-token` | SSM parameter path for HuggingFace token |
| `VPC_ID` | `vpc-0a0078c96978cb8bb` | VPC ID to deploy into |

**Example profile-specific overrides** (`.env.dev`):

```bash
AWS_PROFILE=dev
CDK_DEFAULT_ACCOUNT=123456789012
CDK_DEFAULT_REGION=eu-west-2
INSTANCE_TYPE=g5.xlarge
DATA_VOLUME_SIZE_GB=1000
SNAPSHOT_INTERVAL_HOURS=6
HF_TOKEN_PARAM=/comfyui-dev/hf-token
VPC_ID=vpc-0123456789abcdef0
```

When you run commands with `AWS_PROFILE=dev`, Makefile and CDK will automatically load `.env.dev`.

### GPU Instance Type Reference

| Instance Type | GPU | vCPU | Memory | Typical Spot Price (us-east-1) |
|---------------|-----|------|--------|-------------------------------|
| `g6.xlarge` | NVIDIA L4 (1x) | 4 | 16 GiB | ~$0.30/hr |
| `g6.2xlarge` | NVIDIA L4 (1x) | 8 | 32 GiB | ~$0.40/hr |
| `g5.xlarge` | NVIDIA A10G (1x) | 4 | 16 GiB | ~$0.35/hr |
| `g5.2xlarge` | NVIDIA A10G (1x) | 8 | 32 GiB | ~$0.45/hr |

*Prices are approximate and vary by region and availability. Check current [EC2 Spot Pricing](https://aws.amazon.com/ec2/spot/pricing/).*

## Makefile Reference

### CDK Operations

| Command | Description |
|---|---|
| `make deploy` | Deploy (or update) the stack |
| `make destroy` | Destroy the stack (data volumes and snapshots are preserved) |
| `make diff` | Show pending infrastructure changes |
| `make synth` | Synthesise the CloudFormation template |
| `make bootstrap` | Bootstrap CDK in your AWS account/region |

### Instance Lifecycle

| Command | Description |
|---|---|
| `make start` | Start the ComfyUI instance (sets ASG desired=1) |
| `make stop` | Stop the instance — triggers a snapshot before termination |
| `make status` | Show ASG, instance, and latest snapshot status |

### Connectivity

| Command | Description |
|---|---|
| `make comfyui` | Port-forward ComfyUI to `http://localhost:8181` |
| `make connect` | Open an SSM shell on the EC2 host |
| `make ssh-container` | Shell into the running ComfyUI Docker container |
| `make logs` | Tail ComfyUI container logs (works even if container has exited) |
| `make bootstrap-log` | View the EC2 boot/init log |
| `make diagnose` | Full boot diagnostics: bootstrap log + docker state + cloud-init output |

### Snapshots

| Command | Description |
|---|---|
| `make snapshot` | Manually snapshot the data volume right now |
| `make list-snapshots` | List all ComfyUI EBS snapshots |
| `make delete-snapshots` | Delete all snapshots (with confirmation) |

### HuggingFace Token

| Command | Description |
|---|---|
| `make set-hf-token` | Store your HF token encrypted in SSM Parameter Store |
| `make get-hf-token` | Confirm whether a token is configured |

### Cleanup

For the sake of preventing data loss from accidental deletions, the complete teardown process is semi-automated. To cleanup and remove everything:

#### Option 1: Preserve Data (Recommended)

```bash
make destroy  # Deletes stack but keeps volumes and snapshots
```

This removes the compute infrastructure but preserves your data. You can redeploy later with `make deploy` and your models/workflows will be restored.

#### Option 2: Complete Teardown

```bash
make nuke  # Deletes snapshots, volumes, and stack (with confirmation)
```

This removes **everything** including all snapshots and data. Use with caution.

#### Manual Cleanup Steps

If you prefer granular control:

1. **Stop the instance** (optional, to save costs while deciding):
   ```bash
   make stop
   ```

2. **List and selectively delete snapshots**:
   ```bash
   make list-snapshots
   # Then delete individual snapshots via AWS Console or CLI
   ```

3. **Delete orphaned volumes** (not attached to any instance):
   ```bash
   make delete-volumes
   ```

4. **Destroy the stack**:
   ```bash
   make destroy
   ```

5. **Delete the ECR repository** (optional, to clean up Docker images):
   - Navigate to [ECR Console](https://console.aws.amazon.com/ecr/)
   - Select the `comfyuisimplestack-*` repository
   - Delete it

## Security Considerations

This deployment is designed for **personal/development use** with a simplified security model. For production or multi-user scenarios, consider the [full reference implementation](https://github.com/aws-samples/cost-effective-aws-deployment-of-comfyui) which includes:

- **No Public Endpoints** — ComfyUI is not exposed to the internet (SSM port-forwarding only)
- **No Inbound Security Group Rules** — Instance has no open ports
- **No SSH Keys** — Access via AWS Systems Manager Session Manager only
- **Encrypted EBS Volumes** — Root volume encrypted at rest (data volume encryption can be added via `encrypted=True` in stack)
- **IAM Least Privilege** — EC2 role has minimal permissions (EBS management, ECR pull, SSM parameter read)
- **Spot Instance Lifecycle Management** — Snapshots created before termination to prevent data loss

### Important Security Notes

1. **No Authentication** — Unlike the reference implementation, this deployment has no Cognito/ALB authentication layer. Anyone with AWS console access and appropriate IAM permissions can access the instance via SSM.

2. **VPC Configuration** — By default, uses your existing VPC. If security is critical, set `VPC_ID` to a dedicated VPC in `.env`.

3. **HuggingFace Token** — Stored as an SSM SecureString parameter (encrypted at rest with AWS KMS). The EC2 instance retrieves it at boot time.

4. **AWS Console Access** — Protect your AWS account with MFA and follow [AWS security best practices](https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html).

### Enhancing Security (Optional)

For additional security, you can modify the stack to add:

- **EBS data volume encryption**: In `comfyui_simple_stack.py`, add `encrypted=True` to volume creation
- **VPC Endpoints for ECR/SSM**: Eliminate internet egress by adding VPC endpoints (see AWS docs)
- **CloudTrail logging**: Enable AWS CloudTrail to audit API calls
- **Restrict IAM access**: Use IAM policies to limit which users can access the ComfyUI instance via SSM

## Comparison to Reference Repository

| Feature | This Repository (Simplified) | [Reference Repository](https://github.com/aws-samples/cost-effective-aws-deployment-of-comfyui) |
|---------|------------------------------|----------------------------------|
| **Use Case** | Personal/single-user | Team/multi-user |
| **Access Method** | SSM port-forwarding only | ALB with public endpoint |
| **Authentication** | None (AWS IAM only) | Amazon Cognito (email/SAML) |
| **Security** | No open ports, IAM-based | WAF, Cognito, email domain restrictions, IP restrictions |
| **Infrastructure** | Direct Docker on EC2, existing VPC | ECS service, custom VPC, NAT Gateway/Instance options |
| **Cost** | Lower (simpler stack) | Higher (full managed services) |
| **Complexity** | Minimal (~600 lines CDK) | Full-featured (~2000+ lines CDK) |
| **Deployment Time** | ~8–10 minutes | ~10–15 minutes |
| **Scalability** | Single instance (0/1 ASG) | Single instance or multi-user (0/N ASG) |
| **Monitoring** | Basic (CloudWatch Logs) | Advanced (Slack notifications, CloudWatch dashboards) |

**When to use this simplified version:**
- Personal projects or experimentation
- Cost-sensitive workloads
- Don't need public internet access
- Comfortable with AWS Console and SSM access

**When to use the reference implementation:**
- Team/organizational deployments
- Need web-based authentication
- Require public endpoint with domain name
- Want advanced monitoring and alerting

## Frequently Asked Questions (FAQ)

### Does the Dockerfile pre-install models?

No. The Dockerfile includes only ComfyUI, ComfyUI-Manager, and system tools. You install models after deployment via:
- ComfyUI-Manager web UI (recommended)
- Manual download via `make ssh-container`
- Bulk upload scripts (see reference repository)

### How long does first boot take?

- **First deploy**: ~8–10 minutes (Docker image build + push to ECR + instance launch + volume creation)
- **Subsequent starts**: ~2–3 minutes (restore from snapshot + container start)

### Can I use On-Demand instances instead of Spot?

Yes. In `comfyui_simple_stack.py`, change the ASG `instances_distribution` configuration:
```python
on_demand_base_capacity=1,  # Use On-Demand instead of Spot
on_demand_percentage_above_base_capacity=100,
```

Then redeploy with `make deploy`.

### What happens if my Spot instance is interrupted?

1. AWS sends a 2-minute warning (EC2 Spot Instance Interruption Warning)
2. EventBridge triggers the snapshot Lambda immediately
3. Lambda initiates an EBS snapshot (continues asynchronously in background)
4. Instance is terminated by AWS after 2 minutes
5. ASG does **not** automatically launch a replacement (desired capacity is still 1, but ASG respects Spot interruptions)
6. Next time you run `make start`, the instance restarts with data restored from the latest snapshot

To restart immediately after interruption, you may need to manually run `make start` or set up an EventBridge rule to automatically increase ASG desired capacity.

### Can I change the ComfyUI port?

Yes. Edit three locations:
1. `docker/Dockerfile`: Change `--port 8181` and `EXPOSE 8181`
2. `Makefile`: Change `LOCAL_PORT` and `REMOTE_PORT` variables
3. User data in `comfyui_simple_stack.py`: Change `-p 8181:8181`

Then redeploy with `make deploy`.

### How do I add custom Python packages to the container?

Edit `docker/Dockerfile` and add your packages to the `pip install` commands. Example:
```dockerfile
RUN pip install --no-cache-dir \
    torch torchvision torchaudio \
    transformers accelerate xformers \
    --extra-index-url https://download.pytorch.org/whl/cu126
```

Then redeploy with `make deploy` (CDK will rebuild and push the new image).

### Can I contribute to this project?

Yes! This is a personal project, but contributions are welcome. Please open an issue or pull request on GitHub.

### Is this suitable for production use?

This deployment is designed for **personal/development use**. For production:
- Use the [reference repository](https://github.com/aws-samples/cost-effective-aws-deployment-of-comfyui) with full security features
- Add CloudWatch alarms and monitoring
- Implement backup/disaster recovery procedures
- Consider On-Demand instances for critical workloads
- Add VPC endpoints to eliminate internet egress
- Enable CloudTrail and AWS Config for compliance

## Related Resources

- **ComfyUI**: https://github.com/comfyanonymous/ComfyUI
- **ComfyUI-Manager**: https://github.com/ltdrdata/ComfyUI-Manager
- **Reference Repository**: https://github.com/aws-samples/cost-effective-aws-deployment-of-comfyui
- **AWS CDK Documentation**: https://docs.aws.amazon.com/cdk/
- **EC2 Spot Instances**: https://aws.amazon.com/ec2/spot/
- **AWS Systems Manager Session Manager**: https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html
- **ComfyUI Workflows**: https://openart.ai/workflows (community workflows)

## License

This project is licensed under the MIT-0 License. See the LICENSE file for details.

---

**Note:** This is a simplified, single-user deployment. For team/production use with authentication, WAF, and advanced features, see the [aws-samples/cost-effective-aws-deployment-of-comfyui](https://github.com/aws-samples/cost-effective-aws-deployment-of-comfyui) repository.

## Data Persistence

ComfyUI models, custom nodes, and outputs are stored on a dedicated EBS `gp3` volume mounted at `/data/comfyui`. This volume is:

1. **Created fresh** on first deploy with the following directory structure:
   ```
   /data/comfyui/
   ├── models/
   │   ├── checkpoints/      # Stable Diffusion models
   │   ├── clip/
   │   ├── clip_vision/
   │   ├── configs/
   │   ├── controlnet/       # ControlNet models
   │   ├── diffusers/
   │   ├── embeddings/       # Textual Inversion embeddings
   │   ├── gligen/
   │   ├── hypernetworks/
   │   ├── loras/            # LoRA models
   │   ├── mmdets/
   │   ├── onnx/
   │   ├── sams/
   │   ├── style_models/
   │   ├── ultralytics/
   │   ├── unet/
   │   ├── upscale_models/   # Upscaler models (ESRGAN, etc.)
   │   └── vae/              # VAE models
   ├── custom_nodes/         # ComfyUI extensions
   ├── output/               # Generated images
   └── input/                # Input images
   ```

2. **Snapshotted** automatically via three mechanisms:
   - **Periodic (DLM)** — Every 12 hours by default (configurable via `snapshot_interval_hours`)
   - **Lifecycle Hook** — On instance termination via ASG lifecycle hook Lambda
   - **Spot Interruption** — On EC2 Spot interruption warnings via EventBridge + Lambda
   - **Manual** — On-demand via `make snapshot`

3. **Restored from the latest snapshot** on every subsequent `make start`

The data volume survives `make stop`, `make destroy`, and Spot interruptions. Snapshots are tagged with the stack ID and retained according to the configured retention policy.

### Snapshot Strategy Details

| Trigger | Retention | Purpose |
|---------|-----------|---------|
| **DLM Periodic** | Last `snapshot_retain_count` snapshots | Safety net for long-running instances |
| **Lifecycle Hook** | Last `snapshot_retain_count` snapshots | Capture state on graceful termination |
| **Spot Interruption** | Last `snapshot_retain_count` snapshots | Preserve data during 2-minute warning |
| **Manual (`make snapshot`)** | Indefinite (manual cleanup required) | Before risky operations or experiments |

## Accessing ComfyUI

ComfyUI is exposed on port `8181` inside the instance but has **no public-facing port**. Access is via SSM port-forwarding:

```bash
make comfyui
# → http://localhost:8181
```

This opens a secure tunnel through AWS Systems Manager and forwards the ComfyUI web interface to your local machine. The connection remains active until you press `Ctrl+C`.

**Accessing the Container Shell:**

```bash
make ssh-container  # Interactive shell inside the ComfyUI Docker container
```

**Accessing the EC2 Host:**

```bash
make connect  # SSM shell on the EC2 instance (host OS)
```

All access requires the [SSM Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html) installed locally.

### Installing Models and Extensions

You can install models and custom nodes in several ways:

#### 1. Via ComfyUI-Manager (Recommended)

The deployed image includes [ComfyUI-Manager](https://github.com/ltdrdata/ComfyUI-Manager), which provides a web UI for installing models, custom nodes, and managing missing nodes in workflows.

1. Access ComfyUI at `http://localhost:8181` (via `make comfyui`)
2. Click the **Manager** button in the ComfyUI interface
3. Browse and install models/custom nodes directly from the UI

#### 2. Manual Installation (SSH Method)

For manual model installation or bulk operations:

```bash
# 1. SSH into the container
make ssh-container

# 2. Navigate to the appropriate directory (on EBS volume)
cd /home/user/opt/ComfyUI

# 3. Download models using wget/curl
# Example: Download an upscaler model
wget -c https://huggingface.co/ai-forever/Real-ESRGAN/resolve/main/RealESRGAN_x2.pth \
  -P ./models/upscale_models/

# Example: Clone a custom node
cd custom_nodes
git clone https://github.com/some/custom-node.git
cd custom-node
pip install -r requirements.txt
```

#### 3. Bulk Upload Script

For transferring many models from your local machine, you can use AWS SSM document commands to upload files. See the reference repository's [upload_models.sh script](https://github.com/aws-samples/cost-effective-aws-deployment-of-comfyui/blob/main/scripts/upload_models.sh) for examples.

**Note:** All models, custom nodes, and workflows stored in `/data/comfyui` are automatically persisted via EBS snapshots.

## Cost Management

### Running Costs

Stop the instance when not in use to avoid compute charges — the data volume will be snapshotted before termination:

```bash
make stop   # Terminates instance, creates snapshot
make start  # Restores from snapshot, ready in ~3 minutes
```

**What You Pay For:**

| Resource | When Stopped | When Running |
|----------|--------------|--------------|
| **EC2 Spot Instance** | $0 | ~$0.30–0.60/hr (varies by instance type and region) |
| **EBS gp3 Volume (500 GB)** | ~$40/month | ~$40/month |
| **EBS Snapshots** | ~$0.05/GB-month | ~$0.05/GB-month |
| **SSM Parameter Store (SecureString)** | Free tier | Free tier |
| **CloudWatch Logs** | Minimal (~$0.50/month) | Minimal (~$0.50/month) |
| **Lambda Invocations** | Free tier | Free tier |

### Cost Estimate (Flexible Workload)

For non-production personal use with Spot instances and periodic usage:

| Usage Pattern | Monthly Cost Estimate |
|---------------|----------------------|
| **2h/day Mon-Fri** | ~$50–60 |
| **8h/day Mon-Fri** | ~$70–90 |
| **12h/day Mon-Fri** | ~$90–120 |
| **24/7** | ~$250–300 |

**Assumptions:**
- Spot instance discount: ~70–80% vs on-demand
- Primary instance: `g6.2xlarge` (~$0.40/hr Spot in us-east-1)
- 500 GB EBS volume (~$40/month)
- ~20 GB of snapshots retained (~$1/month)
- Minimal CloudWatch Logs usage

*Prices vary by region and Spot availability. Check current [EC2 Spot Pricing](https://aws.amazon.com/ec2/spot/pricing/) and [EBS Pricing](https://aws.amazon.com/ebs/pricing/).*

### Cost Optimization Tips

1. **Stop when not in use** — Run `make stop` to avoid compute charges (data persists in snapshots)
2. **Use smaller instances** — For testing/development, consider `g6.xlarge` or `g5.xlarge`
3. **Reduce snapshot frequency** — Adjust `snapshot_interval_hours` to 24 or 48 hours for less critical workloads
4. **Clean up old snapshots** — Run `make list-snapshots` and manually delete old snapshots via AWS Console
5. **Monitor Spot interruptions** — If frequently interrupted, adjust `SPOT_MAX_PRICE` or switch to On-Demand (modify the ASG Spot settings in `comfyui_simple/comfyui_simple_stack.py`)

## Repository Structure

```
.
├── app.py                          # CDK app entry point — reads .env and .env.<AWS_PROFILE>
├── cdk.json                        # CDK configuration
├── requirements.txt                # Python CDK dependencies
├── package.json                    # Node.js CDK CLI dependencies
├── Makefile                        # All operational commands (deploy, start, stop, connect, etc.)
│
├── comfyui_simple/
│   └── comfyui_simple_stack.py     # CDK stack definition — EC2, ASG, EBS, Lambda, DLM, EventBridge
│
├── docker/
│   ├── Dockerfile                  # ComfyUI image (CUDA 12.6, Python 3.12, ComfyUI-Manager, nvitop, btop)
│   └── extra_model_paths.yaml      # ComfyUI model path overrides (maps to /data/comfyui/models)
│
└── lambda/
    └── snapshot_handler.py         # EBS snapshot Lambda (lifecycle hook + Spot interruption)
```

### Docker Image Details

The ComfyUI container includes:

- **Base Image:** `nvidia/cuda:12.6.3-runtime-ubuntu22.04`
- **Python:** 3.12.0 (via pyenv)
- **PyTorch:** Latest with CUDA 12.6 support
- **ComfyUI:** Latest from [comfyanonymous/ComfyUI](https://github.com/comfyanonymous/ComfyUI)
- **ComfyUI-Manager:** Pre-installed for easy extension/model management
- **Monitoring Tools:** `nvitop` (GPU monitoring), `btop` (system monitoring), `tmux` (terminal multiplexing)

To customize the image, edit `docker/Dockerfile` and redeploy with `make deploy`.

## Monitoring and Troubleshooting

### Viewing Logs

**ComfyUI container logs:**
```bash
make logs  # Live tail of ComfyUI logs
```

**EC2 bootstrap logs:**
```bash
make bootstrap-log  # View instance initialization script output
```

**Lambda snapshot logs:**
- Navigate to [CloudWatch Logs](https://console.aws.amazon.com/cloudwatch/home#logsV2:log-groups) in AWS Console
- Find log group: `/aws/lambda/ComfyUISimpleStack-SnapshotFunction*`

### Checking Status

```bash
make status  # Shows ASG, instance, and latest snapshot status
```

Example output:
```
=== ASG Status ===
DesiredCapacity: 1
Instances: [{Id: i-0abc123..., State: InService, Health: Healthy}]

=== EC2 Instance ===
Id              State    Type        AZ           LaunchTime
i-0abc123...    running  g6.2xlarge  us-east-1a   2026-03-04T14:30:00Z

=== Latest Snapshot ===
Id              State      StartTime                 Size  CreatedBy
snap-0xyz456... completed  2026-03-04T14:00:00Z      500   lifecycle-hook
```

### Common Issues

**Instance not starting:**
- Check GPU quota: [EC2 Service Quotas Console](https://console.aws.amazon.com/servicequotas/home/services/ec2/quotas/L-3819A6DF)
- Verify Spot capacity in your region (try adjusting `spot_max_price` or switching regions)
- Check `make bootstrap-log` for errors

**HuggingFace token errors:**
```bash
make get-hf-token  # Verify token is configured
make set-hf-token  # Reset token if needed
```

**Snapshot failures:**
- Check Lambda logs in CloudWatch
- Verify Lambda has EBS snapshot permissions (should be automatic)
- Run `make snapshot` to manually test snapshot creation

**ComfyUI not accessible:**
- Ensure instance is running: `make status`
- Check SSM Session Manager plugin is installed
- Verify port 8181 is not in use locally: `lsof -i :8181` (macOS/Linux) or `netstat -ano | findstr :8181` (Windows)

**Docker container issues:**
```bash
make connect  # SSH to EC2 host
sudo docker ps  # Check container status
sudo docker logs comfyui  # View container logs
```

### GPU Monitoring

Access the container and use the pre-installed monitoring tools:

```bash
make ssh-container
nvitop  # Interactive GPU monitoring (nvidia-smi alternative)
btop    # System resource monitoring (CPU, RAM, disk I/O)
```

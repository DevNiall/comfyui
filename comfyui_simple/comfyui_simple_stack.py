"""
ComfyUI Simple Stack — single-user GPU instance with automated EBS snapshots.

Architecture:
  - EC2 Spot instance (g6.2xlarge) in default VPC
  - ComfyUI runs in Docker directly (no ECS)
  - SSM Session Manager access (no inbound SG rules)
  - EBS data volume restored from latest snapshot on boot
  - Snapshot strategy: DLM periodic + lifecycle hook Lambda + manual Makefile target
  - HuggingFace token stored in SSM Parameter Store
"""

import hashlib
import os
from typing import List

from aws_cdk import (
    Stack,
    CfnOutput,
    CfnTag,
    Duration,
    RemovalPolicy,
    Tags,
    aws_autoscaling as autoscaling,
    aws_dlm as dlm,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_ssm as ssm,
)
from constructs import Construct


class ComfyUISimpleStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        # Instance config
        instance_type: str = "g6.2xlarge",
        fallback_instance_types: List[str] = None,
        spot_max_price: str = "1.20",
        # Data volume
        data_volume_size_gb: int = 500,
        # Snapshots
        snapshot_interval_hours: int = 12,
        snapshot_retain_count: int = 3,
        # SSM parameter path for HuggingFace token
        hf_token_param_path: str = "/comfyui/hf-token",
        # Existing VPC (set to None to create a new minimal VPC)
        vpc_id: str = "vpc-0a0078c96978cb8bb",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if fallback_instance_types is None:
            fallback_instance_types = ["g5.2xlarge", "g6.xlarge", "g5.xlarge"]

        # Unique suffix for resource naming
        unique_input = f"{self.account}-{self.region}-{self.stack_name}"
        suffix = hashlib.sha256(unique_input.encode("utf-8")).hexdigest()[:10].lower()
        stack_tag_value = f"comfyui-{suffix}"

        # ------------------------------------------------------------------ #
        # VPC — use existing or create a minimal single-AZ public-only
        # ------------------------------------------------------------------ #
        if vpc_id:
            vpc = ec2.Vpc.from_lookup(self, "VPC", vpc_id=vpc_id)
        else:
            vpc = ec2.Vpc(
                self, "VPC",
                max_azs=1,
                nat_gateways=0,
                subnet_configuration=[
                    ec2.SubnetConfiguration(
                        name="Public",
                        subnet_type=ec2.SubnetType.PUBLIC,
                        cidr_mask=24,
                    ),
                ],
            )

        # ------------------------------------------------------------------ #
        # Security Group — no inbound (SSM only), all outbound
        # ------------------------------------------------------------------ #
        sg = ec2.SecurityGroup(
            self, "InstanceSG",
            vpc=vpc,
            description="ComfyUI instance - no inbound, SSM access only",
            allow_all_outbound=True,
        )

        # ------------------------------------------------------------------ #
        # SSM Parameter — HuggingFace token reference (SecureString)
        # The parameter must already exist before deploying the stack.
        # Run `make set-hf-token` (or the ensure-hf-token Makefile target) first.
        # Using from_secure_string_parameter_attributes so CloudFormation does
        # not attempt to resolve the SecureString value at deploy time.
        # ------------------------------------------------------------------ #
        hf_token_param = ssm.StringParameter.from_secure_string_parameter_attributes(
            self, "HFTokenParam",
            parameter_name=hf_token_param_path,
        )

        # ------------------------------------------------------------------ #
        # Docker image — built and pushed to ECR during cdk deploy
        # ------------------------------------------------------------------ #
        docker_image_asset = ecr_assets.DockerImageAsset(
            self, "ComfyUIImage",
            directory="docker",
            platform=ecr_assets.Platform.LINUX_AMD64,
        )

        # ------------------------------------------------------------------ #
        # IAM Role for EC2 instance
        # ------------------------------------------------------------------ #
        ec2_role = iam.Role(
            self, "EC2Role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AmazonSSMManagedInstanceCore"
                ),
            ],
        )

        # EBS volume management (create from snapshot, attach, tag, detach)
        ec2_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ec2:CreateVolume",
                    "ec2:AttachVolume",
                    "ec2:DetachVolume",
                    "ec2:DeleteVolume",
                    "ec2:DescribeVolumes",
                    "ec2:DescribeSnapshots",
                    "ec2:CreateSnapshot",
                    "ec2:CreateTags",
                    "ec2:DescribeTags",
                ],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "aws:RequestedRegion": self.region,
                    }
                },
            )
        )

        # ECR pull
        docker_image_asset.repository.grant_pull(ec2_role)

        # SSM parameter read (supports both String and SecureString)
        hf_token_param.grant_read(ec2_role)
        ec2_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter{hf_token_param_path}"
                ],
            )
        )

        # ------------------------------------------------------------------ #
        # User Data — bootstrap script
        # ------------------------------------------------------------------ #
        user_data = ec2.UserData.for_linux()
        user_data.add_commands(
            "#!/bin/bash",
            "set -euo pipefail",
            "exec > >(tee /var/log/comfyui-bootstrap.log) 2>&1",
            "# Ensure AWS CLI and other tools are on PATH (cloud-init runs with minimal PATH)",
            "export PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH",
            'echo "=== ComfyUI Bootstrap Starting ==="',
            "",
            "# Install AWS CLI v2 if not present (ECS GPU AMI ships without it)",
            "if ! command -v aws &>/dev/null; then",
            '  echo "AWS CLI not found — installing AWS CLI v2..."',
            "  yum install -y unzip",
            "  curl -fsSL 'https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip' -o /tmp/awscliv2.zip",
            "  unzip -q /tmp/awscliv2.zip -d /tmp/awscliv2",
            "  /tmp/awscliv2/aws/install --bin-dir /usr/local/bin --install-dir /usr/local/aws-cli --update",
            "  rm -rf /tmp/awscliv2.zip /tmp/awscliv2",
            '  echo "AWS CLI v2 installed: $(aws --version)"',
            "fi",
            "",
            "# Stop ECS agent — we run Docker directly",
            "systemctl stop ecs 2>/dev/null || true",
            "systemctl disable ecs 2>/dev/null || true",
            "",
            "# Metadata",
            "TOKEN=$(curl -sX PUT 'http://169.254.169.254/latest/api/token' -H 'X-aws-ec2-metadata-token-ttl-seconds: 300')",
            "REGION=$(curl -s -H \"X-aws-ec2-metadata-token: $TOKEN\" http://169.254.169.254/latest/meta-data/placement/region)",
            "INSTANCE_ID=$(curl -s -H \"X-aws-ec2-metadata-token: $TOKEN\" http://169.254.169.254/latest/meta-data/instance-id)",
            "AZ=$(curl -s -H \"X-aws-ec2-metadata-token: $TOKEN\" http://169.254.169.254/latest/meta-data/placement/availability-zone)",
            "",
            f'STACK_TAG="{stack_tag_value}"',
            f'VOLUME_SIZE={data_volume_size_gb}',
            f'ECR_IMAGE="{docker_image_asset.image_uri}"',
            f'HF_TOKEN_PARAM="{hf_token_param_path}"',
            "",
            "# --- Find latest snapshot ---",
            'echo "Looking for latest snapshot with tag comfyui-stack=$STACK_TAG..."',
            "SNAPSHOT_ID=$(aws ec2 describe-snapshots \\",
            '  --filters "Name=tag:comfyui-stack,Values=$STACK_TAG" \\',
            '  --owner-ids self \\',
            "  --query 'sort_by(Snapshots,&StartTime)[-1].SnapshotId' \\",
            "  --output text \\",
            "  --region $REGION)",
            "",
            '# --- Create or restore data volume ---',
            'if [ "$SNAPSHOT_ID" != "None" ] && [ -n "$SNAPSHOT_ID" ]; then',
            '  echo "Restoring from snapshot: $SNAPSHOT_ID"',
            "  VOLUME_ID=$(aws ec2 create-volume \\",
            "    --availability-zone $AZ \\",
            "    --size $VOLUME_SIZE \\",
            "    --volume-type gp3 \\",
            "    --snapshot-id $SNAPSHOT_ID \\",
            "    --tag-specifications \"ResourceType=volume,Tags=[{Key=Name,Value=comfyui-data},{Key=comfyui-stack,Value=$STACK_TAG}]\" \\",
            "    --query 'VolumeId' --output text \\",
            "    --region $REGION)",
            "  FROM_SNAPSHOT=true",
            "else",
            '  echo "No snapshot found — creating fresh volume"',
            "  VOLUME_ID=$(aws ec2 create-volume \\",
            "    --availability-zone $AZ \\",
            "    --size $VOLUME_SIZE \\",
            "    --volume-type gp3 \\",
            "    --tag-specifications \"ResourceType=volume,Tags=[{Key=Name,Value=comfyui-data},{Key=comfyui-stack,Value=$STACK_TAG}]\" \\",
            "    --query 'VolumeId' --output text \\",
            "    --region $REGION)",
            "  FROM_SNAPSHOT=false",
            "fi",
            "",
            '# --- Wait for volume to be available ---',
            'echo "Waiting for volume $VOLUME_ID to become available..."',
            "aws ec2 wait volume-available --volume-ids $VOLUME_ID --region $REGION",
            "",
            "# --- Attach volume ---",
            'echo "Attaching volume $VOLUME_ID to $INSTANCE_ID as /dev/sdf..."',
            "aws ec2 attach-volume \\",
            "  --volume-id $VOLUME_ID \\",
            "  --instance-id $INSTANCE_ID \\",
            "  --device /dev/sdf \\",
            "  --region $REGION",
            "",
            "# Wait for attachment",
            "echo 'Waiting for volume attachment...'",
            "for i in $(seq 1 60); do",
            "  STATE=$(aws ec2 describe-volumes --volume-ids $VOLUME_ID --region $REGION \\",
            "    --query 'Volumes[0].Attachments[0].State' --output text 2>/dev/null || echo 'none')",
            '  if [ "$STATE" = "attached" ]; then',
            '    echo "Volume attached successfully"',
            "    break",
            "  fi",
            '  echo "Attachment state: $STATE — waiting..."',
            "  sleep 5",
            "done",
            "",
            "# Resolve actual device name (NVMe remapping)",
            "sleep 3",
            'DEVICE=""',
            'for dev in /dev/nvme1n1 /dev/xvdf /dev/sdf; do',
            "  if [ -b $dev ]; then",
            "    DEVICE=$dev",
            "    break",
            "  fi",
            "done",
            "",
            'if [ -z "$DEVICE" ]; then',
            '  echo "ERROR: Could not find attached device"',
            "  exit 1",
            "fi",
            'echo "Using device: $DEVICE"',
            "",
            "# --- Format if new volume ---",
            'if [ "$FROM_SNAPSHOT" = "false" ]; then',
            '  echo "Formatting new volume..."',
            "  mkfs.ext4 -m 0 $DEVICE",
            "fi",
            "",
            "# --- Mount ---",
            "mkdir -p /data/comfyui",
            "mount $DEVICE /data/comfyui",
            "",
            "# Create directory structure if fresh",
            'if [ "$FROM_SNAPSHOT" = "false" ]; then',
            "  mkdir -p /data/comfyui/{models/{checkpoints,clip,clip_vision,configs,controlnet,diffusers,embeddings,gligen,hypernetworks,loras,mmdets,onnx,sams,style_models,ultralytics,unet,upscale_models,vae,vae_approx},custom_nodes,output,input}",
            "  chown -R 1000:1000 /data/comfyui",
            "fi",
            "",
            "# --- Retrieve HuggingFace token ---",
            'HF_TOKEN=$(aws ssm get-parameter --name "$HF_TOKEN_PARAM" --with-decryption \\',
            "  --query 'Parameter.Value' --output text --region $REGION 2>/dev/null || echo 'not-set')",
            "",
            "# --- ECR login and pull ---",
            'echo "Pulling ComfyUI Docker image..."',
            "ECR_REGISTRY=$(echo $ECR_IMAGE | cut -d/ -f1)",
            "aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR_REGISTRY",
            "docker pull $ECR_IMAGE",
            "",
            "# --- Run ComfyUI container ---",
            'echo "Starting ComfyUI container..."',
            "docker run -d \\",
            "  --name comfyui \\",
            "  --gpus all \\",
            "  --restart unless-stopped \\",
            "  -v /data/comfyui/models:/home/user/opt/ComfyUI/models \\",
            "  -v /data/comfyui/custom_nodes:/home/user/opt/ComfyUI/custom_nodes \\",
            "  -v /data/comfyui/output:/home/user/opt/ComfyUI/output \\",
            "  -v /data/comfyui/input:/home/user/opt/ComfyUI/input \\",
            "  -e HF_TOKEN=$HF_TOKEN \\",
            "  -p 8181:8181 \\",
            "  $ECR_IMAGE",
            "",
            'echo "=== ComfyUI Bootstrap Complete ==="',
        )

        # ------------------------------------------------------------------ #
        # Launch Template
        # ECS GPU-optimized AMI: Docker + NVIDIA drivers pre-installed
        # ------------------------------------------------------------------ #
        launch_template = ec2.LaunchTemplate(
            self, "LaunchTemplate",
            machine_image=ec2.MachineImage.from_ssm_parameter(
                # The top-level path returns a JSON blob; /image_id returns the bare AMI ID
                "/aws/service/ecs/optimized-ami/amazon-linux-2/gpu/recommended/image_id",
                os=ec2.OperatingSystemType.LINUX,
            ),
            instance_type=ec2.InstanceType(instance_type),
            role=ec2_role,
            security_group=sg,
            user_data=user_data,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(
                        volume_size=80,
                        encrypted=True,
                        volume_type=ec2.EbsDeviceVolumeType.GP3,
                    ),
                )
            ],
            require_imdsv2=True,
        )

        # ------------------------------------------------------------------ #
        # Auto Scaling Group — 0/1 capacity with Spot pricing
        # ------------------------------------------------------------------ #
        all_instance_types = [instance_type] + [
            t for t in fallback_instance_types if t != instance_type
        ]
        lt_overrides = [
            autoscaling.LaunchTemplateOverrides(
                instance_type=ec2.InstanceType(t)
            )
            for t in all_instance_types
        ]

        asg = autoscaling.AutoScalingGroup(
            self, "ASG",
            vpc=vpc,
            mixed_instances_policy=autoscaling.MixedInstancesPolicy(
                instances_distribution=autoscaling.InstancesDistribution(
                    on_demand_base_capacity=0,
                    on_demand_percentage_above_base_capacity=0,
                    on_demand_allocation_strategy=autoscaling.OnDemandAllocationStrategy.LOWEST_PRICE,
                    spot_allocation_strategy=autoscaling.SpotAllocationStrategy.LOWEST_PRICE,
                    spot_instance_pools=1,
                    spot_max_price=spot_max_price,
                ),
                launch_template=launch_template,
                launch_template_overrides=lt_overrides,
            ),
            min_capacity=0,
            max_capacity=1,
            desired_capacity=1,
            auto_scaling_group_name=f"ComfyUI-ASG-{suffix}",
            new_instances_protected_from_scale_in=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        )

        Tags.of(asg).add("Name", "ComfyUI-Host")
        Tags.of(asg).add("comfyui-stack", stack_tag_value)

        # ------------------------------------------------------------------ #
        # Lifecycle Hook — triggers snapshot Lambda on termination
        # ------------------------------------------------------------------ #
        asg.add_lifecycle_hook(
            "TerminationHook",
            lifecycle_transition=autoscaling.LifecycleTransition.INSTANCE_TERMINATING,
            heartbeat_timeout=Duration.minutes(15),
            default_result=autoscaling.DefaultResult.CONTINUE,
        )

        # ------------------------------------------------------------------ #
        # Snapshot Lambda
        # ------------------------------------------------------------------ #
        snapshot_log_group = logs.LogGroup(
            self, "SnapshotLambdaLogs",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.TWO_WEEKS,
        )

        snapshot_lambda = lambda_.Function(
            self, "SnapshotFunction",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="snapshot_handler.handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(60),
            log_group=snapshot_log_group,
            environment={
                "STACK_ID": stack_tag_value,
                "SNAPSHOT_RETAIN_COUNT": str(snapshot_retain_count),
            },
        )

        # Lambda permissions
        snapshot_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ec2:DescribeVolumes",
                    "ec2:CreateSnapshot",
                    "ec2:CreateTags",
                    "ec2:DescribeSnapshots",
                    "ec2:DeleteSnapshot",
                    "ec2:DetachVolume",
                    "ec2:DeleteVolume",
                ],
                resources=["*"],
            )
        )
        snapshot_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["autoscaling:CompleteLifecycleAction"],
                resources=[asg.auto_scaling_group_arn],
            )
        )

        # ------------------------------------------------------------------ #
        # EventBridge — ASG lifecycle termination → Lambda
        # ------------------------------------------------------------------ #
        lifecycle_rule = events.Rule(
            self, "LifecycleRule",
            event_pattern=events.EventPattern(
                source=["aws.autoscaling"],
                detail_type=["EC2 Instance-terminate Lifecycle Action"],
                detail={
                    "AutoScalingGroupName": [asg.auto_scaling_group_name],
                },
            ),
        )
        lifecycle_rule.add_target(events_targets.LambdaFunction(snapshot_lambda))

        # ------------------------------------------------------------------ #
        # EventBridge — Spot Interruption Warning → Lambda
        # ------------------------------------------------------------------ #
        spot_rule = events.Rule(
            self, "SpotInterruptionRule",
            event_pattern=events.EventPattern(
                source=["aws.ec2"],
                detail_type=["EC2 Spot Instance Interruption Warning"],
            ),
        )
        spot_rule.add_target(events_targets.LambdaFunction(snapshot_lambda))

        # ------------------------------------------------------------------ #
        # DLM Policy — periodic snapshots as safety net
        # ------------------------------------------------------------------ #
        dlm_role = iam.Role(
            self, "DLMRole",
            assumed_by=iam.ServicePrincipal("dlm.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSDataLifecycleManagerServiceRole"
                ),
            ],
        )

        dlm.CfnLifecyclePolicy(
            self, "DLMPolicy",
            description=f"ComfyUI periodic snapshots every {snapshot_interval_hours}h",
            state="ENABLED",
            execution_role_arn=dlm_role.role_arn,
            policy_details=dlm.CfnLifecyclePolicy.PolicyDetailsProperty(
                resource_types=["VOLUME"],
                target_tags=[
                    CfnTag(key="comfyui-stack", value=stack_tag_value),
                    CfnTag(key="Name", value="comfyui-data"),
                ],
                schedules=[
                    dlm.CfnLifecyclePolicy.ScheduleProperty(
                        name="PeriodicSnapshot",
                        create_rule=dlm.CfnLifecyclePolicy.CreateRuleProperty(
                            interval=snapshot_interval_hours,
                            interval_unit="HOURS",
                            times=["00:00"],
                        ),
                        retain_rule=dlm.CfnLifecyclePolicy.RetainRuleProperty(
                            count=snapshot_retain_count,
                        ),
                        tags_to_add=[
                            CfnTag(key="CreatedBy", value="dlm-policy"),
                            CfnTag(key="comfyui-stack", value=stack_tag_value),
                        ],
                        copy_tags=True,
                    )
                ],
            ),
        )

        # ------------------------------------------------------------------ #
        # Outputs
        # ------------------------------------------------------------------ #
        CfnOutput(self, "ASGName", value=asg.auto_scaling_group_name)

        CfnOutput(
            self, "SSMConnectCommand",
            value=(
                f'aws ssm start-session --target '
                f'"$(aws ec2 describe-instances '
                f'--filters "Name=tag:Name,Values=ComfyUI-Host" '
                f'"Name=instance-state-name,Values=running" '
                f'--query \'Reservations[].Instances[].InstanceId\' '
                f'--output text --region {self.region})" '
                f'--region {self.region}'
            ),
        )

        CfnOutput(
            self, "PortForwardCommand",
            value=(
                f'aws ssm start-session --target '
                f'"$(aws ec2 describe-instances '
                f'--filters "Name=tag:Name,Values=ComfyUI-Host" '
                f'"Name=instance-state-name,Values=running" '
                f'--query \'Reservations[].Instances[].InstanceId\' '
                f'--output text --region {self.region})" '
                f'--document-name AWS-StartPortForwardingSession '
                f'--parameters \'{{"portNumber":["8181"],"localPortNumber":["8181"]}}\' '
                f'--region {self.region}'
            ),
        )

        CfnOutput(self, "HFTokenParamPath", value=hf_token_param_path)
        CfnOutput(self, "ECRImageUri", value=docker_image_asset.image_uri)
        CfnOutput(self, "StackTag", value=stack_tag_value)

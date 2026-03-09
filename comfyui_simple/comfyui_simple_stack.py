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
        spot_max_price: str = None,
        # Data volume
        data_volume_size_gb: int = 500,
        # Snapshots
        snapshot_interval_hours: int = 12,
        snapshot_retain_count: int = 3,
        # SSM parameter path for HuggingFace token
        hf_token_param_path: str = "/comfyui/hf-token",
        # VPC ID (required — stack will raise if not provided)
        vpc_id: str = "vpc-0a0078c96978cb8bb",
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        if fallback_instance_types is None:
            fallback_instance_types = ["g4dn.2xlarge", "g6.xlarge", "g5.2xlarge", "g5.xlarge", "g4dn.xlarge"]

        # Unique suffix for resource naming
        unique_input = f"{self.account}-{self.region}-{self.stack_name}"
        suffix = hashlib.sha256(unique_input.encode("utf-8")).hexdigest()[:10].lower()
        stack_tag_value = f"comfyui-{suffix}"

        # ------------------------------------------------------------------ #
        # VPC — must be provided; VPC creation is not supported
        # ------------------------------------------------------------------ #
        if not vpc_id:
            raise ValueError(
                "vpc_id must be specified. "
                "Automatic VPC creation is not supported by this stack."
            )
        vpc = ec2.Vpc.from_lookup(self, "VPC", vpc_id=vpc_id)

        # ------------------------------------------------------------------ #
        # Security Group — no inbound (SSM only), all outbound
        # ------------------------------------------------------------------ #
        sg = ec2.SecurityGroup(
            self, "InstanceSG",
            vpc=vpc,
            security_group_name="comfyui-instance-sg",
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
            role_name=f"comfyui-ec2-role-{self.region}",
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
        # User Data — loaded from scripts/userdata.sh
        # ------------------------------------------------------------------ #
        _userdata_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "userdata.sh")
        with open(_userdata_path) as _f:
            _script = (
                _f.read()
                .replace("@@STACK_TAG@@", stack_tag_value)
                .replace("@@VOLUME_SIZE@@", str(data_volume_size_gb))
                .replace("@@ECR_IMAGE@@", docker_image_asset.image_uri)
                .replace("@@HF_TOKEN_PARAM@@", hf_token_param_path)
            )
        user_data = ec2.UserData.custom(_script)

        # ------------------------------------------------------------------ #
        # Launch Template
        # Deep Learning Base OSS Nvidia Driver AMI (AL2023):
        # ships with driver 570.x+ which supports CUDA 13.1.
        # Includes Docker + nvidia-container-toolkit pre-installed.
        # ------------------------------------------------------------------ #
        launch_template = ec2.LaunchTemplate(
            self, "LaunchTemplate",
            launch_template_name=f"comfyui-{self.stack_name}",
            # no key_pair — SSM Session Manager is the sole access path
            machine_image=ec2.MachineImage.lookup(
                name="Deep Learning Base OSS Nvidia Driver GPU AMI (Amazon Linux 2023) *",
                owners=["amazon"],
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
        # Spot pricing — no price cap so the ASG pays up to on-demand price
        # rather than failing to launch when spot prices rise above the cap.
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
                    spot_allocation_strategy=autoscaling.SpotAllocationStrategy.PRICE_CAPACITY_OPTIMIZED,
                    **({"spot_max_price": spot_max_price} if spot_max_price else {}),
                ),
                launch_template=launch_template,
                launch_template_overrides=lt_overrides,
            ),
            min_capacity=0,
            max_capacity=1,
            desired_capacity=1,
            auto_scaling_group_name=f"ComfyUI-ASG-{suffix}",
            new_instances_protected_from_scale_in=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )

        Tags.of(asg).add("Name", "ComfyUI-Host")
        Tags.of(asg).add("comfyui-stack", stack_tag_value)

        # ------------------------------------------------------------------ #
        # Lifecycle Hook — triggers snapshot Lambda on termination
        # ------------------------------------------------------------------ #
        asg.add_lifecycle_hook(
            "TerminationHook",
            lifecycle_transition=autoscaling.LifecycleTransition.INSTANCE_TERMINATING,
            default_result=autoscaling.DefaultResult.CONTINUE,
        )

        # ------------------------------------------------------------------ #
        # Snapshot Lambda
        # ------------------------------------------------------------------ #
        snapshot_log_group = logs.LogGroup(
            self, "SnapshotLambdaLogs",
            log_group_name="/aws/lambda/comfyui-snapshot-handler",
            removal_policy=RemovalPolicy.DESTROY,
            retention=logs.RetentionDays.TWO_WEEKS,
        )

        snapshot_lambda = lambda_.Function(
            self, "SnapshotFunction",
            function_name="comfyui-snapshot-handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="snapshot_handler.handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(900),
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
            rule_name="comfyui-asg-termination",
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
            rule_name="comfyui-spot-interruption",
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
            role_name=f"comfyui-dlm-role-{self.region}",
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

#!/usr/bin/env python3
import os
import aws_cdk as cdk
from comfyui_simple.comfyui_simple_stack import ComfyUISimpleStack

app = cdk.App()

ComfyUISimpleStack(
    app,
    "ComfyUISimpleStack",
    description="ComfyUI single-user GPU deployment with Spot pricing",
    env=cdk.Environment(
        account=os.environ["CDK_DEFAULT_ACCOUNT"],
        region=os.environ["CDK_DEFAULT_REGION"],
    ),
    tags={
        "Project": "comfyui-simple",
    },
    # ---- Customizable parameters ----
    # instance_type="g6.2xlarge",
    # fallback_instance_types=["g5.2xlarge", "g6.xlarge", "g5.xlarge"],
    # spot_max_price="1.20",
    # data_volume_size_gb=500,
    # snapshot_interval_hours=12,
    # snapshot_retain_count=3,
    # hf_token_param_path="/comfyui/hf-token",
)

app.synth()

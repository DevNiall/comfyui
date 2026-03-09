#!/usr/bin/env python3
import os
from pathlib import Path

import aws_cdk as cdk
from dotenv import dotenv_values

from comfyui_simple.comfyui_simple_stack import ComfyUISimpleStack


def _load_config() -> dict:
    """Load `.env` and `.env.<AWS_PROFILE>` with explicit precedence.

    Precedence: shell env > profile env file > base env file.
    """
    repo_root = Path(__file__).resolve().parent
    base_config = dotenv_values(repo_root / ".env")

    profile = os.environ.get("AWS_PROFILE") or base_config.get("AWS_PROFILE") or "default"
    profile_config = dotenv_values(repo_root / f".env.{profile}")

    merged = {}
    merged.update(base_config)
    merged.update(profile_config)
    merged.update(os.environ)
    return merged


def _required(config: dict, key: str) -> str:
    value = config.get(key)
    if value in (None, ""):
        raise ValueError(f"Missing required configuration value: {key}")
    return str(value)


def _int_config(config: dict, key: str, default: int) -> int:
    value = config.get(key)
    if value in (None, ""):
        return default
    return int(value)


def _list_config(config: dict, key: str) -> list[str] | None:
    value = config.get(key)
    if value in (None, ""):
        return None
    values = [item.strip() for item in str(value).split(",") if item.strip()]
    return values or None


config = _load_config()
app = cdk.App()

ComfyUISimpleStack(
    app,
    "ComfyUISimpleStack",
    description="ComfyUI single-user GPU deployment with Spot pricing",
    env=cdk.Environment(
        account=_required(config, "CDK_DEFAULT_ACCOUNT"),
        region=_required(config, "CDK_DEFAULT_REGION"),
    ),
    tags={
        "Project": "comfyui-simple",
    },
    instance_type=str(config.get("INSTANCE_TYPE", "g6.2xlarge")),
    fallback_instance_types=_list_config(config, "FALLBACK_INSTANCE_TYPES"),
    spot_max_price=config.get("SPOT_MAX_PRICE") or None,
    data_volume_size_gb=_int_config(config, "DATA_VOLUME_SIZE_GB", 500),
    snapshot_interval_hours=_int_config(config, "SNAPSHOT_INTERVAL_HOURS", 12),
    snapshot_retain_count=_int_config(config, "SNAPSHOT_RETAIN_COUNT", 3),
    hf_token_param_path=str(config.get("HF_TOKEN_PARAM", "/comfyui/hf-token")),
    vpc_id=str(config.get("VPC_ID", "vpc-0a0078c96978cb8bb")),
)

app.synth()

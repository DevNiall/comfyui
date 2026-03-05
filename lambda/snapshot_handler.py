"""
Lambda handler for ComfyUI EBS snapshot management.

Triggered by:
  1. ASG Lifecycle Hook (EC2_INSTANCE_TERMINATING) via EventBridge
  2. EC2 Spot Instance Interruption Warning via EventBridge

Strategy: Initiate snapshot immediately, then delete the source volume once it
detaches (after instance termination). The snapshot continues asynchronously
even after the volume is deleted.
"""

import boto3
import os
import json
import time
from datetime import datetime, timezone

ec2 = boto3.client("ec2")
autoscaling = boto3.client("autoscaling")

STACK_TAG_KEY = "comfyui-stack"
VOLUME_TAG_KEY = "Name"
VOLUME_TAG_VALUE = "comfyui-data"
SNAPSHOT_RETAIN_COUNT = int(os.environ.get("SNAPSHOT_RETAIN_COUNT", "5"))
STACK_ID = os.environ.get("STACK_ID", "")


def find_data_volume(instance_id: str) -> str | None:
    """Find the comfyui-data volume attached to this instance."""
    resp = ec2.describe_volumes(
        Filters=[
            {"Name": "attachment.instance-id", "Values": [instance_id]},
            {"Name": f"tag:{VOLUME_TAG_KEY}", "Values": [VOLUME_TAG_VALUE]},
        ]
    )
    volumes = resp.get("Volumes", [])
    if volumes:
        return volumes[0]["VolumeId"]
    return None


def create_snapshot(volume_id: str, source: str) -> str:
    """Initiate an EBS snapshot (returns immediately, snapshot runs async)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    resp = ec2.create_snapshot(
        VolumeId=volume_id,
        Description=f"ComfyUI data snapshot ({source}) - {now}",
        TagSpecifications=[
            {
                "ResourceType": "snapshot",
                "Tags": [
                    {"Key": VOLUME_TAG_KEY, "Value": VOLUME_TAG_VALUE},
                    {"Key": STACK_TAG_KEY, "Value": STACK_ID},
                    {"Key": "CreatedBy", "Value": source},
                    {"Key": "CreatedAt", "Value": now},
                ],
            }
        ],
    )
    snapshot_id = resp["SnapshotId"]
    print(f"Snapshot initiated: {snapshot_id} from volume {volume_id} (source={source})")
    return snapshot_id


def delete_volume(volume_id: str, max_wait_seconds: int = 900):
    """Wait for volume to become available (detached), then delete it."""
    print(f"Waiting for volume {volume_id} to detach before deleting...")
    for i in range(0, max_wait_seconds, 10):
        try:
            resp = ec2.describe_volumes(VolumeIds=[volume_id])
            state = resp["Volumes"][0]["State"]
            if state == "available":
                ec2.delete_volume(VolumeId=volume_id)
                print(f"Deleted volume {volume_id}")
                return
            print(f"Volume {volume_id} state: {state} — waiting...")
        except ec2.exceptions.ClientError as e:
            if "InvalidVolume.NotFound" in str(e):
                print(f"Volume {volume_id} already gone")
                return
            raise
        time.sleep(10)
    print(f"Timed out waiting for volume {volume_id} to detach — will be cleaned up on next boot")


def prune_old_snapshots():
    """Keep only the N most recent lifecycle/spot snapshots. DLM snapshots are managed by DLM."""
    resp = ec2.describe_snapshots(
        Filters=[
            {"Name": f"tag:{STACK_TAG_KEY}", "Values": [STACK_ID]},
            {"Name": "tag:CreatedBy", "Values": ["lifecycle-hook", "spot-interruption"]},
        ],
        OwnerIds=["self"],
    )
    snapshots = sorted(resp["Snapshots"], key=lambda s: s["StartTime"], reverse=True)

    for snap in snapshots[SNAPSHOT_RETAIN_COUNT:]:
        snap_id = snap["SnapshotId"]
        state = snap["State"]
        if state == "completed":
            try:
                ec2.delete_snapshot(SnapshotId=snap_id)
                print(f"Pruned old snapshot: {snap_id}")
            except Exception as e:
                print(f"Failed to prune snapshot {snap_id}: {e}")
        else:
            print(f"Skipping prune of {snap_id} (state={state})")


def complete_lifecycle_action(event_detail: dict, result: str = "CONTINUE"):
    """Signal ASG lifecycle hook completion."""
    try:
        autoscaling.complete_lifecycle_action(
            LifecycleHookName=event_detail["LifecycleHookName"],
            AutoScalingGroupName=event_detail["AutoScalingGroupName"],
            LifecycleActionToken=event_detail["LifecycleActionToken"],
            LifecycleActionResult=result,
        )
        print(f"Lifecycle action completed: {result}")
    except Exception as e:
        print(f"Failed to complete lifecycle action: {e}")


def handler(event, context):
    print(f"Event: {json.dumps(event)}")

    detail = event.get("detail", {})
    detail_type = event.get("detail-type", "")

    # --- ASG Lifecycle Hook (termination) ---
    if detail_type == "EC2 Instance-terminate Lifecycle Action":
        instance_id = detail.get("EC2InstanceId", "")
        print(f"Lifecycle termination event for instance: {instance_id}")

        volume_id = find_data_volume(instance_id)
        if volume_id:
            create_snapshot(volume_id, "lifecycle-hook")
            prune_old_snapshots()
            # Complete lifecycle action first — this lets the instance terminate
            # and the volume to auto-detach
            complete_lifecycle_action(detail)
            # Now wait for detach and delete the volume
            delete_volume(volume_id)
        else:
            print(f"No comfyui-data volume found for instance {instance_id}")
            complete_lifecycle_action(detail)

        return {"statusCode": 200, "body": "Lifecycle snapshot initiated"}

    # --- Spot Interruption Warning ---
    elif detail_type == "EC2 Spot Instance Interruption Warning":
        instance_id = detail.get("instance-id", "")
        print(f"Spot interruption warning for instance: {instance_id}")

        volume_id = find_data_volume(instance_id)
        if volume_id:
            create_snapshot(volume_id, "spot-interruption")
            # Best-effort volume deletion — instance terminates in ~2 minutes
            delete_volume(volume_id, max_wait_seconds=900)
        else:
            print(f"No comfyui-data volume found for instance {instance_id}")

        return {"statusCode": 200, "body": "Spot interruption snapshot initiated"}

    else:
        print(f"Unknown event type: {detail_type}")
        return {"statusCode": 400, "body": f"Unknown event type: {detail_type}"}

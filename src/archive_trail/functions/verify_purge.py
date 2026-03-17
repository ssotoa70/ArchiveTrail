"""ArchiveTrail DataEngine Function: verify_purge

Optionally deletes local copies of files that have been verified
in AWS S3. Every deletion is preceded by:
  1. Confirming the AWS copy exists (HEAD request)
  2. Emitting LOCAL_DELETE_REQUESTED event
  3. Performing the deletion
  4. Emitting LOCAL_DELETED event
  5. Tagging the AWS copy with purge metadata

If auto_delete_local is false in config, this function is a no-op.
"""

import logging
from datetime import datetime, timezone

import boto3

import vastdb

from archive_trail.config import ArchiveTrailConfig
from archive_trail.events import EventEmitter, EventType
from archive_trail.helpers import s3_key_from_path
from archive_trail.registry import AssetRegistry

logger = logging.getLogger("archive_trail.functions.verify_purge")

FUNCTION_NAME = "verify_and_purge"


def handler(event: dict, context: object) -> dict:
    """DataEngine entry point.

    Args:
        event: Output from offload function containing 'offloaded' list.
        context: DataEngine execution context.

    Returns:
        Dict with 'purged' and 'skipped' lists.
    """
    pipeline_run_id = event.get(
        "pipeline_run_id", getattr(context, "run_id", "manual")
    )

    # Initialize
    session = vastdb.Session()
    config = ArchiveTrailConfig(session)
    registry = AssetRegistry(session)
    emitter = EventEmitter(session)

    config_snapshot = config.to_snapshot()

    if not config.auto_delete_local:
        logger.info("auto_delete_local is false, skipping purge phase")
        return {
            "purged": [],
            "skipped": "auto_delete_local=false",
            "pipeline_run_id": pipeline_run_id,
        }

    s3_vast = boto3.client("s3", endpoint_url=config.vast_s3_endpoint)
    s3_aws = boto3.client("s3", region_name=config.target_aws_region)

    # Get all assets in BOTH state (have been copied but not yet purged)
    pending = registry.get_pending_purge()
    logger.info("Found %d assets pending purge", len(pending))

    purged = []
    failed = []

    for asset in pending:
        handle = asset.element_handle
        reg_id = asset.registration_id
        src_path = asset.original_path
        aws_bucket = asset.current_aws_bucket
        aws_key = asset.current_aws_key

        try:
            # Step 1: Verify AWS copy exists
            s3_aws.head_object(Bucket=aws_bucket, Key=aws_key)

            # Step 2: Emit LOCAL_DELETE_REQUESTED
            emitter.emit_quick(
                handle, reg_id, EventType.LOCAL_DELETE_REQUESTED,
                pipeline_run_id=pipeline_run_id,
                function_name=FUNCTION_NAME,
                config_snapshot=config_snapshot,
                source_path=src_path,
                aws_bucket=aws_bucket,
                aws_key=aws_key,
            )

            # Step 3: Delete local copy
            local_bucket = asset.original_bucket
            local_key = s3_key_from_path(src_path, f"/{local_bucket}")
            s3_vast.delete_object(Bucket=local_bucket, Key=local_key)

            # Step 4: Emit LOCAL_DELETED
            emitter.emit_quick(
                handle, reg_id, EventType.LOCAL_DELETED,
                pipeline_run_id=pipeline_run_id,
                function_name=FUNCTION_NAME,
                config_snapshot=config_snapshot,
                source_path=src_path,
                aws_bucket=aws_bucket,
                aws_key=aws_key,
                success=True,
            )

            # Step 5: Update registry: BOTH -> LOCAL_DELETED
            registry.update_state(handle, "LOCAL_DELETED")

            # Step 6: Tag AWS copy with purge metadata
            _tag_aws_purged(s3_aws, aws_bucket, aws_key)

            # Step 7: Update local file tag (if it still exists in Catalog)
            _tag_local_purged(s3_vast, local_bucket, local_key)

            purged.append({
                "handle": handle,
                "original_path": src_path,
                "aws_location": f"s3://{aws_bucket}/{aws_key}",
            })

        except s3_aws.exceptions.ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code in ("404", "NoSuchKey"):
                error_msg = (
                    f"AWS copy not found at s3://{aws_bucket}/{aws_key}, "
                    f"aborting local delete for safety"
                )
            else:
                error_msg = str(e)

            logger.error("Purge failed for %s: %s", src_path, error_msg)
            emitter.emit_quick(
                handle, reg_id, EventType.LOCAL_DELETE_FAILED,
                pipeline_run_id=pipeline_run_id,
                function_name=FUNCTION_NAME,
                config_snapshot=config_snapshot,
                source_path=src_path,
                aws_bucket=aws_bucket,
                aws_key=aws_key,
                success=False,
                error_message=error_msg,
            )
            failed.append({"handle": handle, "reason": error_msg})

        except Exception as e:
            logger.error("Purge failed for %s: %s", src_path, e)
            emitter.emit_quick(
                handle, reg_id, EventType.LOCAL_DELETE_FAILED,
                pipeline_run_id=pipeline_run_id,
                function_name=FUNCTION_NAME,
                config_snapshot=config_snapshot,
                source_path=src_path,
                aws_bucket=aws_bucket,
                aws_key=aws_key,
                success=False,
                error_message=str(e),
            )
            failed.append({"handle": handle, "reason": str(e)})

    logger.info(
        "Purge complete: %d purged, %d failed", len(purged), len(failed)
    )

    return {
        "purged": purged,
        "failed": failed,
        "pipeline_run_id": pipeline_run_id,
    }


def _tag_aws_purged(
    s3_client: boto3.client, bucket: str, key: str
) -> None:
    """Tag the AWS S3 object to indicate the local source has been purged."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        s3_client.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={
                "TagSet": [
                    {"Key": "vast-source-purged", "Value": "true"},
                    {"Key": "vast-purge-timestamp", "Value": now_iso},
                ]
            },
        )
    except Exception as e:
        logger.warning("Failed to tag AWS object %s/%s: %s", bucket, key, e)


def _tag_local_purged(
    s3_client: boto3.client, bucket: str, key: str
) -> None:
    """Attempt to update the local S3 tag to PURGED (best-effort)."""
    try:
        s3_client.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={
                "TagSet": [
                    {"Key": "offload_status", "Value": "PURGED"},
                ]
            },
        )
    except Exception:
        # File may already be deleted — this is expected and safe to ignore
        pass

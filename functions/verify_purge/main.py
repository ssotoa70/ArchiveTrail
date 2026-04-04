"""VAST DataEngine handler: verify_purge

Optionally deletes local copies of files that have been verified
in AWS S3. Every deletion is preceded by:
  1. Confirming the AWS copy exists (HEAD request)
  2. Emitting LOCAL_DELETE_REQUESTED event
  3. Performing the deletion
  4. Emitting LOCAL_DELETED event
  5. Tagging the AWS copy with purge metadata

If auto_delete_local is false in config, this function is a no-op.
"""

import os

__version__ = "0.1.0"

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:
    boto3 = None

# Global state — initialized once in init(), reused per event
vastdb_session = None
s3_vast = None
s3_aws = None
_tables_verified = False


def init(ctx):
    """One-time initialization when the container starts."""
    global vastdb_session, s3_vast, s3_aws, _tables_verified

    ctx.logger.info("INITIALIZING ARCHIVE-TRAIL VERIFY_PURGE %s", __version__)

    # --- VastDB Session ---
    from archive_trail.db import create_session, ensure_tables
    vastdb_session = create_session(logger=ctx.logger)

    if not _tables_verified:
        ensure_tables(vastdb_session, logger=ctx.logger)
        _tables_verified = True

    # --- VAST S3 Client ---
    s3_endpoint = os.environ.get("S3_ENDPOINT", "")
    s3_access_key = os.environ.get("S3_ACCESS_KEY", "")
    s3_secret_key = os.environ.get("S3_SECRET_KEY", "")

    if boto3 is not None and all([s3_endpoint, s3_access_key, s3_secret_key]):
        s3_config = BotoConfig(
            max_pool_connections=25,
            retries={"max_attempts": 3, "mode": "adaptive"},
            connect_timeout=5,
            read_timeout=10,
        )
        s3_vast = boto3.client(
            "s3",
            endpoint_url=s3_endpoint,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
            config=s3_config,
        )
        ctx.logger.info("VAST S3 client created")

    # --- AWS S3 Client ---
    aws_access_key = os.environ.get("AWS_ACCESS_KEY_ID", "")
    aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
    aws_region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")

    if boto3 is not None and all([aws_access_key, aws_secret_key]):
        s3_aws = boto3.client(
            "s3",
            region_name=aws_region,
            aws_access_key_id=aws_access_key,
            aws_secret_access_key=aws_secret_key,
        )
        ctx.logger.info("AWS S3 client created: region=%s", aws_region)

    ctx.logger.info("ARCHIVE-TRAIL VERIFY_PURGE initialized successfully")


def handler(ctx, event):
    """Per-event handler for the verify_purge function.

    Args:
        ctx: VAST function context (has .logger)
        event: CloudEvent containing output from offload function.

    Returns:
        Dict with 'purged' and 'failed' lists.
    """
    from datetime import datetime, timezone
    from archive_trail.config import ArchiveTrailConfig
    from archive_trail.events import EventEmitter, EventType
    from archive_trail.helpers import s3_key_from_path
    from archive_trail.registry import AssetRegistry

    # Extract pipeline_run_id from event
    event_data = event.get_data() if hasattr(event, "get_data") else event
    if isinstance(event_data, dict):
        pipeline_run_id = event_data.get("pipeline_run_id", "unknown")
    else:
        pipeline_run_id = "unknown"

    config = ArchiveTrailConfig(vastdb_session, logger=ctx.logger)
    registry = AssetRegistry(vastdb_session, logger=ctx.logger)
    emitter = EventEmitter(vastdb_session, logger=ctx.logger)
    config_snapshot = config.to_snapshot()
    FUNCTION_NAME = "verify_and_purge"

    if not config.auto_delete_local:
        ctx.logger.info("auto_delete_local is false, skipping purge phase")
        return {
            "purged": [],
            "skipped": "auto_delete_local=false",
            "pipeline_run_id": pipeline_run_id,
        }

    # Get all assets in BOTH state (have been copied but not yet purged)
    pending = registry.get_pending_purge()
    ctx.logger.info("Found %d assets pending purge", len(pending))

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
            _tag_aws_purged(ctx, s3_aws, aws_bucket, aws_key)

            # Step 7: Update local file tag (best-effort, file may be gone)
            _tag_local_purged(s3_vast, local_bucket, local_key)

            purged.append({
                "handle": handle,
                "original_path": src_path,
                "aws_location": f"s3://{aws_bucket}/{aws_key}",
            })

        except Exception as e:
            error_msg = str(e)
            # Check for AWS 404 (copy not found — abort delete for safety)
            if hasattr(e, "response"):
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code in ("404", "NoSuchKey"):
                    error_msg = (
                        f"AWS copy not found at s3://{aws_bucket}/{aws_key}, "
                        f"aborting local delete for safety"
                    )

            ctx.logger.error("Purge failed for %s: %s", src_path, error_msg)
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

    ctx.logger.info("Purge complete: %d purged, %d failed", len(purged), len(failed))

    return {
        "purged": purged,
        "failed": failed,
        "pipeline_run_id": pipeline_run_id,
    }


def _tag_aws_purged(ctx, s3_client, bucket, key):
    """Tag the AWS S3 object to indicate the local source has been purged."""
    from datetime import datetime, timezone

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
        ctx.logger.warning("Failed to tag AWS object %s/%s: %s", bucket, key, e)


def _tag_local_purged(s3_client, bucket, key):
    """Attempt to update the local S3 tag to PURGED (best-effort)."""
    try:
        s3_client.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={
                "TagSet": [{"Key": "offload_status", "Value": "PURGED"}]
            },
        )
    except Exception:
        # File may already be deleted — expected and safe to ignore
        pass

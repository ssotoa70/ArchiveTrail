"""VAST DataEngine handler: offload

Copies discovered cold files from VAST S3 to AWS S3 with:
- MD5 checksum verification (source vs destination)
- Genealogy metadata embedded in AWS S3 object metadata
- S3 tagging on local file for Catalog visibility
- Full lifecycle event chain for every step

Receives candidate list from the discover function.
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

    ctx.logger.info("INITIALIZING ARCHIVE-TRAIL OFFLOAD %s", __version__)

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
            read_timeout=30,
        )
        s3_vast = boto3.client(
            "s3",
            endpoint_url=s3_endpoint,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
            config=s3_config,
        )
        ctx.logger.info("VAST S3 client created: %s", s3_endpoint)

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
    else:
        ctx.logger.warning("AWS S3 client not created (missing credentials)")

    ctx.logger.info("ARCHIVE-TRAIL OFFLOAD initialized successfully")


def handler(ctx, event):
    """Per-event handler for the offload function.

    Args:
        ctx: VAST function context (has .logger)
        event: CloudEvent containing output from discover function.

    Returns:
        Dict with 'offloaded' list for the verify_purge function.
    """
    from datetime import datetime, timezone
    from archive_trail.config import ArchiveTrailConfig
    from archive_trail.events import EventEmitter, EventType
    from archive_trail.helpers import aws_key_from_path, compute_md5, s3_key_from_path
    from archive_trail.registry import AssetRegistry

    # Extract data from the event (output of discover function)
    event_data = event.get_data() if hasattr(event, "get_data") else event
    if isinstance(event_data, dict):
        candidates = event_data.get("candidates", [])
        pipeline_run_id = event_data.get("pipeline_run_id", "unknown")
    else:
        ctx.logger.warning("Unexpected event format, no candidates found")
        return {"offloaded": [], "failed": [], "pipeline_run_id": "unknown"}

    if not candidates:
        ctx.logger.info("No candidates to offload")
        return {"offloaded": [], "failed": [], "pipeline_run_id": pipeline_run_id}

    config = ArchiveTrailConfig(vastdb_session, logger=ctx.logger)
    registry = AssetRegistry(vastdb_session, logger=ctx.logger)
    emitter = EventEmitter(vastdb_session, logger=ctx.logger)

    config_snapshot = config.to_snapshot()
    aws_bucket = config.target_aws_bucket
    aws_region = config.target_aws_region
    aws_storage_class = config.target_aws_storage_class
    verify = config.verify_checksum
    cluster_name = config.vast_cluster_name
    FUNCTION_NAME = "offload_and_track"

    ctx.logger.info(
        "Offload started: %d candidates -> s3://%s (%s)",
        len(candidates), aws_bucket, aws_region,
    )

    offloaded = []
    failed = []

    for candidate in candidates:
        handle = candidate["handle"]
        reg_id = candidate["reg_id"]
        src_path = candidate["path"]
        src_bucket = candidate["bucket"]
        src_key = s3_key_from_path(src_path, f"/{src_bucket}")
        aws_key = aws_key_from_path(src_path)

        try:
            # -- COPY_STARTED --
            emitter.emit_quick(
                handle, reg_id, EventType.COPY_STARTED,
                pipeline_run_id=pipeline_run_id,
                function_name=FUNCTION_NAME,
                config_snapshot=config_snapshot,
                source_path=src_path,
                destination_path=f"s3://{aws_bucket}/{aws_key}",
                aws_bucket=aws_bucket,
                aws_key=aws_key,
                file_size_bytes=candidate.get("size"),
            )

            # Read from VAST S3
            obj = s3_vast.get_object(Bucket=src_bucket, Key=src_key)
            body = obj["Body"].read()
            source_md5 = compute_md5(body)

            # Write to AWS S3 with genealogy metadata
            now_iso = datetime.now(timezone.utc).isoformat()
            s3_aws.put_object(
                Bucket=aws_bucket,
                Key=aws_key,
                Body=body,
                StorageClass=aws_storage_class,
                Metadata={
                    "vast-element-handle": handle,
                    "vast-registration-id": reg_id,
                    "vast-original-path": src_path,
                    "vast-source-cluster": cluster_name,
                    "vast-offload-timestamp": now_iso,
                    "vast-source-md5": source_md5,
                    "vast-aws-storage-class": aws_storage_class,
                },
            )

            # -- CHECKSUM VERIFICATION --
            dest_md5 = source_md5
            if verify:
                verify_obj = s3_aws.get_object(Bucket=aws_bucket, Key=aws_key)
                dest_md5 = compute_md5(verify_obj["Body"].read())

                if source_md5 != dest_md5:
                    emitter.emit_quick(
                        handle, reg_id, EventType.CHECKSUM_MISMATCH,
                        pipeline_run_id=pipeline_run_id,
                        function_name=FUNCTION_NAME,
                        config_snapshot=config_snapshot,
                        source_path=src_path,
                        aws_bucket=aws_bucket,
                        aws_key=aws_key,
                        success=False,
                        checksum_value=f"src={source_md5} dst={dest_md5}",
                        error_message="Checksum mismatch after copy",
                    )
                    failed.append({"handle": handle, "reason": "checksum_mismatch"})
                    continue

                emitter.emit_quick(
                    handle, reg_id, EventType.CHECKSUM_VERIFIED,
                    pipeline_run_id=pipeline_run_id,
                    function_name=FUNCTION_NAME,
                    config_snapshot=config_snapshot,
                    source_path=src_path,
                    aws_bucket=aws_bucket,
                    aws_key=aws_key,
                    success=True,
                    checksum_value=source_md5,
                )

            # -- COPY_COMPLETED --
            emitter.emit_quick(
                handle, reg_id, EventType.COPY_COMPLETED,
                pipeline_run_id=pipeline_run_id,
                function_name=FUNCTION_NAME,
                config_snapshot=config_snapshot,
                source_path=src_path,
                destination_path=f"s3://{aws_bucket}/{aws_key}",
                aws_bucket=aws_bucket,
                aws_key=aws_key,
                success=True,
                checksum_value=source_md5,
                file_size_bytes=candidate.get("size"),
            )

            # Update registry: LOCAL -> BOTH
            registry.update_state(
                handle, "BOTH",
                aws_bucket=aws_bucket,
                aws_key=aws_key,
                aws_region=aws_region,
                source_md5=source_md5,
                destination_md5=dest_md5,
            )

            # Tag local file for Catalog visibility
            _tag_local_file(ctx, s3_vast, src_bucket, src_key, aws_bucket, aws_key)

            offloaded.append({
                "handle": handle,
                "reg_id": reg_id,
                "original_path": src_path,
                "aws_bucket": aws_bucket,
                "aws_key": aws_key,
                "source_md5": source_md5,
            })

        except Exception as e:
            ctx.logger.error("Offload failed for %s: %s", src_path, e)
            emitter.emit_quick(
                handle, reg_id, EventType.COPY_FAILED,
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

    ctx.logger.info(
        "Offload complete: %d succeeded, %d failed",
        len(offloaded), len(failed),
    )

    return {
        "offloaded": offloaded,
        "failed": failed,
        "pipeline_run_id": pipeline_run_id,
    }


def _tag_local_file(ctx, s3_client, bucket, key, aws_bucket, aws_key):
    """Tag the local VAST S3 object with offload status for Catalog indexing."""
    from datetime import datetime, timezone

    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        s3_client.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={
                "TagSet": [
                    {"Key": "offload_status", "Value": "COPIED"},
                    {"Key": "offload_destination", "Value": f"s3://{aws_bucket}/{aws_key}"},
                    {"Key": "offload_timestamp", "Value": now_iso},
                ]
            },
        )
    except Exception as e:
        ctx.logger.warning("Failed to tag local file %s/%s: %s", bucket, key, e)

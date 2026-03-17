"""ArchiveTrail DataEngine Function: offload

Copies discovered cold files from VAST S3 to AWS S3 with:
- MD5 checksum verification (source vs destination)
- Genealogy metadata embedded in AWS S3 object metadata
- S3 tagging on local file for Catalog visibility
- Full lifecycle event chain for every step

Receives candidate list from the discover function.
"""

import logging
from datetime import datetime, timezone

import boto3

import vastdb

from archive_trail.config import ArchiveTrailConfig
from archive_trail.events import EventEmitter, EventType
from archive_trail.helpers import (
    aws_key_from_path,
    compute_md5,
    s3_key_from_path,
)
from archive_trail.registry import AssetRegistry

logger = logging.getLogger("archive_trail.functions.offload")

FUNCTION_NAME = "offload_and_track"


def handler(event: dict, context: object) -> dict:
    """DataEngine entry point.

    Args:
        event: Output from discover function containing 'candidates' list.
        context: DataEngine execution context.

    Returns:
        Dict with 'offloaded' list for the verify_purge function.
    """
    candidates = event.get("candidates", [])
    pipeline_run_id = event.get(
        "pipeline_run_id", getattr(context, "run_id", "manual")
    )

    if not candidates:
        logger.info("No candidates to offload")
        return {"offloaded": [], "pipeline_run_id": pipeline_run_id}

    # Initialize
    session = vastdb.Session()
    config = ArchiveTrailConfig(session)
    registry = AssetRegistry(session)
    emitter = EventEmitter(session)

    config_snapshot = config.to_snapshot()
    aws_bucket = config.target_aws_bucket
    aws_region = config.target_aws_region
    verify = config.verify_checksum
    cluster_name = config.vast_cluster_name

    s3_vast = boto3.client("s3", endpoint_url=config.vast_s3_endpoint)
    s3_aws = boto3.client("s3", region_name=aws_region)

    logger.info(
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
                Metadata={
                    "vast-element-handle": handle,
                    "vast-registration-id": reg_id,
                    "vast-original-path": src_path,
                    "vast-source-cluster": cluster_name,
                    "vast-offload-timestamp": now_iso,
                    "vast-source-md5": source_md5,
                },
            )

            # -- CHECKSUM VERIFICATION --
            dest_md5 = source_md5  # default if verification skipped
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
            _tag_local_file(s3_vast, src_bucket, src_key, aws_bucket, aws_key)

            offloaded.append({
                "handle": handle,
                "reg_id": reg_id,
                "original_path": src_path,
                "aws_bucket": aws_bucket,
                "aws_key": aws_key,
                "source_md5": source_md5,
            })

        except Exception as e:
            logger.error("Offload failed for %s: %s", src_path, e)
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

    logger.info(
        "Offload complete: %d succeeded, %d failed",
        len(offloaded), len(failed),
    )

    return {
        "offloaded": offloaded,
        "failed": failed,
        "pipeline_run_id": pipeline_run_id,
    }


def _tag_local_file(
    s3_client: boto3.client,
    bucket: str,
    key: str,
    aws_bucket: str,
    aws_key: str,
) -> None:
    """Tag the local VAST S3 object with offload status for Catalog indexing."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        s3_client.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={
                "TagSet": [
                    {"Key": "offload_status", "Value": "COPIED"},
                    {
                        "Key": "offload_destination",
                        "Value": f"s3://{aws_bucket}/{aws_key}",
                    },
                    {"Key": "offload_timestamp", "Value": now_iso},
                ]
            },
        )
    except Exception as e:
        logger.warning("Failed to tag local file %s/%s: %s", bucket, key, e)

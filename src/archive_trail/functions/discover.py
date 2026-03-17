"""ArchiveTrail DataEngine Function: discover

Queries the VAST Catalog for files whose last access time exceeds the
configured threshold. Registers discovered files in the asset_registry
and emits REGISTERED + THRESHOLD_EVALUATED lifecycle events.

Outputs a list of candidate files for the next pipeline stage (offload).
"""

import json
import logging

import vastdb

from archive_trail.config import ArchiveTrailConfig
from archive_trail.events import EventEmitter, EventType
from archive_trail.helpers import (
    days_since,
    full_path,
    path_like_clauses,
)
from archive_trail.registry import AssetRegistry, CatalogEntry

logger = logging.getLogger("archive_trail.functions.discover")

FUNCTION_NAME = "discover"


def handler(event: dict, context: object) -> dict:
    """DataEngine entry point.

    Args:
        event: Trigger event payload (schedule trigger metadata).
        context: DataEngine execution context with run_id, etc.

    Returns:
        Dict with 'candidates' list for the next function in the pipeline.
    """
    pipeline_run_id = getattr(context, "run_id", "manual")

    # Initialize VAST DB session and components
    session = vastdb.Session()
    config = ArchiveTrailConfig(session)
    registry = AssetRegistry(session)
    emitter = EventEmitter(session)

    config_snapshot = config.to_snapshot()
    threshold = config.atime_threshold_days
    source_paths = config.source_paths
    batch_size = config.batch_size
    catalog_schema = config.catalog_schema
    catalog_table = config.catalog_table

    logger.info(
        "Discover started: threshold=%dd, paths=%s, batch=%d, dry_run=%s",
        threshold, source_paths, batch_size, config.dry_run,
    )

    # Build the path filter clause
    path_filter = path_like_clauses(source_paths)

    # Query the VAST Catalog for cold files not yet offloaded
    query = f"""
        SELECT handle, parent_path, name, extension, size,
               atime, mtime, ctime, login_name, nfs_mode_bits
        FROM vast."{catalog_schema}".{catalog_table} c
        WHERE c.atime < now() - INTERVAL '{threshold}' DAY
          AND c.element_type = 'FILE'
          AND {path_filter}
          AND c.handle NOT IN (
              SELECT element_handle
              FROM vast."archive/lineage".asset_registry
              WHERE current_location IN ('AWS', 'BOTH', 'LOCAL_DELETED')
          )
        ORDER BY c.atime ASC
        LIMIT {batch_size}
    """

    cold_files = session.query(query)
    logger.info("Catalog query returned %d cold files", len(cold_files))

    candidates = []

    for row in cold_files:
        entry = CatalogEntry(
            handle=row["handle"],
            parent_path=row["parent_path"],
            name=row["name"],
            extension=row.get("extension", ""),
            size=row["size"],
            atime=row["atime"],
            mtime=row["mtime"],
            ctime=row["ctime"],
            login_name=row.get("login_name", "unknown"),
            nfs_mode_bits=row.get("nfs_mode_bits", 0),
        )

        file_path = full_path(entry.parent_path, entry.name)
        age_days = days_since(entry.atime)

        if config.dry_run:
            emitter.emit_quick(
                entry.handle,
                "dry-run",
                EventType.SCANNED,
                pipeline_run_id=pipeline_run_id,
                function_name=FUNCTION_NAME,
                config_snapshot=config_snapshot,
                source_path=file_path,
                file_size_bytes=entry.size,
                file_atime=entry.atime,
                file_mtime=entry.mtime,
                success=True,
                error_message=f"DRY_RUN: age={age_days}d, threshold={threshold}d",
            )
            continue

        # Skip if already registered and offloaded
        if registry.is_already_offloaded(entry.handle):
            logger.debug("Skipping already-offloaded: %s", file_path)
            continue

        # Resolve bucket and view from path
        # In production, these would come from a path-to-view mapping
        bucket = _resolve_bucket(entry.parent_path)
        view = _resolve_view(entry.parent_path)

        # Register the asset
        reg_id = registry.register(entry, bucket=bucket, view=view)

        # Emit REGISTERED event
        emitter.emit_quick(
            entry.handle,
            reg_id,
            EventType.REGISTERED,
            pipeline_run_id=pipeline_run_id,
            function_name=FUNCTION_NAME,
            config_snapshot=config_snapshot,
            source_path=file_path,
            file_size_bytes=entry.size,
            file_atime=entry.atime,
            file_mtime=entry.mtime,
            success=True,
        )

        # Emit THRESHOLD_EVALUATED event (the "why" record)
        emitter.emit_quick(
            entry.handle,
            reg_id,
            EventType.THRESHOLD_EVALUATED,
            pipeline_run_id=pipeline_run_id,
            function_name=FUNCTION_NAME,
            config_snapshot=config_snapshot,
            source_path=file_path,
            file_size_bytes=entry.size,
            file_atime=entry.atime,
            file_mtime=entry.mtime,
            success=True,
            error_message=(
                f"atime={entry.atime.isoformat()}, "
                f"threshold={threshold}d, age={age_days}d"
            ),
        )

        candidates.append(
            {
                "handle": entry.handle,
                "reg_id": reg_id,
                "path": file_path,
                "size": entry.size,
                "bucket": bucket,
                "atime": entry.atime.isoformat(),
                "mtime": entry.mtime.isoformat(),
            }
        )

    logger.info(
        "Discover complete: %d candidates (dry_run=%s)",
        len(candidates), config.dry_run,
    )

    return {"candidates": candidates, "pipeline_run_id": pipeline_run_id}


def _resolve_bucket(parent_path: str) -> str:
    """Resolve the VAST S3 bucket name from a parent path.

    In a production deployment, this would query VAST VMS REST API
    to find the view/bucket associated with this path. For now,
    we derive it from the path convention: /tenant/<bucket>/...
    """
    parts = parent_path.strip("/").split("/")
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else "unknown"


def _resolve_view(parent_path: str) -> str:
    """Resolve the VAST view name from a parent path.

    Same caveat as _resolve_bucket — production would use VMS API.
    """
    parts = parent_path.strip("/").split("/")
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else "unknown"

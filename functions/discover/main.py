"""VAST DataEngine handler: discover

Queries the VAST Catalog for files whose last access time exceeds the
configured threshold. Registers discovered files in the asset_registry
and emits REGISTERED + THRESHOLD_EVALUATED lifecycle events.

Outputs a candidate manifest for the next pipeline stage (offload).
"""

import os
import uuid

__version__ = "0.1.0"

try:
    import boto3
    from botocore.config import Config as BotoConfig
except ImportError:
    boto3 = None

try:
    import pyarrow as pa
except ImportError:
    pa = None

# Global state — initialized once in init(), reused per event
vastdb_session = None
s3_client = None
_tables_verified = False


def init(ctx):
    """One-time initialization when the container starts.

    Creates VastDB session, verifies/creates tables, creates S3 client.
    Runs ONCE per pod lifetime, not per event.
    """
    global vastdb_session, s3_client, _tables_verified

    ctx.logger.info("INITIALIZING ARCHIVE-TRAIL DISCOVER %s", __version__)

    # --- VastDB Session ---
    from archive_trail.db import create_session, ensure_tables
    vastdb_session = create_session(logger=ctx.logger)

    # --- Auto-provision tables (DDL in its own transaction) ---
    if not _tables_verified:
        ensure_tables(vastdb_session, logger=ctx.logger)
        _tables_verified = True

    # --- Seed default config if needed ---
    from archive_trail.config import ArchiveTrailConfig
    try:
        config = ArchiveTrailConfig(vastdb_session, logger=ctx.logger)
    except ValueError:
        # Config table is empty — seed defaults
        config = ArchiveTrailConfig.__new__(ArchiveTrailConfig)
        config._session = vastdb_session
        config._logger = ctx.logger
        config._user_config = {}
        config.seed_defaults()
        ctx.logger.info("Config seeded with defaults")

    # --- S3 Client (for reading from VAST S3) ---
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
        s3_client = boto3.client(
            "s3",
            endpoint_url=s3_endpoint,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
            config=s3_config,
        )
        ctx.logger.info("VAST S3 client created: %s", s3_endpoint)
    else:
        ctx.logger.warning("S3 client not created (missing credentials or boto3)")

    ctx.logger.info("ARCHIVE-TRAIL DISCOVER initialized successfully")


def handler(ctx, event):
    """Per-event handler for the discover function.

    Args:
        ctx: VAST function context (has .logger)
        event: CloudEvent from schedule trigger (tick event with custom extensions)

    Returns:
        Dict with 'candidates' list for the offload function.
    """
    from datetime import datetime, timezone
    from archive_trail.config import ArchiveTrailConfig
    from archive_trail.events import EventEmitter, EventType
    from archive_trail.helpers import days_since, full_path
    from archive_trail.registry import AssetRegistry, CatalogEntry
    from archive_trail.db import get_table

    pipeline_run_id = f"schedule-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    # Load config and create components using the global session
    config = ArchiveTrailConfig(vastdb_session, logger=ctx.logger)
    registry = AssetRegistry(vastdb_session, logger=ctx.logger)
    emitter = EventEmitter(vastdb_session, logger=ctx.logger)

    config_snapshot = config.to_snapshot()
    threshold = config.atime_threshold_days
    source_paths = config.source_paths
    batch_size = config.batch_size

    ctx.logger.info(
        "Discover started: threshold=%dd, paths=%s, batch=%d, dry_run=%s",
        threshold, source_paths, batch_size, config.dry_run,
    )

    # Get already-offloaded handles for filtering (avoids complex subquery)
    offloaded_handles = registry.get_offloaded_handles()
    ctx.logger.info("Already offloaded: %d handles", len(offloaded_handles))

    # Query the VAST Catalog for cold files
    cold_files = _query_catalog(ctx, config)
    ctx.logger.info("Catalog query returned %d cold files", len(cold_files))

    candidates = []
    FUNCTION_NAME = "discover"

    for entry in cold_files:
        # Skip already-offloaded files
        if entry.handle in offloaded_handles:
            continue

        file_path = full_path(entry.parent_path, entry.name)
        age_days = days_since(entry.atime)

        if config.dry_run:
            emitter.emit_quick(
                entry.handle, "dry-run", EventType.SCANNED,
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

        # Resolve bucket and view from path
        bucket = _resolve_bucket(entry.parent_path)
        view = _resolve_view(entry.parent_path)

        # Register the asset
        reg_id = registry.register(entry, bucket=bucket, view=view)

        # Emit REGISTERED event
        emitter.emit_quick(
            entry.handle, reg_id, EventType.REGISTERED,
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
            entry.handle, reg_id, EventType.THRESHOLD_EVALUATED,
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

        candidates.append({
            "handle": entry.handle,
            "reg_id": reg_id,
            "path": file_path,
            "size": entry.size,
            "bucket": bucket,
            "atime": entry.atime.isoformat(),
            "mtime": entry.mtime.isoformat(),
        })

        if len(candidates) >= batch_size:
            break

    ctx.logger.info(
        "Discover complete: %d candidates (dry_run=%s)",
        len(candidates), config.dry_run,
    )

    return {"candidates": candidates, "pipeline_run_id": pipeline_run_id}


def _query_catalog(ctx, config):
    """Query the VAST Catalog for cold files using the vastdb SDK.

    Reads the Catalog table with predicate pushdown for:
      - element_type = 'FILE'
      - atime < threshold
      - parent_path matches source_paths

    Returns a list of CatalogEntry objects.
    """
    from datetime import datetime, timedelta, timezone
    from archive_trail.registry import CatalogEntry

    threshold_dt = datetime.now(timezone.utc) - timedelta(days=config.atime_threshold_days)
    source_paths = config.source_paths
    catalog_bucket = config.catalog_bucket

    try:
        with vastdb_session.transaction() as tx:
            bucket = tx.bucket(catalog_bucket)
            # Navigate to the Catalog schema and table
            # The exact path depends on cluster configuration
            catalog_schema_name = os.environ.get("VAST_CATALOG_SCHEMA", "catalog")
            catalog_table_name = os.environ.get("VAST_CATALOG_TABLE", "catalog_table")
            schema = bucket.schema(catalog_schema_name)
            table = schema.table(catalog_table_name)

            # Build predicate: FILE type with atime before threshold
            predicate = (
                (pa.compute.field("element_type") == "FILE")
                & (pa.compute.field("atime") < threshold_dt)
            )

            # Add path filter if source_paths configured
            if source_paths:
                path_predicates = None
                for sp in source_paths:
                    # Use starts_with for path prefix matching
                    p = pa.compute.field("parent_path").cast(pa.utf8()).isin([sp])
                    # Fallback: we'll filter paths in Python after select
                    pass

            result = table.select(
                filter=predicate,
                columns=[
                    "handle", "parent_path", "name", "extension", "size",
                    "atime", "mtime", "ctime", "login_name", "nfs_mode_bits",
                ],
            )

        # Convert to CatalogEntry objects, applying path filter in Python
        entries = []
        for i in range(result.num_rows):
            parent_path = result.column("parent_path")[i].as_py()

            # Filter by source paths (prefix match)
            if source_paths:
                matches = any(
                    parent_path == sp or parent_path.startswith(sp + "/")
                    for sp in source_paths
                )
                if not matches:
                    continue

            entries.append(CatalogEntry(
                handle=result.column("handle")[i].as_py(),
                parent_path=parent_path,
                name=result.column("name")[i].as_py(),
                extension=result.column("extension")[i].as_py() or "",
                size=result.column("size")[i].as_py(),
                atime=result.column("atime")[i].as_py(),
                mtime=result.column("mtime")[i].as_py(),
                ctime=result.column("ctime")[i].as_py(),
                login_name=result.column("login_name")[i].as_py() or "unknown",
                nfs_mode_bits=result.column("nfs_mode_bits")[i].as_py() or 0,
            ))

        return entries

    except Exception as exc:
        ctx.logger.error("Catalog query failed: %s", exc)
        return []


def _resolve_bucket(parent_path: str) -> str:
    """Resolve the VAST S3 bucket name from a parent path.

    Convention: /tenant/<bucket>/...
    Production would use VMS REST API for path-to-view mapping.
    """
    parts = parent_path.strip("/").split("/")
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else "unknown"


def _resolve_view(parent_path: str) -> str:
    """Resolve the VAST view name from a parent path."""
    parts = parent_path.strip("/").split("/")
    if len(parts) >= 2:
        return parts[1]
    return parts[0] if parts else "unknown"

"""VAST Database layer for ArchiveTrail.

Defines PyArrow schemas for all tables, provides auto-provisioning
via get-or-create pattern, and manages the VastDB session lifecycle.

Uses the real vastdb Python SDK (PyArrow-based):
  - vastdb.connect() for session creation
  - session.transaction() for all operations
  - table.insert(pa.table) for writes
  - table.select() for reads
  - schema.create_table(name, pa.schema) for DDL
"""

import os

import pyarrow as pa
import vastdb

# ---------------------------------------------------------------------------
# Schema / table names
# ---------------------------------------------------------------------------

BUCKET_NAME = os.environ.get("VAST_DB_BUCKET", "archive-trail-db")
SCHEMA_NAME = os.environ.get("VAST_DB_SCHEMA", "archive/lineage")

TABLE_ASSET_REGISTRY = "asset_registry"
TABLE_LIFECYCLE_EVENTS = "lifecycle_events"
TABLE_OFFLOAD_CONFIG = "offload_config"
TABLE_CONFIG_CHANGE_LOG = "config_change_log"

# ---------------------------------------------------------------------------
# PyArrow schema definitions
# ---------------------------------------------------------------------------

ASSET_REGISTRY_SCHEMA = pa.schema([
    # Identity (immutable)
    ("element_handle", pa.utf8()),
    ("registration_id", pa.utf8()),
    # Origin (captured at first discovery)
    ("original_path", pa.utf8()),
    ("original_bucket", pa.utf8()),
    ("original_view", pa.utf8()),
    ("file_name", pa.utf8()),
    ("file_extension", pa.utf8()),
    ("file_size_bytes", pa.int64()),
    ("file_ctime", pa.timestamp("us")),
    ("file_mtime", pa.timestamp("us")),
    ("file_atime", pa.timestamp("us")),
    ("owner_uid", pa.utf8()),
    ("owner_login", pa.utf8()),
    ("nfs_mode_bits", pa.int32()),
    # Current state
    ("current_location", pa.utf8()),
    ("current_aws_bucket", pa.utf8()),
    ("current_aws_key", pa.utf8()),
    ("current_aws_region", pa.utf8()),
    # Timestamps
    ("registered_at", pa.timestamp("us")),
    ("last_state_change", pa.timestamp("us")),
    # Integrity
    ("source_md5", pa.utf8()),
    ("destination_md5", pa.utf8()),
])

LIFECYCLE_EVENTS_SCHEMA = pa.schema([
    # Identity
    ("event_id", pa.utf8()),
    ("element_handle", pa.utf8()),
    ("registration_id", pa.utf8()),
    # Event
    ("event_type", pa.utf8()),
    ("event_timestamp", pa.timestamp("us")),
    # Context
    ("source_path", pa.utf8()),
    ("destination_path", pa.utf8()),
    ("aws_bucket", pa.utf8()),
    ("aws_key", pa.utf8()),
    # Metadata snapshot
    ("file_size_bytes", pa.int64()),
    ("file_atime", pa.timestamp("us")),
    ("file_mtime", pa.timestamp("us")),
    # Execution
    ("pipeline_run_id", pa.utf8()),
    ("function_name", pa.utf8()),
    ("triggered_by", pa.utf8()),
    # Result
    ("success", pa.bool_()),
    ("error_message", pa.utf8()),
    ("checksum_value", pa.utf8()),
    # Traceability
    ("config_snapshot", pa.utf8()),
])

OFFLOAD_CONFIG_SCHEMA = pa.schema([
    ("config_key", pa.utf8()),
    ("config_value", pa.utf8()),
    ("updated_by", pa.utf8()),
    ("updated_at", pa.timestamp("us")),
    ("change_reason", pa.utf8()),
])

CONFIG_CHANGE_LOG_SCHEMA = pa.schema([
    ("change_id", pa.utf8()),
    ("config_key", pa.utf8()),
    ("old_value", pa.utf8()),
    ("new_value", pa.utf8()),
    ("changed_by", pa.utf8()),
    ("changed_at", pa.timestamp("us")),
    ("change_reason", pa.utf8()),
])

# All table definitions for auto-provisioning
TABLE_DEFINITIONS = {
    TABLE_ASSET_REGISTRY: ASSET_REGISTRY_SCHEMA,
    TABLE_LIFECYCLE_EVENTS: LIFECYCLE_EVENTS_SCHEMA,
    TABLE_OFFLOAD_CONFIG: OFFLOAD_CONFIG_SCHEMA,
    TABLE_CONFIG_CHANGE_LOG: CONFIG_CHANGE_LOG_SCHEMA,
}


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def create_session(logger=None) -> vastdb.Session:
    """Create a VastDB session from environment variables.

    Required env vars:
        VAST_DB_ENDPOINT or S3_ENDPOINT
        VAST_DB_ACCESS_KEY or S3_ACCESS_KEY
        VAST_DB_SECRET_KEY or S3_SECRET_KEY
    """
    endpoint = os.environ.get("VAST_DB_ENDPOINT") or os.environ.get("S3_ENDPOINT", "")
    access_key = os.environ.get("VAST_DB_ACCESS_KEY") or os.environ.get("S3_ACCESS_KEY", "")
    secret_key = os.environ.get("VAST_DB_SECRET_KEY") or os.environ.get("S3_SECRET_KEY", "")

    if not all([endpoint, access_key, secret_key]):
        msg = "VastDB credentials incomplete (need VAST_DB_ENDPOINT, VAST_DB_ACCESS_KEY, VAST_DB_SECRET_KEY)"
        if logger:
            logger.error(msg)
        raise RuntimeError(msg)

    session = vastdb.connect(
        endpoint=endpoint,
        access=access_key,
        secret=secret_key,
    )
    if logger:
        logger.info("VastDB session created: endpoint=%s", endpoint)
    return session


# ---------------------------------------------------------------------------
# Auto-provisioning (get-or-create pattern)
# ---------------------------------------------------------------------------

def _get_or_create_schema(bucket, name, logger=None):
    """Get an existing schema or create it. Handles race conditions."""
    try:
        return bucket.schema(name)
    except Exception:
        pass
    try:
        s = bucket.create_schema(name)
        if logger:
            logger.info("Created schema: %s", name)
        return s
    except Exception:
        return bucket.schema(name)  # Race condition retry


def _get_or_create_table(schema, name, arrow_schema, logger=None):
    """Get an existing table or create it. Handles race conditions."""
    try:
        return schema.table(name)
    except Exception:
        pass
    try:
        t = schema.create_table(name, arrow_schema)
        if logger:
            logger.info("Created table: %s", name)
        return t
    except Exception:
        return schema.table(name)


def ensure_tables(session, logger=None):
    """Auto-provision the schema and all tables.

    Uses get-or-create pattern — safe to call multiple times.
    Runs DDL in its own transaction, separate from DML.
    """
    with session.transaction() as tx:
        bucket = tx.bucket(BUCKET_NAME)
        schema = _get_or_create_schema(bucket, SCHEMA_NAME, logger=logger)
        for table_name, arrow_schema in TABLE_DEFINITIONS.items():
            _get_or_create_table(schema, table_name, arrow_schema, logger=logger)

    if logger:
        logger.info(
            "Tables verified: %s in %s/%s",
            list(TABLE_DEFINITIONS.keys()), BUCKET_NAME, SCHEMA_NAME,
        )


def get_table(tx, table_name):
    """Get a table handle within an active transaction."""
    bucket = tx.bucket(BUCKET_NAME)
    schema = bucket.schema(SCHEMA_NAME)
    return schema.table(table_name)

-- ArchiveTrail: Table Creation
-- Run after 001_create_schema.sql
-- Execute via VAST DB query editor or any Trino-compatible SQL client.

-- Table 1: User-configurable parameters
CREATE TABLE IF NOT EXISTS vast."archive/lineage".offload_config (
    config_key        VARCHAR,
    config_value      VARCHAR,
    updated_by        VARCHAR,
    updated_at        TIMESTAMP,
    change_reason     VARCHAR
);

-- Table 2: Master identity table (one row per element, ever)
CREATE TABLE IF NOT EXISTS vast."archive/lineage".asset_registry (
    -- IDENTITY (immutable)
    element_handle       VARCHAR,
    registration_id      VARCHAR,

    -- ORIGIN (captured at first discovery)
    original_path        VARCHAR,
    original_bucket      VARCHAR,
    original_view        VARCHAR,
    file_name            VARCHAR,
    file_extension       VARCHAR,
    file_size_bytes      BIGINT,
    file_ctime           TIMESTAMP,
    file_mtime           TIMESTAMP,
    file_atime           TIMESTAMP,
    owner_uid            VARCHAR,
    owner_login          VARCHAR,
    nfs_mode_bits        INTEGER,

    -- CURRENT STATE
    current_location     VARCHAR,
    current_aws_bucket   VARCHAR,
    current_aws_key      VARCHAR,
    current_aws_region   VARCHAR,

    -- TIMESTAMPS
    registered_at        TIMESTAMP,
    last_state_change    TIMESTAMP,

    -- INTEGRITY
    source_md5           VARCHAR,
    destination_md5      VARCHAR
);

-- Table 3: Append-only lifecycle events (traceability chain)
CREATE TABLE IF NOT EXISTS vast."archive/lineage".lifecycle_events (
    -- IDENTITY
    event_id             VARCHAR,
    element_handle       VARCHAR,
    registration_id      VARCHAR,

    -- EVENT
    event_type           VARCHAR,
    event_timestamp      TIMESTAMP,

    -- CONTEXT
    source_path          VARCHAR,
    destination_path     VARCHAR,
    aws_bucket           VARCHAR,
    aws_key              VARCHAR,

    -- METADATA SNAPSHOT
    file_size_bytes      BIGINT,
    file_atime           TIMESTAMP,
    file_mtime           TIMESTAMP,

    -- EXECUTION
    pipeline_run_id      VARCHAR,
    function_name        VARCHAR,
    triggered_by         VARCHAR,

    -- RESULT
    success              BOOLEAN,
    error_message        VARCHAR,
    checksum_value       VARCHAR,

    -- TRACEABILITY
    config_snapshot      VARCHAR
);

-- Table 4: Config change log (config genealogy)
CREATE TABLE IF NOT EXISTS vast."archive/lineage".config_change_log (
    change_id            VARCHAR,
    config_key           VARCHAR,
    old_value            VARCHAR,
    new_value            VARCHAR,
    changed_by           VARCHAR,
    changed_at           TIMESTAMP,
    change_reason        VARCHAR
);

"""Pre-built genealogy and traceability queries for ArchiveTrail.

These queries can be run directly via Trino or through the VAST Query Engine.
They are also used by the CLI for genealogy reporting.
"""

SCHEMA = "archive/lineage"

# -- Asset Location Queries --

LOCATE_BY_PATH = f"""
SELECT element_handle, original_path, current_location,
       current_aws_bucket, current_aws_key, source_md5,
       registered_at, last_state_change
FROM vast."{SCHEMA}".asset_registry
WHERE original_path LIKE ?
ORDER BY last_state_change DESC
"""

LOCATE_BY_HANDLE = f"""
SELECT element_handle, original_path, current_location,
       current_aws_bucket, current_aws_key, current_aws_region,
       source_md5, destination_md5,
       registered_at, last_state_change
FROM vast."{SCHEMA}".asset_registry
WHERE element_handle = ?
"""

# -- Full Lifecycle History --

LIFECYCLE_HISTORY = f"""
SELECT event_type, event_timestamp, source_path, destination_path,
       aws_bucket, aws_key, success, checksum_value, error_message,
       pipeline_run_id, function_name, triggered_by, config_snapshot
FROM vast."{SCHEMA}".lifecycle_events
WHERE element_handle = ?
ORDER BY event_timestamp ASC
"""

# -- Cross-Reference with VAST Protocol Audit --

AUDIT_CORROBORATE = """
SELECT timestamp, protocol, operation, object_path, user_name, bytes
FROM vast."{audit_schema}".audit_table
WHERE object_path LIKE ?
  AND timestamp BETWEEN ? AND ?
ORDER BY timestamp
"""

# -- Config Genealogy --

CONFIG_AT_OFFLOAD_TIME = f"""
SELECT event_type, event_timestamp,
       json_extract_scalar(config_snapshot, '$.atime_threshold_days') AS threshold,
       json_extract_scalar(config_snapshot, '$.auto_delete_local') AS auto_delete,
       json_extract_scalar(config_snapshot, '$.target_aws_bucket') AS aws_bucket
FROM vast."{SCHEMA}".lifecycle_events
WHERE element_handle = ?
  AND event_type = 'THRESHOLD_EVALUATED'
"""

CONFIG_CHANGE_HISTORY = f"""
SELECT change_id, config_key, old_value, new_value,
       changed_by, changed_at, change_reason
FROM vast."{SCHEMA}".config_change_log
ORDER BY changed_at DESC
LIMIT ?
"""

CONFIG_CHANGE_FOR_KEY = f"""
SELECT change_id, old_value, new_value,
       changed_by, changed_at, change_reason
FROM vast."{SCHEMA}".config_change_log
WHERE config_key = ?
ORDER BY changed_at DESC
"""

# -- Files Offloaded Under Changed Thresholds --

OFFLOADED_WITH_STALE_THRESHOLD = f"""
SELECT a.element_handle, a.original_path, e.event_timestamp,
       json_extract_scalar(e.config_snapshot, '$.atime_threshold_days')
           AS threshold_at_offload,
       c.config_value AS current_threshold
FROM vast."{SCHEMA}".asset_registry a
JOIN vast."{SCHEMA}".lifecycle_events e
  ON a.element_handle = e.element_handle
  AND e.event_type = 'COPY_COMPLETED'
CROSS JOIN (
    SELECT config_value FROM vast."{SCHEMA}".offload_config
    WHERE config_key = 'atime_threshold_days'
) c
WHERE json_extract_scalar(e.config_snapshot, '$.atime_threshold_days')
      != c.config_value
ORDER BY e.event_timestamp DESC
"""

# -- Summary Statistics --

ASSET_SUMMARY_BY_LOCATION = f"""
SELECT current_location,
       COUNT(*) AS asset_count,
       SUM(file_size_bytes) AS total_bytes,
       MIN(registered_at) AS earliest_registered,
       MAX(last_state_change) AS latest_change
FROM vast."{SCHEMA}".asset_registry
GROUP BY current_location
"""

EVENT_COUNTS_BY_TYPE = f"""
SELECT event_type,
       COUNT(*) AS event_count,
       MIN(event_timestamp) AS earliest,
       MAX(event_timestamp) AS latest
FROM vast."{SCHEMA}".lifecycle_events
GROUP BY event_type
ORDER BY event_count DESC
"""

FAILED_EVENTS = f"""
SELECT event_id, element_handle, event_type, event_timestamp,
       source_path, error_message, pipeline_run_id
FROM vast."{SCHEMA}".lifecycle_events
WHERE success = false
ORDER BY event_timestamp DESC
LIMIT ?
"""

# -- Purged Files Report --

PURGED_FILES_BY_PATH = f"""
SELECT a.element_handle, a.file_name, a.file_size_bytes,
       a.original_path, a.current_aws_bucket, a.current_aws_key,
       a.registered_at, a.last_state_change, a.source_md5
FROM vast."{SCHEMA}".asset_registry a
WHERE a.original_path LIKE ?
  AND a.current_location = 'LOCAL_DELETED'
ORDER BY a.last_state_change DESC
"""

# -- Pipeline Run Report --

PIPELINE_RUN_EVENTS = f"""
SELECT event_type, event_timestamp, element_handle,
       source_path, success, error_message
FROM vast."{SCHEMA}".lifecycle_events
WHERE pipeline_run_id = ?
ORDER BY event_timestamp ASC
"""

# -- Data Volume Offloaded Over Time --

OFFLOAD_VOLUME_BY_DAY = f"""
SELECT DATE(event_timestamp) AS offload_date,
       COUNT(*) AS files_offloaded,
       SUM(file_size_bytes) AS bytes_offloaded
FROM vast."{SCHEMA}".lifecycle_events
WHERE event_type = 'COPY_COMPLETED'
  AND success = true
GROUP BY DATE(event_timestamp)
ORDER BY offload_date DESC
LIMIT ?
"""

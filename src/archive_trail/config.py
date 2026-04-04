"""Configuration management for ArchiveTrail.

Two-tier configuration:
  1. Bootstrap config (env vars): S3 endpoint, cluster name, DB connection
  2. User config (VAST DB table): threshold, target bucket, source paths, flags

Bootstrap values are loaded from environment variables (available before DB).
User-configurable values are loaded from the offload_config table in VAST DB.
Config changes are tracked with full genealogy in config_change_log.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import pyarrow as pa

from archive_trail.db import (
    CONFIG_CHANGE_LOG_SCHEMA,
    OFFLOAD_CONFIG_SCHEMA,
    TABLE_CONFIG_CHANGE_LOG,
    TABLE_OFFLOAD_CONFIG,
    get_table,
)

# User-configurable keys expected in the offload_config table
USER_CONFIG_KEYS = {
    "atime_threshold_days",
    "target_aws_bucket",
    "target_aws_region",
    "target_aws_storage_class",
    "source_paths",
    "auto_delete_local",
    "dry_run",
    "batch_size",
    "verify_checksum",
}


class ArchiveTrailConfig:
    """Loads and provides typed access to ArchiveTrail configuration.

    Bootstrap values come from env vars; user-tunable values from VAST DB.
    """

    def __init__(self, session, logger=None):
        self._session = session
        self._logger = logger
        self._user_config: dict[str, str] = {}
        self.reload()

    def _log(self, level, msg, *args):
        if self._logger:
            getattr(self._logger, level)(msg, *args)

    def reload(self) -> None:
        """Load or refresh user config from VAST DB."""
        with self._session.transaction() as tx:
            table = get_table(tx, TABLE_OFFLOAD_CONFIG)
            result = table.select(columns=["config_key", "config_value"])

        keys = result.column("config_key").to_pylist()
        values = result.column("config_value").to_pylist()
        self._user_config = dict(zip(keys, values))
        self._validate()
        self._log("info", "Config loaded: %d user keys", len(self._user_config))

    def _validate(self) -> None:
        missing = USER_CONFIG_KEYS - set(self._user_config.keys())
        if missing:
            raise ValueError(f"Missing required config keys in offload_config: {missing}")

    # -- Bootstrap accessors (from env vars) --

    @property
    def vast_s3_endpoint(self) -> str:
        return os.environ.get("S3_ENDPOINT", "")

    @property
    def vast_cluster_name(self) -> str:
        return os.environ.get("VAST_CLUSTER_NAME", "unknown-cluster")

    @property
    def catalog_bucket(self) -> str:
        return os.environ.get("VAST_CATALOG_BUCKET", "vast-big-catalog-bucket")

    # -- User-configurable accessors (from DB) --

    @property
    def atime_threshold_days(self) -> int:
        return int(self._user_config["atime_threshold_days"])

    @property
    def target_aws_bucket(self) -> str:
        return self._user_config["target_aws_bucket"]

    @property
    def target_aws_region(self) -> str:
        return self._user_config["target_aws_region"]

    @property
    def target_aws_storage_class(self) -> str:
        """AWS S3 storage class for archived objects.

        Valid values: STANDARD, INTELLIGENT_TIERING, STANDARD_IA,
        ONEZONE_IA, GLACIER, GLACIER_IR, DEEP_ARCHIVE
        """
        return self._user_config.get("target_aws_storage_class", "INTELLIGENT_TIERING")

    @property
    def source_paths(self) -> list[str]:
        return [p.strip() for p in self._user_config["source_paths"].split(",")]

    @property
    def auto_delete_local(self) -> bool:
        return self._user_config["auto_delete_local"].lower() == "true"

    @property
    def dry_run(self) -> bool:
        return self._user_config["dry_run"].lower() == "true"

    @property
    def batch_size(self) -> int:
        return int(self._user_config["batch_size"])

    @property
    def verify_checksum(self) -> bool:
        return self._user_config["verify_checksum"].lower() == "true"

    def to_snapshot(self) -> str:
        """Serialize current config as JSON for embedding in lifecycle events.

        Includes both user config and bootstrap values for full traceability.
        """
        snapshot = dict(self._user_config)
        snapshot["vast_s3_endpoint"] = self.vast_s3_endpoint
        snapshot["vast_cluster_name"] = self.vast_cluster_name
        return json.dumps(snapshot, sort_keys=True)

    def update(
        self, key: str, new_value: str, changed_by: str, reason: str
    ) -> None:
        """Update a config key with full change tracking."""
        if key not in self._user_config:
            raise KeyError(f"Unknown config key: {key}")

        old_value = self._user_config[key]
        if old_value == new_value:
            self._log("info", "Config key '%s' unchanged, skipping", key)
            return

        now = datetime.now(timezone.utc)
        change_id = str(uuid.uuid4())

        with self._session.transaction() as tx:
            # Record the change in the change log
            change_row = pa.table(
                schema=CONFIG_CHANGE_LOG_SCHEMA,
                data=[
                    [change_id],
                    [key],
                    [old_value],
                    [new_value],
                    [changed_by],
                    [now],
                    [reason],
                ],
            )
            change_table = get_table(tx, TABLE_CONFIG_CHANGE_LOG)
            change_table.insert(change_row)

            # Update the config table (delete old row, insert new)
            config_table = get_table(tx, TABLE_OFFLOAD_CONFIG)
            predicate = pa.compute.field("config_key") == key
            config_table.delete(predicate)

            updated_row = pa.table(
                schema=OFFLOAD_CONFIG_SCHEMA,
                data=[[key], [new_value], [changed_by], [now], [reason]],
            )
            config_table.insert(updated_row)

        self._user_config[key] = new_value
        self._log(
            "info",
            "Config updated: %s = '%s' -> '%s' by %s (%s)",
            key, old_value, new_value, changed_by, reason,
        )

    def seed_defaults(self, defaults: Optional[dict[str, str]] = None) -> None:
        """Seed the config table with default values if empty.

        Only inserts keys that don't already exist. Safe to call multiple times.
        """
        if defaults is None:
            defaults = {
                "atime_threshold_days": "60",
                "target_aws_bucket": "corp-cold-tier",
                "target_aws_region": "us-east-1",
                "target_aws_storage_class": "INTELLIGENT_TIERING",
                "source_paths": "/tenant/projects,/tenant/media",
                "auto_delete_local": "false",
                "dry_run": "true",
                "batch_size": "500",
                "verify_checksum": "true",
            }

        now = datetime.now(timezone.utc)
        existing_keys = set(self._user_config.keys())
        new_keys = set(defaults.keys()) - existing_keys

        if not new_keys:
            self._log("info", "Config already seeded, no new keys needed")
            return

        with self._session.transaction() as tx:
            config_table = get_table(tx, TABLE_OFFLOAD_CONFIG)
            for key in sorted(new_keys):
                row = pa.table(
                    schema=OFFLOAD_CONFIG_SCHEMA,
                    data=[[key], [defaults[key]], ["system"], [now], ["Initial setup"]],
                )
                config_table.insert(row)

        # Reload to pick up the new values
        self.reload()
        self._log("info", "Config seeded: %d new keys", len(new_keys))

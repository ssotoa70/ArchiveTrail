"""Configuration management for ArchiveTrail.

Loads user-configurable parameters from the VAST DB offload_config table
and provides typed access. Tracks config changes with full genealogy.
"""

import json
import logging
import uuid
from datetime import datetime, timezone

import vastdb

logger = logging.getLogger("archive_trail.config")

SCHEMA = "archive/lineage"
CONFIG_TABLE = "offload_config"
CHANGE_LOG_TABLE = "config_change_log"

# Required config keys and their default types for validation
REQUIRED_KEYS = {
    "atime_threshold_days": int,
    "target_aws_bucket": str,
    "target_aws_region": str,
    "source_paths": list,
    "auto_delete_local": bool,
    "dry_run": bool,
    "batch_size": int,
    "verify_checksum": bool,
    "vast_s3_endpoint": str,
    "vast_cluster_name": str,
    "catalog_schema": str,
    "catalog_table": str,
}


class ArchiveTrailConfig:
    """Loads and provides typed access to ArchiveTrail configuration."""

    def __init__(self, session: vastdb.Session):
        self._session = session
        self._raw: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        """Load or refresh config from VAST DB."""
        rows = self._session.query(
            f'SELECT config_key, config_value FROM vast."{SCHEMA}".{CONFIG_TABLE}'
        )
        self._raw = {row["config_key"]: row["config_value"] for row in rows}
        self._validate()
        logger.info("Config loaded: %d keys", len(self._raw))

    def _validate(self) -> None:
        missing = set(REQUIRED_KEYS.keys()) - set(self._raw.keys())
        if missing:
            raise ValueError(f"Missing required config keys: {missing}")

    # -- Typed accessors --

    @property
    def atime_threshold_days(self) -> int:
        return int(self._raw["atime_threshold_days"])

    @property
    def target_aws_bucket(self) -> str:
        return self._raw["target_aws_bucket"]

    @property
    def target_aws_region(self) -> str:
        return self._raw["target_aws_region"]

    @property
    def source_paths(self) -> list[str]:
        return [p.strip() for p in self._raw["source_paths"].split(",")]

    @property
    def auto_delete_local(self) -> bool:
        return self._raw["auto_delete_local"].lower() == "true"

    @property
    def dry_run(self) -> bool:
        return self._raw["dry_run"].lower() == "true"

    @property
    def batch_size(self) -> int:
        return int(self._raw["batch_size"])

    @property
    def verify_checksum(self) -> bool:
        return self._raw["verify_checksum"].lower() == "true"

    @property
    def vast_s3_endpoint(self) -> str:
        return self._raw["vast_s3_endpoint"]

    @property
    def vast_cluster_name(self) -> str:
        return self._raw["vast_cluster_name"]

    @property
    def catalog_schema(self) -> str:
        return self._raw["catalog_schema"]

    @property
    def catalog_table(self) -> str:
        return self._raw["catalog_table"]

    def to_snapshot(self) -> str:
        """Serialize current config as JSON for embedding in lifecycle events."""
        return json.dumps(self._raw, sort_keys=True)

    def update(
        self, key: str, new_value: str, changed_by: str, reason: str
    ) -> None:
        """Update a config key with full change tracking.

        Records the old value, new value, who changed it, and why
        in the config_change_log table.
        """
        if key not in self._raw:
            raise KeyError(f"Unknown config key: {key}")

        old_value = self._raw[key]
        if old_value == new_value:
            logger.info("Config key '%s' unchanged, skipping", key)
            return

        now = datetime.now(timezone.utc)
        change_id = str(uuid.uuid4())

        # Record the change in the change log
        self._session.execute(
            f"""
            INSERT INTO vast."{SCHEMA}".{CHANGE_LOG_TABLE}
                (change_id, config_key, old_value, new_value,
                 changed_by, changed_at, change_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [change_id, key, old_value, new_value, changed_by, now, reason],
        )

        # Update the config table
        self._session.execute(
            f"""
            UPDATE vast."{SCHEMA}".{CONFIG_TABLE}
            SET config_value = ?, updated_by = ?,
                updated_at = ?, change_reason = ?
            WHERE config_key = ?
            """,
            [new_value, changed_by, now, reason, key],
        )

        self._raw[key] = new_value
        logger.info(
            "Config updated: %s = '%s' -> '%s' by %s (%s)",
            key, old_value, new_value, changed_by, reason,
        )

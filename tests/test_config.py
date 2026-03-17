"""Tests for archive_trail.config module."""

import json

from archive_trail.config import REQUIRED_KEYS


def test_required_keys_complete():
    """Verify all expected config keys are defined."""
    expected = {
        "atime_threshold_days",
        "target_aws_bucket",
        "target_aws_region",
        "source_paths",
        "auto_delete_local",
        "dry_run",
        "batch_size",
        "verify_checksum",
        "vast_s3_endpoint",
        "vast_cluster_name",
        "catalog_schema",
        "catalog_table",
    }
    assert set(REQUIRED_KEYS.keys()) == expected


def test_required_keys_types():
    """Verify expected types for each config key."""
    assert REQUIRED_KEYS["atime_threshold_days"] is int
    assert REQUIRED_KEYS["target_aws_bucket"] is str
    assert REQUIRED_KEYS["source_paths"] is list
    assert REQUIRED_KEYS["auto_delete_local"] is bool
    assert REQUIRED_KEYS["dry_run"] is bool
    assert REQUIRED_KEYS["batch_size"] is int
    assert REQUIRED_KEYS["verify_checksum"] is bool

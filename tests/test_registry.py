"""Tests for archive_trail.registry module."""

from datetime import datetime, timezone

from archive_trail.registry import CatalogEntry, RegisteredAsset


def test_catalog_entry_creation():
    now = datetime.now(timezone.utc)
    entry = CatalogEntry(
        handle="0xABC",
        parent_path="/tenant/projects/2024",
        name="report.pdf",
        extension="pdf",
        size=1024000,
        atime=now,
        mtime=now,
        ctime=now,
        login_name="jdoe",
        nfs_mode_bits=644,
    )
    assert entry.handle == "0xABC"
    assert entry.name == "report.pdf"
    assert entry.extension == "pdf"
    assert entry.size == 1024000


def test_registered_asset_creation():
    now = datetime.now(timezone.utc)
    asset = RegisteredAsset(
        element_handle="0xDEF",
        registration_id="reg-001",
        original_path="/tenant/projects/2024/report.pdf",
        original_bucket="projects",
        original_view="projects-view",
        file_name="report.pdf",
        file_extension="pdf",
        file_size_bytes=2048000,
        file_ctime=now,
        file_mtime=now,
        file_atime=now,
        owner_uid="1001",
        owner_login="jdoe",
        nfs_mode_bits=644,
        current_location="LOCAL",
        current_aws_bucket=None,
        current_aws_key=None,
        current_aws_region=None,
        registered_at=now,
        last_state_change=now,
        source_md5=None,
        destination_md5=None,
    )
    assert asset.current_location == "LOCAL"
    assert asset.current_aws_bucket is None
    assert asset.source_md5 is None


def test_registered_asset_offloaded_state():
    now = datetime.now(timezone.utc)
    asset = RegisteredAsset(
        element_handle="0xGHI",
        registration_id="reg-002",
        original_path="/tenant/media/video.mp4",
        original_bucket="media",
        original_view="media-view",
        file_name="video.mp4",
        file_extension="mp4",
        file_size_bytes=500000000,
        file_ctime=now,
        file_mtime=now,
        file_atime=now,
        owner_uid="1002",
        owner_login="editor",
        nfs_mode_bits=755,
        current_location="BOTH",
        current_aws_bucket="corp-cold-tier",
        current_aws_key="tenant/media/video.mp4",
        current_aws_region="us-east-1",
        registered_at=now,
        last_state_change=now,
        source_md5="abc123",
        destination_md5="abc123",
    )
    assert asset.current_location == "BOTH"
    assert asset.current_aws_bucket == "corp-cold-tier"
    assert asset.source_md5 == asset.destination_md5


def test_registered_asset_purged_state():
    now = datetime.now(timezone.utc)
    asset = RegisteredAsset(
        element_handle="0xJKL",
        registration_id="reg-003",
        original_path="/tenant/projects/old.xlsx",
        original_bucket="projects",
        original_view="projects-view",
        file_name="old.xlsx",
        file_extension="xlsx",
        file_size_bytes=50000,
        file_ctime=now,
        file_mtime=now,
        file_atime=now,
        owner_uid="1001",
        owner_login="jdoe",
        nfs_mode_bits=644,
        current_location="LOCAL_DELETED",
        current_aws_bucket="corp-cold-tier",
        current_aws_key="tenant/projects/old.xlsx",
        current_aws_region="us-east-1",
        registered_at=now,
        last_state_change=now,
        source_md5="def456",
        destination_md5="def456",
    )
    assert asset.current_location == "LOCAL_DELETED"
    assert asset.current_aws_bucket == "corp-cold-tier"

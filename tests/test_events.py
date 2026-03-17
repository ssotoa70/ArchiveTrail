"""Tests for archive_trail.events module."""

from datetime import datetime, timezone

from archive_trail.events import EventType, LifecycleEvent


def test_event_type_constants():
    assert EventType.REGISTERED == "REGISTERED"
    assert EventType.COPY_STARTED == "COPY_STARTED"
    assert EventType.COPY_COMPLETED == "COPY_COMPLETED"
    assert EventType.COPY_FAILED == "COPY_FAILED"
    assert EventType.CHECKSUM_VERIFIED == "CHECKSUM_VERIFIED"
    assert EventType.CHECKSUM_MISMATCH == "CHECKSUM_MISMATCH"
    assert EventType.LOCAL_DELETE_REQUESTED == "LOCAL_DELETE_REQUESTED"
    assert EventType.LOCAL_DELETED == "LOCAL_DELETED"
    assert EventType.LOCAL_DELETE_FAILED == "LOCAL_DELETE_FAILED"
    assert EventType.RECALLED == "RECALLED"
    assert EventType.CONFIG_CHANGED == "CONFIG_CHANGED"
    assert EventType.THRESHOLD_EVALUATED == "THRESHOLD_EVALUATED"
    assert EventType.SCANNED == "SCANNED"


def test_lifecycle_event_creation():
    event = LifecycleEvent(
        element_handle="0xABC123",
        registration_id="reg-001",
        event_type=EventType.REGISTERED,
        source_path="/tenant/projects/report.pdf",
    )
    assert event.element_handle == "0xABC123"
    assert event.registration_id == "reg-001"
    assert event.event_type == "REGISTERED"
    assert event.source_path == "/tenant/projects/report.pdf"
    assert event.event_id is not None
    assert event.event_timestamp is not None
    assert event.triggered_by == "SCHEDULE"


def test_lifecycle_event_defaults():
    event = LifecycleEvent(
        element_handle="0x1",
        registration_id="r1",
        event_type=EventType.SCANNED,
    )
    assert event.destination_path is None
    assert event.aws_bucket is None
    assert event.success is None
    assert event.error_message is None
    assert event.checksum_value is None
    assert event.config_snapshot is None


def test_lifecycle_event_full():
    now = datetime.now(timezone.utc)
    event = LifecycleEvent(
        element_handle="0xDEF",
        registration_id="reg-full",
        event_type=EventType.COPY_COMPLETED,
        source_path="/tenant/media/video.mp4",
        destination_path="s3://cold-tier/tenant/media/video.mp4",
        aws_bucket="cold-tier",
        aws_key="tenant/media/video.mp4",
        file_size_bytes=1024 * 1024 * 500,
        file_atime=now,
        file_mtime=now,
        pipeline_run_id="run-123",
        function_name="offload_and_track",
        triggered_by="SCHEDULE",
        success=True,
        checksum_value="abc123def456",
        config_snapshot='{"atime_threshold_days": "60"}',
    )
    assert event.file_size_bytes == 524288000
    assert event.success is True
    assert event.aws_bucket == "cold-tier"
    assert event.pipeline_run_id == "run-123"


def test_lifecycle_event_unique_ids():
    e1 = LifecycleEvent("h1", "r1", EventType.REGISTERED)
    e2 = LifecycleEvent("h1", "r1", EventType.REGISTERED)
    assert e1.event_id != e2.event_id

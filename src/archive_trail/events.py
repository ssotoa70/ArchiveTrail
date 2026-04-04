"""Lifecycle event emitter for ArchiveTrail.

Every state transition produces an append-only row in the lifecycle_events
table. This module provides the single entry point for emitting events
with full context and traceability.

Uses the vastdb PyArrow-based SDK for inserts.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pyarrow as pa

from archive_trail.db import (
    LIFECYCLE_EVENTS_SCHEMA,
    TABLE_LIFECYCLE_EVENTS,
    get_table,
)


class EventType:
    """All valid lifecycle event types."""

    REGISTERED = "REGISTERED"
    SCANNED = "SCANNED"
    THRESHOLD_EVALUATED = "THRESHOLD_EVALUATED"
    COPY_STARTED = "COPY_STARTED"
    COPY_COMPLETED = "COPY_COMPLETED"
    COPY_FAILED = "COPY_FAILED"
    CHECKSUM_VERIFIED = "CHECKSUM_VERIFIED"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    LOCAL_DELETE_REQUESTED = "LOCAL_DELETE_REQUESTED"
    LOCAL_DELETED = "LOCAL_DELETED"
    LOCAL_DELETE_FAILED = "LOCAL_DELETE_FAILED"
    RECALLED = "RECALLED"
    CONFIG_CHANGED = "CONFIG_CHANGED"


@dataclass
class LifecycleEvent:
    """A single lifecycle event with full traceability context."""

    element_handle: str
    registration_id: str
    event_type: str

    # Context
    source_path: Optional[str] = None
    destination_path: Optional[str] = None
    aws_bucket: Optional[str] = None
    aws_key: Optional[str] = None

    # Metadata snapshot
    file_size_bytes: Optional[int] = None
    file_atime: Optional[datetime] = None
    file_mtime: Optional[datetime] = None

    # Execution
    pipeline_run_id: Optional[str] = None
    function_name: Optional[str] = None
    triggered_by: str = "SCHEDULE"

    # Result
    success: Optional[bool] = None
    error_message: Optional[str] = None
    checksum_value: Optional[str] = None

    # Traceability
    config_snapshot: Optional[str] = None

    # Auto-generated
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class EventEmitter:
    """Emits lifecycle events to the VAST DB lifecycle_events table.

    Uses vastdb session with PyArrow table inserts.
    """

    def __init__(self, session, logger=None):
        self._session = session
        self._logger = logger

    def _log(self, level, msg, *args):
        if self._logger:
            getattr(self._logger, level)(msg, *args)

    def emit(self, event: LifecycleEvent) -> str:
        """Write a lifecycle event to the database. Returns the event_id."""
        row = pa.table(
            schema=LIFECYCLE_EVENTS_SCHEMA,
            data=[
                [event.event_id],
                [event.element_handle],
                [event.registration_id],
                [event.event_type],
                [event.event_timestamp],
                [event.source_path],
                [event.destination_path],
                [event.aws_bucket],
                [event.aws_key],
                [event.file_size_bytes],
                [event.file_atime],
                [event.file_mtime],
                [event.pipeline_run_id],
                [event.function_name],
                [event.triggered_by],
                [event.success],
                [event.error_message],
                [event.checksum_value],
                [event.config_snapshot],
            ],
        )
        with self._session.transaction() as tx:
            table = get_table(tx, TABLE_LIFECYCLE_EVENTS)
            table.insert(row)

        self._log(
            "info",
            "Event emitted: %s %s handle=%s",
            event.event_type,
            event.event_id,
            event.element_handle,
        )
        return event.event_id

    def emit_quick(
        self,
        element_handle: str,
        registration_id: str,
        event_type: str,
        *,
        pipeline_run_id: str = "",
        function_name: str = "",
        config_snapshot: str = "",
        source_path: Optional[str] = None,
        destination_path: Optional[str] = None,
        aws_bucket: Optional[str] = None,
        aws_key: Optional[str] = None,
        file_size_bytes: Optional[int] = None,
        file_atime: Optional[datetime] = None,
        file_mtime: Optional[datetime] = None,
        success: Optional[bool] = None,
        error_message: Optional[str] = None,
        checksum_value: Optional[str] = None,
        triggered_by: str = "SCHEDULE",
    ) -> str:
        """Convenience method for emitting events without constructing a dataclass."""
        event = LifecycleEvent(
            element_handle=element_handle,
            registration_id=registration_id,
            event_type=event_type,
            source_path=source_path,
            destination_path=destination_path,
            aws_bucket=aws_bucket,
            aws_key=aws_key,
            file_size_bytes=file_size_bytes,
            file_atime=file_atime,
            file_mtime=file_mtime,
            pipeline_run_id=pipeline_run_id,
            function_name=function_name,
            triggered_by=triggered_by,
            success=success,
            error_message=error_message,
            checksum_value=checksum_value,
            config_snapshot=config_snapshot,
        )
        return self.emit(event)

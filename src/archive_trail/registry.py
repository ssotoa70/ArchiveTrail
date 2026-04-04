"""Asset registry operations for ArchiveTrail.

Manages the master identity table (asset_registry) — one row per element,
ever. Provides registration, state updates, and query helpers.

Uses the vastdb PyArrow-based SDK for all database operations.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pyarrow as pa

from archive_trail.db import (
    ASSET_REGISTRY_SCHEMA,
    TABLE_ASSET_REGISTRY,
    get_table,
)


@dataclass
class CatalogEntry:
    """A file discovered from the VAST Catalog."""

    handle: str
    parent_path: str
    name: str
    extension: str
    size: int
    atime: datetime
    mtime: datetime
    ctime: datetime
    login_name: str
    nfs_mode_bits: int


@dataclass
class RegisteredAsset:
    """An asset that has been registered in the asset_registry."""

    element_handle: str
    registration_id: str
    original_path: str
    original_bucket: str
    original_view: str
    file_name: str
    file_extension: str
    file_size_bytes: int
    file_ctime: datetime
    file_mtime: datetime
    file_atime: datetime
    owner_uid: str
    owner_login: str
    nfs_mode_bits: int
    current_location: str
    current_aws_bucket: Optional[str]
    current_aws_key: Optional[str]
    current_aws_region: Optional[str]
    registered_at: datetime
    last_state_change: datetime
    source_md5: Optional[str]
    destination_md5: Optional[str]


class AssetRegistry:
    """Manages the asset_registry table in VAST DB."""

    def __init__(self, session, logger=None):
        self._session = session
        self._logger = logger

    def _log(self, level, msg, *args):
        if self._logger:
            getattr(self._logger, level)(msg, *args)

    def register(self, entry: CatalogEntry, bucket: str, view: str) -> str:
        """Register a new asset from a Catalog entry. Returns the registration_id."""
        reg_id = str(uuid.uuid4())
        full_path = f"{entry.parent_path}/{entry.name}"
        now = datetime.now(timezone.utc)

        row = pa.table(
            schema=ASSET_REGISTRY_SCHEMA,
            data=[
                [entry.handle],         # element_handle
                [reg_id],               # registration_id
                [full_path],            # original_path
                [bucket],               # original_bucket
                [view],                 # original_view
                [entry.name],           # file_name
                [entry.extension],      # file_extension
                [entry.size],           # file_size_bytes
                [entry.ctime],          # file_ctime
                [entry.mtime],          # file_mtime
                [entry.atime],          # file_atime
                [entry.login_name],     # owner_uid
                [entry.login_name],     # owner_login
                [entry.nfs_mode_bits],  # nfs_mode_bits
                ["LOCAL"],              # current_location
                [None],                 # current_aws_bucket
                [None],                 # current_aws_key
                [None],                 # current_aws_region
                [now],                  # registered_at
                [now],                  # last_state_change
                [None],                 # source_md5
                [None],                 # destination_md5
            ],
        )

        with self._session.transaction() as tx:
            table = get_table(tx, TABLE_ASSET_REGISTRY)
            table.insert(row)

        self._log(
            "info",
            "Registered asset: handle=%s path=%s reg_id=%s",
            entry.handle, full_path, reg_id,
        )
        return reg_id

    def update_state(
        self,
        element_handle: str,
        new_location: str,
        *,
        aws_bucket: Optional[str] = None,
        aws_key: Optional[str] = None,
        aws_region: Optional[str] = None,
        source_md5: Optional[str] = None,
        destination_md5: Optional[str] = None,
    ) -> None:
        """Update the current state of an asset.

        Reads the existing row, applies changes, and writes back.
        The vastdb SDK uses select + insert for updates (no SQL UPDATE).
        We read the current row, delete it, and insert the updated version.
        """
        now = datetime.now(timezone.utc)

        with self._session.transaction() as tx:
            table = get_table(tx, TABLE_ASSET_REGISTRY)

            # Read the current row
            predicate = pa.compute.field("element_handle") == element_handle
            result = table.select(filter=predicate)

            if result.num_rows == 0:
                self._log("warning", "Asset not found for update: %s", element_handle)
                return

            # Build updated row from current values
            row_dict = {}
            for col_name in ASSET_REGISTRY_SCHEMA.names:
                col = result.column(col_name)
                row_dict[col_name] = [col[0].as_py()]

            # Apply updates
            row_dict["current_location"] = [new_location]
            row_dict["last_state_change"] = [now]
            if aws_bucket is not None:
                row_dict["current_aws_bucket"] = [aws_bucket]
            if aws_key is not None:
                row_dict["current_aws_key"] = [aws_key]
            if aws_region is not None:
                row_dict["current_aws_region"] = [aws_region]
            if source_md5 is not None:
                row_dict["source_md5"] = [source_md5]
            if destination_md5 is not None:
                row_dict["destination_md5"] = [destination_md5]

            updated_row = pa.table(schema=ASSET_REGISTRY_SCHEMA, data=row_dict)

            # Delete old row and insert updated one
            table.delete(predicate)
            table.insert(updated_row)

        self._log("info", "Registry updated: handle=%s -> %s", element_handle, new_location)

    def is_already_offloaded(self, element_handle: str) -> bool:
        """Check if an element has already been offloaded."""
        with self._session.transaction() as tx:
            table = get_table(tx, TABLE_ASSET_REGISTRY)
            predicate = (
                (pa.compute.field("element_handle") == element_handle)
                & (
                    (pa.compute.field("current_location") == "AWS")
                    | (pa.compute.field("current_location") == "BOTH")
                    | (pa.compute.field("current_location") == "LOCAL_DELETED")
                )
            )
            result = table.select(filter=predicate, columns=["element_handle"])
            return result.num_rows > 0

    def get_offloaded_handles(self) -> set[str]:
        """Get all element handles that have been offloaded (for bulk filtering)."""
        with self._session.transaction() as tx:
            table = get_table(tx, TABLE_ASSET_REGISTRY)
            predicate = (
                (pa.compute.field("current_location") == "AWS")
                | (pa.compute.field("current_location") == "BOTH")
                | (pa.compute.field("current_location") == "LOCAL_DELETED")
            )
            result = table.select(filter=predicate, columns=["element_handle"])
            return set(result.column("element_handle").to_pylist())

    def get_pending_purge(self) -> list[RegisteredAsset]:
        """Get all assets in BOTH state (local + AWS) ready for purge."""
        with self._session.transaction() as tx:
            table = get_table(tx, TABLE_ASSET_REGISTRY)
            predicate = pa.compute.field("current_location") == "BOTH"
            result = table.select(filter=predicate)
            return [self._row_to_asset(result, i) for i in range(result.num_rows)]

    def find_by_handle(self, element_handle: str) -> Optional[RegisteredAsset]:
        """Find a single asset by element handle."""
        with self._session.transaction() as tx:
            table = get_table(tx, TABLE_ASSET_REGISTRY)
            predicate = pa.compute.field("element_handle") == element_handle
            result = table.select(filter=predicate)
            if result.num_rows == 0:
                return None
            return self._row_to_asset(result, 0)

    @staticmethod
    def _row_to_asset(table: pa.Table, index: int) -> RegisteredAsset:
        """Convert a PyArrow table row to a RegisteredAsset."""
        def _val(col_name):
            return table.column(col_name)[index].as_py()

        return RegisteredAsset(
            element_handle=_val("element_handle"),
            registration_id=_val("registration_id"),
            original_path=_val("original_path"),
            original_bucket=_val("original_bucket"),
            original_view=_val("original_view"),
            file_name=_val("file_name"),
            file_extension=_val("file_extension"),
            file_size_bytes=_val("file_size_bytes"),
            file_ctime=_val("file_ctime"),
            file_mtime=_val("file_mtime"),
            file_atime=_val("file_atime"),
            owner_uid=_val("owner_uid"),
            owner_login=_val("owner_login"),
            nfs_mode_bits=_val("nfs_mode_bits"),
            current_location=_val("current_location"),
            current_aws_bucket=_val("current_aws_bucket"),
            current_aws_key=_val("current_aws_key"),
            current_aws_region=_val("current_aws_region"),
            registered_at=_val("registered_at"),
            last_state_change=_val("last_state_change"),
            source_md5=_val("source_md5"),
            destination_md5=_val("destination_md5"),
        )

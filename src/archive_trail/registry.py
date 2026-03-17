"""Asset registry operations for ArchiveTrail.

Manages the master identity table (asset_registry) — one row per element,
ever. Provides registration, state updates, and query helpers.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import vastdb

logger = logging.getLogger("archive_trail.registry")

SCHEMA = "archive/lineage"
REGISTRY_TABLE = "asset_registry"


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

    def __init__(self, session: vastdb.Session):
        self._session = session

    def register(
        self,
        entry: CatalogEntry,
        bucket: str,
        view: str,
    ) -> str:
        """Register a new asset from a Catalog entry. Returns the registration_id."""
        reg_id = str(uuid.uuid4())
        full_path = f"{entry.parent_path}/{entry.name}"
        now = datetime.now(timezone.utc)

        self._session.execute(
            f"""
            INSERT INTO vast."{SCHEMA}".{REGISTRY_TABLE}
                (element_handle, registration_id,
                 original_path, original_bucket, original_view,
                 file_name, file_extension, file_size_bytes,
                 file_ctime, file_mtime, file_atime,
                 owner_uid, owner_login, nfs_mode_bits,
                 current_location, current_aws_bucket,
                 current_aws_key, current_aws_region,
                 registered_at, last_state_change,
                 source_md5, destination_md5)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    'LOCAL', NULL, NULL, NULL, ?, ?, NULL, NULL)
            """,
            [
                entry.handle,
                reg_id,
                full_path,
                bucket,
                view,
                entry.name,
                entry.extension,
                entry.size,
                entry.ctime,
                entry.mtime,
                entry.atime,
                entry.login_name,
                entry.login_name,
                entry.nfs_mode_bits,
                now,
                now,
            ],
        )
        logger.info(
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
        """Update the current state of an asset."""
        now = datetime.now(timezone.utc)

        set_clauses = [
            "current_location = ?",
            "last_state_change = ?",
        ]
        params: list = [new_location, now]

        if aws_bucket is not None:
            set_clauses.append("current_aws_bucket = ?")
            params.append(aws_bucket)
        if aws_key is not None:
            set_clauses.append("current_aws_key = ?")
            params.append(aws_key)
        if aws_region is not None:
            set_clauses.append("current_aws_region = ?")
            params.append(aws_region)
        if source_md5 is not None:
            set_clauses.append("source_md5 = ?")
            params.append(source_md5)
        if destination_md5 is not None:
            set_clauses.append("destination_md5 = ?")
            params.append(destination_md5)

        params.append(element_handle)

        self._session.execute(
            f"""
            UPDATE vast."{SCHEMA}".{REGISTRY_TABLE}
            SET {', '.join(set_clauses)}
            WHERE element_handle = ?
            """,
            params,
        )
        logger.info(
            "Registry updated: handle=%s -> %s", element_handle, new_location
        )

    def is_already_offloaded(self, element_handle: str) -> bool:
        """Check if an element has already been offloaded."""
        rows = self._session.query(
            f"""
            SELECT element_handle FROM vast."{SCHEMA}".{REGISTRY_TABLE}
            WHERE element_handle = ?
              AND current_location IN ('AWS', 'BOTH', 'LOCAL_DELETED')
            """,
            [element_handle],
        )
        return len(rows) > 0

    def get_pending_purge(self) -> list[RegisteredAsset]:
        """Get all assets in BOTH state (local + AWS) ready for purge."""
        rows = self._session.query(
            f"""
            SELECT * FROM vast."{SCHEMA}".{REGISTRY_TABLE}
            WHERE current_location = 'BOTH'
            """
        )
        return [self._row_to_asset(row) for row in rows]

    def find_by_path(self, path_pattern: str) -> list[RegisteredAsset]:
        """Find assets by original path pattern."""
        rows = self._session.query(
            f"""
            SELECT * FROM vast."{SCHEMA}".{REGISTRY_TABLE}
            WHERE original_path LIKE ?
            """,
            [path_pattern],
        )
        return [self._row_to_asset(row) for row in rows]

    def find_by_handle(self, element_handle: str) -> Optional[RegisteredAsset]:
        """Find a single asset by element handle."""
        rows = self._session.query(
            f"""
            SELECT * FROM vast."{SCHEMA}".{REGISTRY_TABLE}
            WHERE element_handle = ?
            """,
            [element_handle],
        )
        if not rows:
            return None
        return self._row_to_asset(rows[0])

    @staticmethod
    def _row_to_asset(row: dict) -> RegisteredAsset:
        return RegisteredAsset(
            element_handle=row["element_handle"],
            registration_id=row["registration_id"],
            original_path=row["original_path"],
            original_bucket=row["original_bucket"],
            original_view=row["original_view"],
            file_name=row["file_name"],
            file_extension=row["file_extension"],
            file_size_bytes=row["file_size_bytes"],
            file_ctime=row["file_ctime"],
            file_mtime=row["file_mtime"],
            file_atime=row["file_atime"],
            owner_uid=row["owner_uid"],
            owner_login=row["owner_login"],
            nfs_mode_bits=row["nfs_mode_bits"],
            current_location=row["current_location"],
            current_aws_bucket=row.get("current_aws_bucket"),
            current_aws_key=row.get("current_aws_key"),
            current_aws_region=row.get("current_aws_region"),
            registered_at=row["registered_at"],
            last_state_change=row["last_state_change"],
            source_md5=row.get("source_md5"),
            destination_md5=row.get("destination_md5"),
        )

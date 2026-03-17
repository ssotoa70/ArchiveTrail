"""Utility functions for ArchiveTrail.

Path manipulation, checksum computation, S3 bucket/key resolution,
and other shared helpers used across DataEngine functions.
"""

import hashlib
import io
import logging
from datetime import datetime, timezone

logger = logging.getLogger("archive_trail.helpers")


def compute_md5(data: bytes) -> str:
    """Compute MD5 hex digest for a byte payload."""
    return hashlib.md5(data).hexdigest()


def compute_md5_streaming(stream: io.IOBase, chunk_size: int = 8 * 1024 * 1024) -> str:
    """Compute MD5 hex digest from a streaming source."""
    md5 = hashlib.md5()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        md5.update(chunk)
    return md5.hexdigest()


def full_path(parent_path: str, name: str) -> str:
    """Construct full file path from parent path and file name."""
    if parent_path.endswith("/"):
        return f"{parent_path}{name}"
    return f"{parent_path}/{name}"


def bucket_from_path(path: str, path_to_bucket_map: dict[str, str]) -> str:
    """Resolve a VAST S3 bucket name from an element path.

    Uses a mapping of path prefixes to bucket names. The most specific
    (longest) matching prefix wins.
    """
    best_match = ""
    best_bucket = ""
    for prefix, bucket in path_to_bucket_map.items():
        if path.startswith(prefix) and len(prefix) > len(best_match):
            best_match = prefix
            best_bucket = bucket
    if not best_bucket:
        raise ValueError(f"No bucket mapping found for path: {path}")
    return best_bucket


def s3_key_from_path(path: str, bucket_root: str) -> str:
    """Convert a full element path to an S3 key relative to the bucket root.

    Example:
        path="/tenant/projects/2024/report.pdf"
        bucket_root="/tenant/projects"
        -> "2024/report.pdf"
    """
    if path.startswith(bucket_root):
        key = path[len(bucket_root) :]
        return key.lstrip("/")
    return path.lstrip("/")


def aws_key_from_path(path: str) -> str:
    """Generate an AWS S3 key that preserves the full original path structure.

    Strips the leading slash so the key is a valid S3 object key.
    """
    return path.lstrip("/")


def days_since(dt: datetime) -> int:
    """Calculate whole days since a given datetime."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    return delta.days


def path_list_sql(paths: list[str]) -> str:
    """Convert a list of paths to a SQL-safe comma-separated string for IN clauses.

    Example: ['/a', '/b'] -> "'/a', '/b'"
    """
    escaped = [p.replace("'", "''") for p in paths]
    return ", ".join(f"'{p}'" for p in escaped)


def path_like_clauses(paths: list[str], column: str = "c.parent_path") -> str:
    """Generate SQL OR clauses for matching paths with LIKE.

    Supports both exact matches and prefix matches (paths ending with /*).
    Example: ['/tenant/projects', '/tenant/media']
    -> "(c.parent_path = '/tenant/projects' OR c.parent_path LIKE '/tenant/projects/%'
         OR c.parent_path = '/tenant/media' OR c.parent_path LIKE '/tenant/media/%')"
    """
    clauses = []
    for path in paths:
        escaped = path.replace("'", "''")
        clauses.append(f"{column} = '{escaped}'")
        clauses.append(f"{column} LIKE '{escaped}/%'")
    return f"({' OR '.join(clauses)})"

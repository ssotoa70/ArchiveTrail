"""Tests for archive_trail.helpers module."""

from datetime import datetime, timedelta, timezone

from archive_trail.helpers import (
    aws_key_from_path,
    compute_md5,
    days_since,
    full_path,
    path_like_clauses,
    path_list_sql,
    s3_key_from_path,
)


def test_compute_md5():
    data = b"hello world"
    result = compute_md5(data)
    assert result == "5eb63bbbe01eeed093cb22bb8f5acdc3"


def test_compute_md5_empty():
    result = compute_md5(b"")
    assert result == "d41d8cd98f00b204e9800998ecf8427e"


def test_full_path_no_trailing_slash():
    assert full_path("/tenant/projects", "report.pdf") == "/tenant/projects/report.pdf"


def test_full_path_trailing_slash():
    assert full_path("/tenant/projects/", "report.pdf") == "/tenant/projects/report.pdf"


def test_s3_key_from_path():
    path = "/tenant/projects/2024/report.pdf"
    bucket_root = "/tenant/projects"
    assert s3_key_from_path(path, bucket_root) == "2024/report.pdf"


def test_s3_key_from_path_no_match():
    path = "/other/path/file.txt"
    bucket_root = "/tenant/projects"
    assert s3_key_from_path(path, bucket_root) == "other/path/file.txt"


def test_aws_key_from_path():
    assert aws_key_from_path("/tenant/projects/report.pdf") == "tenant/projects/report.pdf"


def test_aws_key_from_path_no_slash():
    assert aws_key_from_path("already/clean.txt") == "already/clean.txt"


def test_days_since_recent():
    now = datetime.now(timezone.utc)
    dt = now - timedelta(days=5)
    assert days_since(dt) == 5


def test_days_since_old():
    now = datetime.now(timezone.utc)
    dt = now - timedelta(days=90)
    assert days_since(dt) == 90


def test_days_since_naive_datetime():
    now = datetime.now(timezone.utc)
    dt = (now - timedelta(days=30)).replace(tzinfo=None)
    assert days_since(dt) == 30


def test_path_list_sql():
    result = path_list_sql(["/tenant/projects", "/tenant/media"])
    assert result == "'/tenant/projects', '/tenant/media'"


def test_path_list_sql_escapes_quotes():
    result = path_list_sql(["/tenant/o'brien"])
    assert result == "'/tenant/o''brien'"


def test_path_like_clauses():
    result = path_like_clauses(["/tenant/projects"])
    assert "c.parent_path = '/tenant/projects'" in result
    assert "c.parent_path LIKE '/tenant/projects/%'" in result


def test_path_like_clauses_multiple():
    result = path_like_clauses(["/a", "/b"])
    assert "/a" in result
    assert "/b" in result
    assert " OR " in result


def test_path_like_clauses_custom_column():
    result = path_like_clauses(["/x"], column="t.path")
    assert "t.path = '/x'" in result

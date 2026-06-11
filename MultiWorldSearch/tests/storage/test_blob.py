"""Tests for blob store."""

import tempfile
from pathlib import Path

from mws.storage.blob import BlobStore


def test_put_and_get() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        bs = BlobStore(base_path=Path(tmpdir))
        bs.put("test.bin", b"hello world")
        assert bs.get("test.bin") == b"hello world"


def test_exists_and_delete() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        bs = BlobStore(base_path=Path(tmpdir))
        bs.put("x.dat", b"data")
        assert bs.exists("x.dat")
        bs.delete("x.dat")
        assert not bs.exists("x.dat")


def test_get_missing() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        bs = BlobStore(base_path=Path(tmpdir))
        assert bs.get("nonexistent") is None

"""Blob store for heavy data (images, pointclouds, tensors)."""

from __future__ import annotations

import json
from pathlib import Path

from mws.core.logging import get_logger

logger = get_logger(__name__)


class BlobStore:
    """Local filesystem blob store for heavy payloads."""

    def __init__(self, base_path: Path | None = None) -> None:
        self._base = base_path or Path("./data/blobs")
        self._base.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes, metadata: dict | None = None) -> str:
        """Store binary data. Returns the storage key."""
        path = self._base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if metadata:
            meta_path = path.with_suffix(path.suffix + ".meta.json")
            meta_path.write_text(json.dumps(metadata))
        return key

    def get(self, key: str) -> bytes | None:
        """Retrieve binary data by key."""
        path = self._base / key
        if path.exists():
            return path.read_bytes()
        return None

    def exists(self, key: str) -> bool:
        return (self._base / key).exists()

    def delete(self, key: str) -> None:
        path = self._base / key
        if path.exists():
            path.unlink()
        meta = path.with_suffix(path.suffix + ".meta.json")
        if meta.exists():
            meta.unlink()

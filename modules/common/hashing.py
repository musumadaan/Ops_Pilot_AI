"""Hashing helpers for provenance (source-document hashes, duplicate keys)."""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(*parts) -> str:
    """Order-sensitive hash of normalised parts (used for duplicate-invoice keys)."""
    norm = "\x1f".join("" if p is None else str(p).strip().lower() for p in parts)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]

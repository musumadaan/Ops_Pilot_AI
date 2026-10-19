"""Corpus integrity: a manifest of SHA-256 hashes for every approved document.

A retrieval system that cites whatever is in its folder will happily cite a poisoned file. With a manifest, a new,
modified or removed document fails loudly at load time and must be approved (re-signed) by a person.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST = "MANIFEST.json"


class IntegrityError(Exception):
    pass


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_manifest(directory: Path) -> dict[str, str]:
    m = {p.name: _sha(p) for p in sorted(directory.glob("*.md"))}
    (directory / MANIFEST).write_text(json.dumps(m, indent=2, sort_keys=True))
    return m


def verify_manifest(directory: Path) -> None:
    mp = directory / MANIFEST
    if not mp.exists():
        raise IntegrityError(f"{MANIFEST} missing in {directory}")
    approved = json.loads(mp.read_text())
    actual = {p.name: _sha(p) for p in directory.glob("*.md")}
    problems = ([f"unapproved document: {n}" for n in sorted(set(actual) - set(approved))]
                + [f"missing document: {n}" for n in sorted(set(approved) - set(actual))]
                + [f"modified document: {n}" for n in sorted(n for n in set(actual) & set(approved) if actual[n] != approved[n])])
    if problems:
        raise IntegrityError("; ".join(problems))

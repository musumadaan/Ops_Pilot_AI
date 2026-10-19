"""Minimal `key: value` front-matter parser (avoids a PyYAML dependency for flat metadata)."""
from __future__ import annotations


def parse(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"bad front matter line: {line!r}")
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out

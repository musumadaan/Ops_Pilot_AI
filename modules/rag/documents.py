"""Load markdown policy documents into section-aware chunks with metadata and character spans."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import yaml_lite

MAX_WORDS = 110


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    access: str
    version: str
    section: str
    text: str
    start: int            # character offsets in the source file body
    end: int

    @property
    def context(self) -> str:
        return f"{self.title}. {self.section}. {self.text}"


def parse_front_matter(raw: str) -> tuple[dict[str, str], str, int]:
    m = re.match(r"^---\n(.*?)\n---\n", raw, flags=re.DOTALL)
    if not m:
        raise ValueError("document has no front matter")
    meta = yaml_lite.parse(m.group(1))
    for k in ("doc_id", "title", "access", "version"):
        if k not in meta:
            raise ValueError(f"front matter missing '{k}'")
    return meta, raw[m.end():], m.end()


def _split_long(text: str, base: int) -> list[tuple[str, int, int]]:
    """Split on sentence boundaries into <= MAX_WORDS windows with one sentence of overlap."""
    sents = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[^.!?]+[.!?]?", text) if m.group(0).strip()]
    out, cur = [], []
    for s in sents:
        cur.append(s)
        if sum(len(x[0].split()) for x in cur) >= MAX_WORDS:
            out.append((" ".join(x[0].strip() for x in cur), base + cur[0][1], base + cur[-1][2]))
            cur = cur[-1:]
    if len(cur) > 1 or not out:
        out.append((" ".join(x[0].strip() for x in cur), base + cur[0][1], base + cur[-1][2]))
    return out


def load_document(path: Path) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8")
    meta, body, offset = parse_front_matter(raw)
    chunks: list[Chunk] = []
    heads = list(re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(body)
        text_start = h.end()
        seg = body[text_start:end]
        stripped = seg.strip()
        if not stripped:
            continue
        lead = len(seg) - len(seg.lstrip())
        base = offset + text_start + lead
        words = len(stripped.split())
        pieces = [(stripped, base, base + len(stripped))] if words <= MAX_WORDS else _split_long(stripped, base)
        for j, (t, s, e) in enumerate(pieces):
            chunks.append(Chunk(f"{meta['doc_id']}#{i}.{j}", meta["doc_id"], meta["title"], meta["access"], meta["version"],
                                h.group(1), t, s, e))
    return chunks


def load_corpus(directory: str | Path, verify: bool = False) -> list[Chunk]:
    """``verify=True`` requires a valid MANIFEST.json (see integrity.py) and rejects unapproved or modified files."""
    if verify:
        from .integrity import verify_manifest
        verify_manifest(Path(directory))
    chunks: list[Chunk] = []
    for p in sorted(Path(directory).glob("*.md")):
        chunks.extend(load_document(p))
    if not chunks:
        raise ValueError(f"no documents found in {directory}")
    return chunks

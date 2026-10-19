"""Minimal tracing: spans with duration and attributes written as JSON lines (OpenTelemetry-shaped field names).

The blueprint names OpenTelemetry; it is not installed here. Field names (trace_id, span_id, name, start/end, attributes)
follow its data model so records can be forwarded to a collector later.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class Tracer:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.spans: list[dict[str, Any]] = []
        self.trace_id = uuid.uuid4().hex

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        rec: dict[str, Any] = {"trace_id": self.trace_id, "span_id": uuid.uuid4().hex[:16], "name": name,
                               "start": time.time(), "attributes": dict(attrs), "status": "ok"}
        try:
            yield rec["attributes"]
        except Exception as exc:
            rec["status"] = "error"
            rec["attributes"]["exception"] = type(exc).__name__
            raise
        finally:
            rec["end"] = time.time()
            rec["duration_ms"] = round((rec["end"] - rec["start"]) * 1000, 2)
            self.spans.append(rec)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as f:
                    f.write(json.dumps(rec, default=str) + "\n")

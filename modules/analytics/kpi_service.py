"""Parameterised, read-only KPI execution with provenance.

This is the only SQL surface the agent layer (Workflow 5) may call: named templates with typed,
validated parameters. The model never writes SQL, so it cannot invent a query or change data.

    from modules.analytics.kpi_service import run_kpi
    res = run_kpi(engine, "invoice_cycle_time", {"start_date": "2026-01-01", "end_date": "2026-03-31"})
    res.rows, res.provenance        # exact numbers + which query, parameters and time produced them
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from .db import SQL_DIR

KPI_DIR = SQL_DIR / "kpi"
_HEADER = re.compile(r"^--\s*(\w+):\s*(.*)$")
_TYPES = {"date", "int", "float", "text"}
MAX_TOP_N = 1000


class KpiError(Exception):
    """Base class for KPI service errors."""


class UnknownKpi(KpiError):
    pass


class KpiParamError(KpiError):
    pass


@dataclass(frozen=True)
class KpiSpec:
    kpi_id: str
    name: str
    description: str
    params: dict[str, str]
    grain: str
    sql: str
    sql_sha256: str
    engines: tuple[str, ...] = ("postgres",)


@dataclass
class KpiResult:
    kpi_id: str
    columns: list[str]
    rows: list[dict[str, Any]]
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        def conv(v: Any) -> Any:
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, (date, datetime)):
                return v.isoformat()
            return v
        return {"kpi_id": self.kpi_id, "columns": self.columns,
                "rows": [{k: conv(v) for k, v in r.items()}for r in self.rows], "provenance": self.provenance}


def _parse(path: Path) -> KpiSpec:
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body: list[str] = []
    in_header = True
    for line in raw.splitlines():
        if in_header:
            m = _HEADER.match(line)
            if m:
                meta[m.group(1)] = m.group(2).strip()
            elif not line.strip() or line.lstrip().startswith("--"):
                # Keep reading metadata across multiline comment descriptions.
                continue
            else:
                in_header = False
                body.append(line)
        else:
            body.append(line)
    sql = "\n".join(body).strip()
    params: dict[str, str] = {}
    for item in filter(None, (p.strip() for p in meta.get("params", "").split(","))):
        name, _, typ = item.partition(":")
        if typ not in _TYPES:
            raise KpiError(f"{path.name}: bad param type '{item}'")
        params[name] = typ
    declared = set(re.findall(r"(?<!:):(\w+)", sql))
    if declared != set(params):
        raise KpiError(f"{path.name}: SQL binds {sorted(declared)} but header declares {sorted(params)}")
    if meta.get("kpi") != path.stem:
        raise KpiError(f"{path.name}: header kpi id must equal the file name")
    return KpiSpec(path.stem, meta.get("name", path.stem), meta.get("description", ""), params,
                   meta.get("grain", ""), sql, hashlib.sha256(sql.encode()).hexdigest(),
                   tuple(e.strip() for e in meta.get("engines", "postgres").split(",")))


@lru_cache(maxsize=1)
def registry() -> dict[str, KpiSpec]:
    return {p.stem: _parse(p) for p in sorted(KPI_DIR.glob("*.sql"))}


def list_kpis() -> list[dict[str, Any]]:
    return [{"kpi_id": s.kpi_id, "name": s.name, "description": s.description, "params": s.params, "grain": s.grain,
             "engines": list(s.engines)}
            for s in registry().values()]


def _coerce(name: str, typ: str, value: Any) -> Any:
    try:
        if typ == "date":
            if isinstance(value, datetime):
                return value.date()
            return value if isinstance(value, date) else date.fromisoformat(str(value))
        if typ == "int":
            if isinstance(value, bool):
                raise ValueError("bool")
            iv = int(value)
            if not 1 <= iv <= MAX_TOP_N:
                raise ValueError(f"must be 1..{MAX_TOP_N}")
            return iv
        if typ == "float":
            return float(value)
        return str(value)
    except (ValueError, TypeError) as exc:
        raise KpiParamError(f"parameter '{name}' is not a valid {typ}: {exc}") from exc


def validate_params(spec: KpiSpec, params: dict[str, Any]) -> dict[str, Any]:
    unknown = set(params) - set(spec.params)
    missing = set(spec.params) - set(params)
    if unknown:
        raise KpiParamError(f"unknown parameter(s) for {spec.kpi_id}: {sorted(unknown)}")
    if missing:
        raise KpiParamError(f"missing parameter(s) for {spec.kpi_id}: {sorted(missing)}")
    out = {k: _coerce(k, spec.params[k], v) for k, v in params.items()}
    if "start_date" in out and "end_date" in out and out["start_date"] > out["end_date"]:
        raise KpiParamError("start_date must be on or before end_date")
    return out


def run_kpi(engine: Engine, kpi_id: str, params: dict[str, Any] | None = None, timeout_ms: int = 10_000) -> KpiResult:
    spec = registry().get(kpi_id)
    if spec is None:
        raise UnknownKpi(f"unknown KPI '{kpi_id}'. Available: {sorted(registry())}")
    bound = validate_params(spec, params or {})
    t0 = time.perf_counter()
    with engine.connect() as conn:
        with conn.begin():
            conn.execute(text("SET TRANSACTION READ ONLY"))                  # cannot write even by mistake
            conn.execute(text(f"SET LOCAL statement_timeout = {int(timeout_ms)}"))
            result = conn.execute(text(spec.sql), bound)
            cols = list(result.keys())
            rows = [dict(zip(cols, r)) for r in result.fetchall()]
    return KpiResult(kpi_id, cols, rows, {
        "kpi_id": kpi_id, "sql_sha256": spec.sql_sha256, "params": {k: str(v) for k, v in bound.items()},
        "executed_at": datetime.now(UTC).isoformat(timespec="seconds"), "row_count": len(rows),
        "duration_ms": round((time.perf_counter() - t0) * 1000, 1), "engine": engine.dialect.name,
    })


def record_evidence(engine: Engine, case_id: str, result: KpiResult, evidence_id: str) -> None:
    """Attach a KPI result to an investigation case so the answer can cite the exact query that produced it."""
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO ops.case_evidence (evidence_id, case_id, kind, reference, sha256) "
            "VALUES (:e, :c, 'sql', :ref, :h)"),
            {"e": evidence_id, "c": case_id,
             "ref": f"{result.kpi_id}?{result.provenance['params']}", "h": result.provenance["sql_sha256"]})

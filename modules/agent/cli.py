"""Run one investigation from the command line against the local stack (Postgres + synthetic CSVs + mock ERP in-process).

    DATABASE_URL=postgresql+psycopg://... python -m modules.agent.cli INV-000004 --role analyst
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from fastapi.testclient import TestClient

from modules.analytics.db import get_engine
from modules.erp.app import create_app
from modules.erp.client import ErpClient
from modules.rag.corpus import write_corpus
from modules.rag.documents import load_corpus
from modules.rag.retriever import Retriever

from .backend_live import LiveBackend, make_anomaly_fn
from .workflow import InvestigationWorkflow


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("invoice_id")
    ap.add_argument("--role", default="analyst")
    ap.add_argument("--user", default="cli_user")
    ap.add_argument("--data", default="datasets/synthetic/procurement")
    ap.add_argument("--policies", default="datasets/synthetic/policies")
    ap.add_argument("--model-root", default="models/registry")
    ap.add_argument("--llm", choices=["template", "anthropic"], default="template")
    a = ap.parse_args()
    if not Path(a.policies).exists():
        write_corpus(Path(a.policies))
    http = TestClient(create_app(a.data))
    anomaly = None
    try:
        from modules.ml import registry
        bundle, meta = registry.load("invoice_anomaly", root=Path(a.model_root))
        anomaly = make_anomaly_fn(bundle, meta["version"], a.data)
    except FileNotFoundError:
        pass                                                 # no model trained: the case will record a gap
    backend = LiveBackend(get_engine(), ErpClient(http, "read-token"), ErpClient(http, "write-token"),
                          Retriever(load_corpus(a.policies)), anomaly)
    llm = None
    if a.llm == "anthropic":
        from .llm import AnthropicLlm
        llm = AnthropicLlm()
    case = InvestigationWorkflow(backend, llm).run(a.invoice_id, a.user, a.role)
    print(json.dumps({"case_id": case.case_id, "state": case.state.value, "recommendation": case.recommendation, "risk": case.risk,
                      "narrative": case.narrative, "gaps": case.gaps, "actions": [asdict(x) for x in case.actions], "usage": case.usage}, indent=2))


if __name__ == "__main__":
    main()

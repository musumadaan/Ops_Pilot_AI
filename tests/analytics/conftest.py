"""Postgres fixtures. Tests run on a throw-away database whose name MUST end in ``_test``.

    OPSPILOT_TEST_DATABASE_URL=postgresql+psycopg://user:pw@127.0.0.1:5432/opspilot_test pytest tests/analytics

The database is created if missing and the ``ops`` schema is rebuilt from the migrations on every
session. If Postgres is unreachable the tests are skipped (not failed).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "postgresql+psycopg://opspilot:opspilot@127.0.0.1:5432/opspilot_test"


def _test_url():
    url = make_url(os.environ.get("OPSPILOT_TEST_DATABASE_URL", DEFAULT_URL))
    if not (url.database or "").endswith("_test"):
        raise RuntimeError(f"refusing to reset database '{url.database}': name must end with _test")
    return url


@pytest.fixture(scope="session")
def csv_dir(tmp_path_factory) -> Path:
    from modules.analytics.synthetic import Generator
    out = tmp_path_factory.mktemp("proc")
    Generator(ROOT / "datasets/synthetic/suppliers.csv", 42).write(out)
    return out


@pytest.fixture(scope="session")
def engine(csv_dir):
    url = _test_url()
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            if not c.execute(text("SELECT 1 FROM pg_database WHERE datname=:d"), {"d": url.database}).first():
                c.execute(text(f'CREATE DATABASE "{url.database}" ENCODING UTF8 TEMPLATE template0'))
    except Exception as exc:
        pytest.skip(f"Postgres not reachable: {exc}")
    eng = create_engine(url, future=True)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS ops CASCADE"))
        c.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "modules/analytics/migrations"))
    cfg.attributes["url"] = url.render_as_string(hide_password=False)
    command.upgrade(cfg, "head")
    from modules.analytics.loader import load_directory
    load_directory(eng, csv_dir, validate=True, replace=True)
    eng.alembic_cfg = cfg  # type: ignore[attr-defined]
    yield eng
    eng.dispose()

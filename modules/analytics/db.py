"""Engine factory and SQL-file helpers."""
from __future__ import annotations

from functools import cache
from pathlib import Path

from sqlalchemy import Engine, create_engine

from modules.common.settings import database_url

SQL_DIR = Path(__file__).parent / "sql"


def get_engine(url: str | None = None, **kw) -> Engine:
    """New engine. ``pool_pre_ping`` avoids stale connections after laptop sleep."""
    return create_engine(url or database_url(), pool_pre_ping=True, future=True, **kw)


@cache
def read_sql(relative: str) -> str:
    """Read a file under modules/analytics/sql (e.g. 'schema/001_core.sql')."""
    return (SQL_DIR / relative).read_text(encoding="utf-8")


def sql_files(subdir: str) -> list[str]:
    """Sorted relative paths of the .sql files in a subdirectory (numeric prefixes set the order)."""
    return sorted(f"{subdir}/{p.name}" for p in (SQL_DIR / subdir).glob("*.sql"))


def run_script(bind, sql: str) -> None:
    """Execute a multi-statement SQL script on a SQLAlchemy connection.

    psycopg 3 parses ``%`` as a parameter marker even with no parameters, so literal percent
    signs (``format('%s')``, comments) are doubled first.
    """
    bind.exec_driver_sql(sql.replace("%", "%%"))

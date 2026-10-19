"""Drop and rebuild the ``ops`` schema in a DEVELOPMENT database (refuses names containing 'prod')."""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.analytics.db import get_engine


def main() -> None:
    eng = get_engine()
    name = eng.url.database or ""
    if "prod" in name.lower():
        sys.exit(f"refusing to reset '{name}'")
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA IF EXISTS ops CASCADE"))
        c.execute(text("DROP TABLE IF EXISTS alembic_version"))
    print(f"dropped ops schema in '{name}'")


if __name__ == "__main__":
    main()

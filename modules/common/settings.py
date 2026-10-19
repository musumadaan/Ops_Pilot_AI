"""Environment-driven settings (no secrets in code; copy .env.example to .env locally)."""
from __future__ import annotations

import os

DEFAULT_DATABASE_URL = "postgresql+psycopg://opspilot:opspilot@localhost:5432/opspilot"


def database_url() -> str:
    """SQLAlchemy URL. Plain ``postgresql://`` is upgraded to the psycopg 3 driver."""
    url = os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

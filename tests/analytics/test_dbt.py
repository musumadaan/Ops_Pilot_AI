import os
import shutil
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

pytestmark = [pytest.mark.db, pytest.mark.dbt]
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("dbt") is None, reason="dbt not installed")
def test_dbt_build_passes(engine, tmp_path):
    u = make_url(engine.url.render_as_string(hide_password=False))
    env = {**os.environ, "PGHOST": u.host, "PGPORT": str(u.port or 5432), "PGUSER": u.username,
           "PGPASSWORD": u.password or "", "PGDATABASE": u.database, "DBT_PROFILES_DIR": str(ROOT / "dbt")}
    prof = ROOT / "dbt/profiles.yml"
    created = not prof.exists()
    if created:
        prof.write_text((ROOT / "dbt/profiles.yml.example").read_text())
    try:
        p = subprocess.run(["dbt", "build", "--no-use-colors", "--target-path", str(tmp_path / "target")],
                           cwd=ROOT / "dbt", env=env, capture_output=True, text=True)
    finally:
        if created:
            prof.unlink()
    assert p.returncode == 0, p.stdout[-3000:]
    assert "ERROR=0" in p.stdout

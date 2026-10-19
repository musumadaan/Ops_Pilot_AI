# OpsPilot AI - common tasks.  Needs a UTF8 Postgres database (see .env.example).
PY ?= python3
export DBT_PROFILES_DIR ?= $(CURDIR)/dbt

.PHONY: help db migrate seed dq kpis dbt lint test test-fast gate reset explain

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | tr '\n' ' '; echo

db:            ## create the databases (needs createdb on PATH)
	createdb -E UTF8 -T template0 opspilot || true
	createdb -E UTF8 -T template0 opspilot_test || true

migrate:       ## apply Alembic migrations
	$(PY) -m alembic upgrade head

seed:          ## generate synthetic data, validate with Pandera, bulk-load
	$(PY) scripts/generate_master_data.py
	$(PY) scripts/load_synthetic.py

dq:            ## data-quality rules (non-zero exit on error-level failures)
	$(PY) -m modules.data_quality.run

kpis:          ## list the KPI catalogue
	$(PY) -c "import json; from modules.analytics.kpi_service import list_kpis; print(json.dumps(list_kpis(), indent=2))"

dbt:           ## build + test dbt models (copies profiles.yml.example if needed)
	test -f dbt/profiles.yml || cp dbt/profiles.yml.example dbt/profiles.yml
	cd dbt && dbt build

lint:          ## SQL + Python lint
	sqlfluff lint modules/analytics/sql/kpi modules/analytics/sql/views
	ruff check modules scripts tests

test:          ## full test suite
	$(PY) -m pytest -q

test-fast:     ## without the database / dbt tests
	$(PY) -m pytest -q -m "not db and not dbt"

explain:       ## before/after EXPLAIN ANALYZE demo
	$(PY) scripts/explain_demo.py

gate:          ## everything that must be green before a change merges
	$(MAKE) lint test dq
	$(PY) -m modules.qa.release

reset:         ## DANGER: drop the ops schema and rebuild it (dev database only)
	$(PY) scripts/reset_dev_db.py
	$(MAKE) migrate seed
# Makefile (optional)
demo:   ## Streamlit showcase
	streamlit run app/streamlit_app.py

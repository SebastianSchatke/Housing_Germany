"""
Deploy the housing_germany package and the methodology procedures to
HOUSING_DEV.MARKET_INSIGHTS_AGENTS.

The package is zipped and uploaded as housing_germany_<commit>.zip so every
procedure version points at exact code. Refuses to deploy from a dirty tree.

Usage: python scripts/deploy_procedures.py [--connection aviv-prod] [--allow-dirty]
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile
import zipfile

from snowflake.snowpark import Session

REPO = pathlib.Path(__file__).resolve().parents[1]
SCHEMA = "HOUSING_DEV.MARKET_INSIGHTS_AGENTS"
PUBLISHED = "HOUSING_DEV.MARKET_INSIGHTS_STREAMLIT_APPS"
STAGE = f"{SCHEMA}.CODE"
PACKAGES = "'snowflake-snowpark-python', 'pandas', 'numpy'"
# Owns the agent schema and inherits R_HOUSING_RW_DATA_ACCESS_DEV (owner of the
# published dashboard tables), so it can read SILVER_PROD and write those tables.
# R_HOUSING_RW_DATA_ACCESS_DEV alone cannot read SILVER_PROD.
AGENTS_OWNER_ROLE = "SNF_R_HOUSING_DATA_ANALYST"

REFRESH_OBJECTS = {
    "REFRESH_PUBLISHED_TABLES": """
CREATE OR REPLACE PROCEDURE {schema}.REFRESH_PUBLISHED_TABLES()
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ({packages})
IMPORTS = ('{zip}')
HANDLER = 'handler'
COMMENT = 'Rebuild INDEX_SERIES and MARKET_SUMMARY as _NEXT, check, INSERT OVERWRITE; then drift probe vs index_growth v1. Code commit {sha}.'
EXECUTE AS OWNER
AS $$
from housing_germany.methodology.refresh import refresh_all

def handler(session):
    return refresh_all(session)
$$""",
    "TASK_REFRESH_METHODOLOGIES": """
CREATE OR REPLACE TASK {schema}.TASK_REFRESH_METHODOLOGIES
  WAREHOUSE = WH_HOUSING_DEV  -- owner role has direct USAGE (tasks ignore secondary roles)
  SCHEDULE = 'USING CRON 0 12 2 * * Europe/Berlin'
  SUSPEND_TASK_AFTER_NUM_FAILURES = 2
  COMMENT = 'Monthly checked refresh of dashboard tables (replaces TASK_REFRESH_INDEX_SERIES / _MARKET_SUMMARY). Code commit {sha}.'
AS CALL {schema}.REFRESH_PUBLISHED_TABLES()""",
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def build_zip(dest: pathlib.Path) -> None:
    src = REPO / "src"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((src / "housing_germany").rglob("*")):
            if path.suffix in (".py", ".sql"):
                zf.write(path, path.relative_to(src))


PROCEDURES = {
    "RUN_METHODOLOGY": """
CREATE OR REPLACE PROCEDURE {schema}.RUN_METHODOLOGY(
    METHODOLOGY_ID VARCHAR, PARAMS VARIANT, VERSION NUMBER DEFAULT NULL)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ({packages})
IMPORTS = ('{zip}')
HANDLER = 'handler'
COMMENT = 'Run an approved housing methodology. Validates params, serves cached results, checks, logs. Code commit {sha}.'
EXECUTE AS CALLER
AS $$
import json
from housing_germany.methodology import run, RequestError

def handler(session, methodology_id, params, version):
    params = json.loads(params) if isinstance(params, str) else (params or {{}})
    try:
        return run(session, methodology_id, params, version=version)
    except RequestError as e:
        return {{"status": "rejected", "methodology": methodology_id, "error": str(e)}}
$$""",
    # Agent-facing wrappers: Cortex Agent generic tools on a warehouse only accept
    # scalar arguments (no OBJECT/ARRAY), so params are passed as flat strings.
    "AGENT_RUN_METHODOLOGY": """
CREATE OR REPLACE PROCEDURE {schema}.AGENT_RUN_METHODOLOGY(
    METHODOLOGY_ID VARCHAR, GEOGRAPHY VARCHAR,
    PERIOD VARCHAR DEFAULT NULL, ITEM VARCHAR DEFAULT NULL, TRANSACTION_TYPE VARCHAR DEFAULT NULL,
    HOUSEHOLD VARCHAR DEFAULT NULL, REALTOR_FEE VARCHAR DEFAULT NULL)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ({packages})
IMPORTS = ('{zip}')
HANDLER = 'handler'
COMMENT = 'Flat-argument wrapper of RUN_METHODOLOGY for the HOUSING_ANALYST agent. Code commit {sha}.'
EXECUTE AS CALLER
AS $$
from housing_germany.methodology import run, RequestError

def handler(session, methodology_id, geography, period, item, transaction_type, household, realtor_fee):
    ids = [g.strip() for g in (geography or "").split(",") if g.strip()]
    params = {{"geography": ids[0] if len(ids) == 1 else ids}}
    for key, value in (("window", period), ("item", item), ("transaction", transaction_type),
                       ("household", household), ("realtor_fee", realtor_fee)):
        if value not in (None, ""):
            params[key] = value
    try:
        return run(session, methodology_id, params)
    except RequestError as e:
        return {{"status": "rejected", "methodology": methodology_id, "params": params, "error": str(e)}}
$$""",
    "AGENT_REPORT_GAP": """
CREATE OR REPLACE PROCEDURE {schema}.AGENT_REPORT_GAP(
    REQUEST VARCHAR, MISSING VARCHAR, CANDIDATE_SOURCES VARCHAR DEFAULT NULL)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ({packages})
IMPORTS = ('{zip}')
HANDLER = 'handler'
COMMENT = 'Flat-argument wrapper of REPORT_GAP for the HOUSING_ANALYST agent. Code commit {sha}.'
EXECUTE AS CALLER
AS $$
from housing_germany.methodology import report_gap

def handler(session, request, missing, candidate_sources):
    sources = [s.strip() for s in (candidate_sources or "").split(",") if s.strip()]
    return report_gap(session, request, missing, sources)
$$""",
    "REPORT_GAP": """
CREATE OR REPLACE PROCEDURE {schema}.REPORT_GAP(
    REQUEST VARCHAR, MISSING VARCHAR, CANDIDATE_SOURCES ARRAY DEFAULT NULL, DRAFT_SPEC VARIANT DEFAULT NULL)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.11'
PACKAGES = ({packages})
IMPORTS = ('{zip}')
HANDLER = 'handler'
COMMENT = 'Log a request that no approved methodology covers. Never computes a number. Code commit {sha}.'
EXECUTE AS CALLER
AS $$
import json
from housing_germany.methodology import report_gap

def handler(session, request, missing, candidate_sources, draft_spec):
    if isinstance(draft_spec, str):
        draft_spec = json.loads(draft_spec)
    return report_gap(session, request, missing, list(candidate_sources or []), draft_spec)
$$""",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--connection", default="aviv-prod")
    ap.add_argument("--allow-dirty", action="store_true")
    args = ap.parse_args()
    if git("status", "--porcelain", "--", "src") and not args.allow_dirty:
        sys.exit("src/ has uncommitted changes; commit first (or --allow-dirty).")
    sha = git("rev-parse", "--short", "HEAD")

    session = Session.builder.config("connection_name", args.connection).create()
    session.sql(f"USE ROLE {AGENTS_OWNER_ROLE}").collect()
    session.sql(f"CREATE STAGE IF NOT EXISTS {STAGE} COMMENT = 'Versioned housing_germany code zips'").collect()
    with tempfile.TemporaryDirectory() as tmp:
        zpath = pathlib.Path(tmp) / f"housing_germany_{sha}.zip"
        build_zip(zpath)
        session.file.put(str(zpath), f"@{STAGE}", auto_compress=False, overwrite=True)
    zip_ref = f"@{STAGE}/housing_germany_{sha}.zip"
    for name, ddl in PROCEDURES.items():
        session.sql(ddl.format(schema=SCHEMA, packages=PACKAGES, zip=zip_ref, sha=sha)).collect()
        print(f"deployed {SCHEMA}.{name} -> {zip_ref}")

    for name, ddl in REFRESH_OBJECTS.items():
        session.sql(ddl.format(schema=SCHEMA, packages=PACKAGES, zip=zip_ref, sha=sha)).collect()
        print(f"deployed {SCHEMA}.{name}")
    session.close()


if __name__ == "__main__":
    main()

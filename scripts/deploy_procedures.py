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
STAGE = f"{SCHEMA}.CODE"
PACKAGES = "'snowflake-snowpark-python', 'pandas', 'numpy'"


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def build_zip(dest: pathlib.Path) -> None:
    src = REPO / "src"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((src / "housing_germany").rglob("*.py")):
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
    session.sql(f"CREATE STAGE IF NOT EXISTS {STAGE} COMMENT = 'Versioned housing_germany code zips'").collect()
    with tempfile.TemporaryDirectory() as tmp:
        zpath = pathlib.Path(tmp) / f"housing_germany_{sha}.zip"
        build_zip(zpath)
        session.file.put(str(zpath), f"@{STAGE}", auto_compress=False, overwrite=True)
    zip_ref = f"@{STAGE}/housing_germany_{sha}.zip"
    for name, ddl in PROCEDURES.items():
        session.sql(ddl.format(schema=SCHEMA, packages=PACKAGES, zip=zip_ref, sha=sha)).collect()
        print(f"deployed {SCHEMA}.{name} -> {zip_ref}")
    session.close()


if __name__ == "__main__":
    main()

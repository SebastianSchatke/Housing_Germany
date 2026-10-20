"""
Sync methodologies/*/v*.yaml into HOUSING_DEV.MARKET_INSIGHTS_AGENTS.METHODOLOGY_REGISTRY.

Records the current git commit so every run can be traced to the exact spec.
Refuses to sync from a dirty working tree, since the commit hash would not
describe what was loaded.

Usage: python scripts/sync_methodologies.py [--connection aviv-prod] [--allow-dirty]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

import yaml
from snowflake.snowpark import Session

REPO = pathlib.Path(__file__).resolve().parents[1]
TABLE = "HOUSING_DEV.MARKET_INSIGHTS_AGENTS.METHODOLOGY_REGISTRY"
REQUIRED = ("id", "version", "status", "inputs", "sources", "implementation", "outputs", "checks")
STATUSES = {"draft", "approved", "deprecated"}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def load_specs() -> list[tuple[str, dict]]:
    specs = []
    for path in sorted(REPO.glob("methodologies/*/v*.yaml")):
        spec = yaml.safe_load(path.read_text())
        missing = [k for k in REQUIRED if k not in spec]
        if missing:
            sys.exit(f"{path}: missing keys {missing}")
        if spec["status"] not in STATUSES:
            sys.exit(f"{path}: invalid status {spec['status']!r}")
        if f"v{spec['version']}.yaml" != path.name or spec["id"] != path.parent.name:
            sys.exit(f"{path}: id/version do not match the file path")
        if spec["status"] == "approved" and spec.get("open_questions"):
            print(f"warning: {path} is approved but has open_questions")
        specs.append((str(path.relative_to(REPO)), spec))
    return specs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--connection", default="aviv-prod")
    ap.add_argument("--allow-dirty", action="store_true")
    args = ap.parse_args()

    if git("status", "--porcelain", "--", "methodologies") and not args.allow_dirty:
        sys.exit("methodologies/ has uncommitted changes; commit first (or --allow-dirty).")
    sha = git("rev-parse", "HEAD")
    specs = load_specs()

    session = Session.builder.config("connection_name", args.connection).create()
    for spec_path, spec in specs:
        session.sql(
            f"""
            MERGE INTO {TABLE} t
            USING (SELECT ? AS ID, ? AS VERSION, ? AS STATUS, ? AS TITLE,
                          PARSE_JSON(?) AS SPEC, ? AS SPEC_PATH, ? AS COMMIT_SHA) s
            ON t.METHODOLOGY_ID = s.ID AND t.VERSION = s.VERSION
            WHEN MATCHED THEN UPDATE SET STATUS = s.STATUS, TITLE = s.TITLE, SPEC = s.SPEC,
                 SPEC_PATH = s.SPEC_PATH, COMMIT_SHA = s.COMMIT_SHA, LOADED_AT = CURRENT_TIMESTAMP()
            WHEN NOT MATCHED THEN INSERT (METHODOLOGY_ID, VERSION, STATUS, TITLE, SPEC, SPEC_PATH, COMMIT_SHA)
                 VALUES (s.ID, s.VERSION, s.STATUS, s.TITLE, s.SPEC, s.SPEC_PATH, s.COMMIT_SHA)
            """,
            params=[spec["id"], spec["version"], spec["status"], spec.get("title"),
                    json.dumps(spec, default=str), spec_path, sha],
        ).collect()
        print(f"synced {spec['id']} v{spec['version']} ({spec['status']})")
    session.close()
    print(f"{len(specs)} specs synced at {sha[:7]}")


if __name__ == "__main__":
    main()

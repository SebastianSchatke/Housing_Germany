"""
Methodology execution: validate a request against its approved spec, serve
reproducible results from the run log, otherwise compute, check and log.

Every official number the orchestrator agent returns goes through run().
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid

import numpy as np
import pandas as pd

from housing_germany.indices.functions import (
    build_index_series,
    load_housing_weights,
    load_indices,
    variation_windows,
)

SCHEMA = "HOUSING_DEV.MARKET_INSIGHTS_AGENTS"
MAX_ATTEMPTS = 2  # one automatic rerun, then flag


class RequestError(ValueError):
    """The request does not fit the methodology spec."""


# ---------------------------------------------------------------------------
# Spec handling
# ---------------------------------------------------------------------------

def load_spec(session, methodology_id: str, version: int | None = None) -> dict:
    """Approved spec (latest version unless pinned) plus its commit SHA."""
    if version is None:
        rows = session.sql(
            f"SELECT VERSION, SPEC, COMMIT_SHA FROM {SCHEMA}.APPROVED_METHODOLOGIES WHERE METHODOLOGY_ID = ?",
            params=[methodology_id],
        ).collect()
    else:
        rows = session.sql(
            f"""SELECT VERSION, SPEC, COMMIT_SHA FROM {SCHEMA}.METHODOLOGY_REGISTRY
                WHERE METHODOLOGY_ID = ? AND VERSION = ? AND STATUS = 'approved'""",
            params=[methodology_id, int(version)],
        ).collect()
    if not rows:
        raise RequestError(f"No approved methodology '{methodology_id}'"
                           + (f" v{version}" if version else ""))
    spec = json.loads(rows[0]["SPEC"])
    spec["_commit_sha"] = rows[0]["COMMIT_SHA"]
    return spec


def canonical_params(spec: dict, params: dict) -> dict:
    """Apply defaults, reject unknown keys and disallowed values. Output is sorted and stable."""
    inputs = spec["inputs"]
    unknown = set(params) - set(inputs)
    if unknown:
        raise RequestError(f"Unknown parameters {sorted(unknown)}; allowed: {sorted(inputs)}")
    out = {}
    for name, rule in inputs.items():
        value = params.get(name, rule.get("default"))
        if value is None:
            if rule.get("required"):
                raise RequestError(f"Missing required parameter '{name}'")
            continue
        if name == "geography":
            ids = [value] if isinstance(value, str) else list(value)
            levels = set(rule.get("levels", []))
            bad = [g for g in ids if g[:4] not in levels or g[4:6] != "DE"]
            if bad:
                raise RequestError(f"Geography {bad} not in levels {sorted(levels)} for Germany")
            if len({g[:4] for g in ids}) > 1:
                raise RequestError("All geography ids must be the same level")
            value = sorted(ids)
        elif "allowed" in rule and value not in rule["allowed"]:
            raise RequestError(f"'{name}'={value!r} not allowed; choose from {rule['allowed']}")
        out[name] = value
    return dict(sorted(out.items()))


def request_hash(methodology_id: str, version: int, params: dict, snapshot: str) -> str:
    payload = json.dumps([methodology_id, version, params, snapshot], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Methodology implementations (each returns result dict + long-format rows)
# ---------------------------------------------------------------------------

def _index_snapshot(session, params: dict) -> str:
    ids = params["geography"]
    row = session.sql(
        f"""SELECT TO_VARCHAR(MAX(RECORD_TS)) AS TS FROM SILVER_PROD.MKKN_INDICE.INDICE_DE
            WHERE METRIC = 'METRIC.SQM_PRICE' AND TRANSACTION_TYPE = ?
              AND PLACE_ID IN ({', '.join('?' for _ in ids)})""",
        params=[params["transaction"], *ids],
    ).collect()[0]
    return row["TS"] or "none"


def _run_index_growth(session, params: dict):
    ids, place_type = params["geography"], params["geography"][0][:4]
    weights = load_housing_weights(session, place_type)
    weights = {k: v for k, v in weights.items() if k in ids}
    indices = load_indices(session, place_type, params["transaction"], place_ids=ids)
    series = build_index_series(indices, params["item"], ids, stock_weights=weights)
    if series.empty:
        raise RequestError(f"No index data for {ids} ({params['item']}, {params['transaction']})")
    windows = variation_windows(series)
    value = windows[params["window"]]
    result = {
        "value": None if pd.isna(value) else float(value),
        "unit": "fraction",
        "window": params["window"],
        "as_of": series.index.max().strftime("%Y-%m-%d"),
        "all_windows": {k: (None if pd.isna(v) else float(v)) for k, v in windows.items()},
        "places": ids,
    }
    rows = [
        {"PLACE_ID": ",".join(ids), "ITEM_TYPE": params["item"],
         "TRANSACTION_TYPE": params["transaction"], "PERIOD_DATE": d.date(),
         "METRIC": "index", "VALUE": float(v)}
        for d, v in series.items()
    ]
    return result, rows


def _purchasing_power_snapshot(session, params: dict) -> str:
    row = session.sql(
        """SELECT TO_VARCHAR(MAX(RATE_DATE)) AS D
           FROM HOUSING_DEV.MARKET_INSIGHTS_STREAMLIT_APPS.INTEREST_RATES"""
    ).collect()[0]
    return f"rates:{row['D']}"


def _run_purchasing_power(session, params: dict):
    col = {
        ("single", "with"): "PP_SQM_SINGLE_W_REALTOR",
        ("family", "with"): "PP_SQM_FAMILY_W_REALTOR",
        ("single", "without"): "PP_SQM_SINGLE_NO_REALTOR",
        ("family", "without"): "PP_SQM_FAMILY_NO_REALTOR",
    }[(params["household"], params["realtor_fee"])]
    ids = params["geography"]
    item_key = {"house": "ITEM_TYPE.HOUSE", "apartment": "ITEM_TYPE.APARTMENT",
                "hybrid": "ITEM_TYPE.HYBRID"}[params["item"]]
    df = session.sql(
        f"""SELECT PLACE_ID, {col} AS PP_SQM, PRICE_SQM, ANNUAL_RATE, TAX_RATE
            FROM HOUSING_DEV.MARKET_INSIGHTS_STREAMLIT_APPS.PURCHASING_POWER
            WHERE ITEM_TYPE = ? AND PLACE_ID IN ({', '.join('?' for _ in ids)})
            ORDER BY PLACE_ID""",
        params=[item_key, *ids],
    ).to_pandas()
    if df.empty:
        raise RequestError(f"No purchasing power data for {ids} ({params['item']})")
    per_place = {r.PLACE_ID: float(r.PP_SQM) for r in df.itertuples() if pd.notna(r.PP_SQM)}
    result = {
        "value": per_place[ids[0]] if len(ids) == 1 and ids[0] in per_place else None,
        "unit": "m2",
        "per_place": per_place,
        "assumptions": {"annual_rate": float(df.ANNUAL_RATE.iloc[0])},
        "places": ids,
    }
    rows = [{"PLACE_ID": pid, "ITEM_TYPE": params["item"], "TRANSACTION_TYPE": "TRANSACTION_TYPE.SELL",
             "PERIOD_DATE": None, "METRIC": "purchasing_power_sqm", "VALUE": v}
            for pid, v in per_place.items()]
    return result, rows


IMPLEMENTATIONS = {
    "index_growth": (_index_snapshot, _run_index_growth),
    "purchasing_power": (_purchasing_power_snapshot, _run_purchasing_power),
}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def run_checks(spec: dict, params: dict, result: dict) -> list[dict]:
    """Per-request checks from the spec. Table-level checks run in the refresh pipeline."""
    out = []
    window_col = f"VAR_{params['window'].upper()}" if "window" in params else None
    values = list(result.get("per_place", {}).values()) or [result.get("value")]
    for check in spec.get("checks", []):
        kind = check["type"]
        if kind == "not_empty":
            ok = any(v is not None for v in values)
            out.append({"check": "not_empty", "passed": ok})
        elif kind == "range":
            col = check["column"]
            if col.startswith("VAR_") and col != window_col:
                continue
            bad = [v for v in values if v is not None and not (check["min"] <= v <= check["max"])]
            out.append({"check": f"range:{col}", "passed": not bad,
                        "detail": {"min": check["min"], "max": check["max"], "out_of_range": bad}})
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(session, methodology_id: str, params: dict | None = None,
        version: int | None = None, use_cache: bool = True) -> dict:
    """Validate, serve from cache or compute, check (one rerun), log. Returns a response dict."""
    if methodology_id not in IMPLEMENTATIONS:
        raise RequestError(f"Methodology '{methodology_id}' has no executable implementation")
    spec = load_spec(session, methodology_id, version)
    version = int(spec["version"])
    params = canonical_params(spec, params or {})
    snapshot_fn, run_fn = IMPLEMENTATIONS[methodology_id]
    snapshot = snapshot_fn(session, params)
    rhash = request_hash(methodology_id, version, params, snapshot)

    if use_cache:
        cached = session.sql(
            f"""SELECT RUN_ID, RESULT, CHECK_RESULTS FROM {SCHEMA}.RUN_LOG
                WHERE REQUEST_HASH = ? AND STATUS IN ('passed', 'rerun_passed')
                ORDER BY FINISHED_AT DESC LIMIT 1""",
            params=[rhash],
        ).collect()
        if cached:
            return {"status": "cached", "run_id": cached[0]["RUN_ID"], "request_hash": rhash,
                    "methodology": methodology_id, "version": version, "params": params,
                    "data_snapshot": snapshot, "commit_sha": spec["_commit_sha"],
                    "result": json.loads(cached[0]["RESULT"]),
                    "checks": json.loads(cached[0]["CHECK_RESULTS"] or "[]")}

    run_id = str(uuid.uuid4())
    tag = f"housing_methodology:{methodology_id}:v{version}:{run_id}"
    session.sql(f"ALTER SESSION SET QUERY_TAG = '{tag}'").collect()
    started = time.time()
    result, rows, checks, attempt = None, [], [], 0
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            result, rows = run_fn(session, params)
            checks = run_checks(spec, params, result)
            if all(c["passed"] for c in checks):
                break
    finally:
        session.sql("ALTER SESSION UNSET QUERY_TAG").collect()
    passed = all(c["passed"] for c in checks)
    status = ("passed" if attempt == 1 else "rerun_passed") if passed else "flagged"

    session.sql(
        f"""INSERT INTO {SCHEMA}.RUN_LOG
            (RUN_ID, REQUEST_HASH, METHODOLOGY_ID, VERSION, COMMIT_SHA, PARAMS, DATA_SNAPSHOT,
             STATUS, CHECK_RESULTS, RESULT, ATTEMPT, FINISHED_AT, RUNTIME_SECONDS, QUERY_TAG)
            SELECT ?, ?, ?, ?, ?, PARSE_JSON(?), ?, ?, PARSE_JSON(?), PARSE_JSON(?), ?,
                   CURRENT_TIMESTAMP(), ?, ?""",
        params=[run_id, rhash, methodology_id, version, spec["_commit_sha"], json.dumps(params),
                snapshot, status, json.dumps(checks, default=str), json.dumps(result, default=str),
                attempt, round(time.time() - started, 3), tag],
    ).collect()
    if rows:
        frame = pd.DataFrame(rows).assign(RUN_ID=run_id)
        session.write_pandas(frame, "RUN_RESULTS", database="HOUSING_DEV",
                             schema="MARKET_INSIGHTS_AGENTS", auto_create_table=False,
                             quote_identifiers=True, use_logical_type=True)
    if not passed:
        session.sql(
            f"""INSERT INTO {SCHEMA}.ISSUES (SEVERITY, KIND, METHODOLOGY_ID, VERSION, RUN_ID, MESSAGE, DETAILS)
                SELECT 'error', 'check_failed', ?, ?, ?, ?, PARSE_JSON(?)""",
            params=[methodology_id, version, run_id,
                    f"{methodology_id} v{version} failed checks after {attempt} attempts",
                    json.dumps({"params": params, "checks": checks}, default=str)],
        ).collect()

    return {"status": status, "run_id": run_id, "request_hash": rhash,
            "methodology": methodology_id, "version": version, "params": params,
            "data_snapshot": snapshot, "commit_sha": spec["_commit_sha"],
            "result": result if passed else None, "checks": checks}


def report_gap(session, request: str, missing: str, candidate_sources: list[str] | None = None,
               draft_spec: dict | None = None) -> dict:
    """Record a request no approved methodology covers. Never computes anything."""
    issue_id = str(uuid.uuid4())
    session.sql(
        f"""INSERT INTO {SCHEMA}.ISSUES (ISSUE_ID, SEVERITY, KIND, MESSAGE, DETAILS)
            SELECT ?, 'info', 'gap', ?, PARSE_JSON(?)""",
        params=[issue_id, f"No methodology for: {request}",
                json.dumps({"request": request, "missing": missing,
                            "candidate_sources": candidate_sources or [],
                            "draft_spec": draft_spec}, default=str)],
    ).collect()
    return {"status": "gap_recorded", "issue_id": issue_id,
            "message": "No approved methodology covers this request. It was logged for review."}

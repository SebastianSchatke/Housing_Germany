"""
Refresh pipeline for the tables the dashboard reads.

Each table is built as <TABLE>_NEXT, checked, and published with INSERT
OVERWRITE only if the checks pass (one automatic rebuild before flagging). A failed refresh leaves
the published table untouched and writes an ISSUES row.

After MARKET_SUMMARY is refreshed, a drift probe compares its SQL windows with
the canonical package implementation (index_growth v1) for a fixed set of
places, so the decision to switch formulas can be made on data.
"""
from __future__ import annotations

import json
import time
import uuid
from importlib import resources

import pandas as pd

from housing_germany.indices.functions import (
    build_index_series,
    load_housing_weights,
    load_indices,
    variation_windows,
)
from housing_germany.indices.hybrid import hybrid_from_long

PUBLISHED = "HOUSING_DEV.MARKET_INSIGHTS_STREAMLIT_APPS"
AGENTS = "HOUSING_DEV.MARKET_INSIGHTS_AGENTS"
HYBRID_CHUNK = 1500  # places per pandas batch when computing hybrid

# Table-level checks. min_rows_ratio guards against a partial source load.
TABLES = [
    {
        "table": "INDEX_SERIES",
        "sql": "index_series.sql",
        "methodology": "index_growth",
        # INDICE_DE no longer publishes ITEM_TYPE.HYBRID (2026-10): hybrid is computed
        # from house + apartment with the index_growth v1 formula.
        "computed_hybrid": True,
        "checks": [
            {"type": "min_rows_ratio", "ratio": 0.95},
            {"type": "segments_present", "columns": ["PLACE_TYPE", "TRANSACTION_TYPE", "ITEM_TYPE"]},
            {"type": "no_nulls", "columns": ["PLACE_ID", "INDEX_DATE", "INDEX_VALUE"]},
            {"type": "range", "column": "INDEX_VALUE", "min": 1, "max": 1000},
            {"type": "not_older_than_previous", "column": "INDEX_DATE"},
        ],
    },
    {
        "table": "MARKET_SUMMARY",
        "sql": "market_summary.sql",
        "methodology": "index_growth",
        "checks": [
            {"type": "min_rows_ratio", "ratio": 0.95},
            {"type": "range", "column": "VAR_1Y", "min": -0.5, "max": 1.0, "max_violation_share": 0.01},
            {"type": "range", "column": "VAR_5Y", "min": -0.6, "max": 1.5, "max_violation_share": 0.01},
            {"type": "completeness", "column": "VAR_1Y", "min_share_non_null": 0.9},
            {"type": "not_older_than_previous", "column": "LATEST_DATE"},
        ],
    },
]

DRIFT_PLACES = ["AD02DE1"] + [f"AD04DE{i}" for i in range(1, 17)]
DRIFT_WINDOWS = ["1y", "5y"]
DRIFT_TOLERANCE = 0.001  # 0.1 percentage points


def _sql_file(name: str) -> str:
    text = resources.files("housing_germany.methodology").joinpath("sql", name).read_text()
    return "\n".join(l for l in text.splitlines() if not l.startswith("--"))


def _scalar(session, sql: str, params=None):
    return session.sql(sql, params=params or []).collect()[0][0]


def _table_exists(session, fqn: str) -> bool:
    db, schema, name = fqn.split(".")
    return bool(session.sql(
        f"SELECT COUNT(*) FROM {db}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
        params=[schema, name],
    ).collect()[0][0])


def run_table_checks(session, candidate: str, published: str, checks: list[dict]) -> list[dict]:
    results = []
    has_previous = _table_exists(session, published)
    for c in checks:
        kind = c["type"]
        if kind == "min_rows_ratio":
            new = _scalar(session, f"SELECT COUNT(*) FROM {candidate}")
            old = _scalar(session, f"SELECT COUNT(*) FROM {published}") if has_previous else 0
            ok = new > 0 and (old == 0 or new >= c["ratio"] * old)
            results.append({"check": kind, "passed": ok, "detail": {"rows": new, "previous_rows": old}})
        elif kind == "no_nulls":
            expr = " OR ".join(f"{col} IS NULL" for col in c["columns"])
            n = _scalar(session, f"SELECT COUNT(*) FROM {candidate} WHERE {expr}")
            results.append({"check": f"no_nulls:{','.join(c['columns'])}", "passed": n == 0,
                            "detail": {"null_rows": n}})
        elif kind == "range":
            col = c["column"]
            total, bad = session.sql(
                f"""SELECT COUNT({col}), COUNT_IF({col} < ? OR {col} > ?) FROM {candidate}""",
                params=[c["min"], c["max"]],
            ).collect()[0]
            share = bad / total if total else 0.0
            ok = share <= c.get("max_violation_share", 0.0)
            results.append({"check": f"range:{col}", "passed": ok,
                            "detail": {"violations": bad, "share": round(share, 6)}})
        elif kind == "completeness":
            col = c["column"]
            share = _scalar(session, f"SELECT COUNT({col}) / NULLIF(COUNT(*), 0) FROM {candidate}") or 0
            results.append({"check": f"completeness:{col}", "passed": float(share) >= c["min_share_non_null"],
                            "detail": {"share_non_null": float(share)}})
        elif kind == "segments_present":
            keys = ", ".join(c["columns"])
            missing = session.sql(
                f"""SELECT {keys} FROM (SELECT DISTINCT {keys} FROM {published})
                    EXCEPT SELECT DISTINCT {keys} FROM {candidate}"""
            ).collect() if has_previous else []
            results.append({"check": f"segments_present:{keys}", "passed": not missing,
                            "detail": {"missing": ["/".join(str(v) for v in r) for r in missing]}})
        elif kind == "not_older_than_previous":
            col = c["column"]
            new = _scalar(session, f"SELECT MAX({col}) FROM {candidate}")
            old = _scalar(session, f"SELECT MAX({col}) FROM {published}") if has_previous else None
            ok = new is not None and (old is None or new >= old)
            results.append({"check": f"not_older_than_previous:{col}", "passed": ok,
                            "detail": {"max": str(new), "previous_max": str(old)}})
    return results


def _log(session, run_id, methodology, status, checks, result, attempt, started, tag, snapshot):
    version, sha = session.sql(
        f"SELECT VERSION, COMMIT_SHA FROM {AGENTS}.APPROVED_METHODOLOGIES WHERE METHODOLOGY_ID = ?",
        params=[methodology],
    ).collect()[0]
    session.sql(
        f"""INSERT INTO {AGENTS}.RUN_LOG
            (RUN_ID, REQUEST_HASH, METHODOLOGY_ID, VERSION, COMMIT_SHA, PARAMS, DATA_SNAPSHOT, STATUS,
             CHECK_RESULTS, RESULT, ATTEMPT, STARTED_AT, FINISHED_AT, RUNTIME_SECONDS, QUERY_TAG)
            SELECT ?, ?, ?, ?, ?, PARSE_JSON(?), ?, ?, PARSE_JSON(?), PARSE_JSON(?), ?,
                   CONVERT_TIMEZONE('UTC', TO_TIMESTAMP_NTZ(?::NUMBER(38, 3)))::TIMESTAMP_LTZ, CURRENT_TIMESTAMP(), ?, ?""",
        params=[run_id, f"refresh:{run_id}", methodology, int(version), sha,
                json.dumps({"refresh": result.get("table")}), snapshot, status,
                json.dumps(checks, default=str), json.dumps(result, default=str), attempt,
                started, round(time.time() - started, 3), tag],
    ).collect()
    return int(version)


def _issue(session, severity, kind, methodology, version, run_id, message, details):
    session.sql(
        f"""INSERT INTO {AGENTS}.ISSUES (SEVERITY, KIND, METHODOLOGY_ID, VERSION, RUN_ID, MESSAGE, DETAILS)
            SELECT ?, ?, ?, ?, ?, ?, PARSE_JSON(?)""",
        params=[severity, kind, methodology, version, run_id, message, json.dumps(details, default=str)],
    ).collect()


def add_computed_hybrid(session, candidate: str) -> dict:
    """Replace source HYBRID rows in the candidate with hybrid computed from house + apartment."""
    db, schema, table = candidate.split(".")
    session.sql(f"DELETE FROM {candidate} WHERE ITEM_TYPE = 'ITEM_TYPE.HYBRID'").collect()
    item_map = {"ITEM_TYPE.HOUSE": "house", "ITEM_TYPE.APARTMENT": "apartment"}
    written = 0
    for (place_type,) in session.sql(f"SELECT DISTINCT PLACE_TYPE FROM {candidate} ORDER BY 1").collect():
        weights = load_housing_weights(session, place_type)
        places = [r[0] for r in session.sql(
            f"SELECT DISTINCT PLACE_ID FROM {candidate} WHERE PLACE_TYPE = ? ORDER BY 1", params=[place_type]
        ).collect()]
        for i in range(0, len(places), HYBRID_CHUNK):
            chunk = places[i:i + HYBRID_CHUNK]
            long_df = session.sql(
                f"""SELECT PLACE_ID, TRANSACTION_TYPE, ITEM_TYPE, INDEX_DATE, INDEX_VALUE, VOLUME
                    FROM {candidate}
                    WHERE PLACE_TYPE = ? AND ITEM_TYPE IN ('ITEM_TYPE.HOUSE', 'ITEM_TYPE.APARTMENT')
                      AND PLACE_ID IN ({', '.join('?' for _ in chunk)})""",
                params=[place_type, *chunk],
            ).to_pandas()
            if long_df.empty:
                continue
            long_df["item"] = long_df["ITEM_TYPE"].map(item_map)
            hyb = hybrid_from_long(long_df, weights)
            hyb.insert(1, "PLACE_TYPE", place_type)
            hyb.insert(3, "ITEM_TYPE", "ITEM_TYPE.HYBRID")
            hyb = hyb[["PLACE_ID", "PLACE_TYPE", "TRANSACTION_TYPE", "ITEM_TYPE",
                       "INDEX_DATE", "INDEX_VALUE", "VOLUME"]]
            session.write_pandas(hyb, table, database=db, schema=schema, auto_create_table=False,
                                 quote_identifiers=True, use_logical_type=True)
            written += len(hyb)
    return {"hybrid_rows": written}


def refresh_table(session, cfg: dict, max_attempts: int = 2) -> dict:
    published = f"{PUBLISHED}.{cfg['table']}"
    candidate = f"{published}_NEXT"
    run_id, started = str(uuid.uuid4()), time.time()
    # Owner's-rights procedures can't ALTER SESSION, so no QUERY_TAG here; cost is
    # attributed via the run's STARTED_AT/FINISHED_AT window in QUERY_HISTORY.
    tag = None
    build_sql = _sql_file(cfg["sql"]).replace("{target}", candidate)
    checks, attempt, extra = [], 0, {}
    for attempt in range(1, max_attempts + 1):
        session.sql(build_sql).collect()
        if cfg.get("computed_hybrid"):
            extra = add_computed_hybrid(session, candidate)
        checks = run_table_checks(session, candidate, published, cfg["checks"])
        if all(c["passed"] for c in checks):
            break
    passed = all(c["passed"] for c in checks)
    snapshot = None
    if passed:
        date_col = "INDEX_DATE" if cfg["table"] == "INDEX_SERIES" else "LATEST_DATE"
        snapshot = str(_scalar(session, f"SELECT MAX({date_col}) FROM {candidate}"))
        if _table_exists(session, published):
            # INSERT OVERWRITE is atomic and keeps the published table's owner and
            # grants, so dashboard roles keep access (CREATE OR REPLACE / SWAP would not).
            session.sql(f"INSERT OVERWRITE INTO {published} SELECT * FROM {candidate}").collect()
            session.sql(f"DROP TABLE IF EXISTS {candidate}").collect()
        else:
            session.sql(f"ALTER TABLE {candidate} RENAME TO {published}").collect()

    status = ("passed" if attempt == 1 else "rerun_passed") if passed else "flagged"
    result = {"table": cfg["table"], "published": passed, "data_snapshot": snapshot, **extra}
    version = _log(session, run_id, cfg["methodology"], status, checks, result, attempt, started, tag, snapshot)
    if not passed:
        _issue(session, "error", "check_failed", cfg["methodology"], version, run_id,
               f"Refresh of {cfg['table']} failed checks after {attempt} attempts; published table kept, "
               f"candidate left as {candidate} for inspection", {"checks": checks})
    return {"table": cfg["table"], "status": status, "run_id": run_id, "checks": checks}


def drift_probe(session) -> dict:
    """Compare MARKET_SUMMARY (SQL) with index_growth v1 (package) for fixed places."""
    rows = []
    for item, key in (("hybrid", "ITEM_TYPE.HYBRID"), ("house", "ITEM_TYPE.HOUSE"),
                      ("apartment", "ITEM_TYPE.APARTMENT")):
        for level in ("AD02", "AD04"):
            ids = [p for p in DRIFT_PLACES if p.startswith(level)]
            weights = load_housing_weights(session, level)
            idx = load_indices(session, level, "TRANSACTION_TYPE.SELL", place_ids=ids)
            summary = session.sql(
                f"""SELECT PLACE_ID, VAR_1Y, VAR_5Y FROM {PUBLISHED}.MARKET_SUMMARY
                    WHERE TRANSACTION_TYPE = 'TRANSACTION_TYPE.SELL' AND ITEM_TYPE = ?
                      AND PLACE_ID IN ({', '.join('?' for _ in ids)})""",
                params=[key, *ids],
            ).to_pandas().set_index("PLACE_ID")
            for pid in ids:
                series = build_index_series(idx, item, [pid], stock_weights={pid: weights.get(pid, {})})
                if series.empty or pid not in summary.index:
                    continue
                windows = variation_windows(series)
                for w in DRIFT_WINDOWS:
                    sql_v = summary.loc[pid, f"VAR_{w.upper()}"]
                    pkg_v = windows[w]
                    if pd.isna(sql_v) or pd.isna(pkg_v):
                        continue
                    rows.append({"place_id": pid, "item": item, "window": w,
                                 "sql": float(sql_v), "package": float(pkg_v),
                                 "abs_diff": abs(float(sql_v) - float(pkg_v))})
    df = pd.DataFrame(rows)
    summary = {
        "compared": len(df),
        "max_abs_diff": float(df.abs_diff.max()) if len(df) else None,
        "share_over_tolerance": float((df.abs_diff > DRIFT_TOLERANCE).mean()) if len(df) else None,
        "by_item": (df.groupby("item").abs_diff.max().round(6).to_dict() if len(df) else {}),
        "worst": (df.sort_values("abs_diff", ascending=False).head(5).to_dict("records") if len(df) else []),
    }
    if len(df) and summary["share_over_tolerance"] > 0:
        _issue(session, "warning", "drift", "index_growth", None, None,
               f"MARKET_SUMMARY differs from index_growth v1 by up to {summary['max_abs_diff']:.4f} "
               f"({summary['share_over_tolerance']:.0%} of {len(df)} comparisons over {DRIFT_TOLERANCE})",
               summary)
    return summary


def refresh_all(session) -> dict:
    out = {"tables": [], "drift": None}
    for cfg in TABLES:
        res = refresh_table(session, cfg)
        out["tables"].append(res)
        if res["status"] == "flagged":
            break  # downstream tables depend on this one
    if all(t["status"] != "flagged" for t in out["tables"]):
        out["drift"] = drift_probe(session)
    return out

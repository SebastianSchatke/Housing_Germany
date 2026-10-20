"""
Parity: hybrid_from_long (vectorised, used by the refresh pipeline) must equal
build_index_series(item="hybrid") (the canonical per-place implementation).

Uses live INDICE_DE data for Germany, all 16 states and a sample of districts.
Run: python tests/test_hybrid_parity.py
"""
import os
import sys

import pandas as pd
from snowflake.snowpark import Session

from housing_germany.indices.functions import build_index_series, load_housing_weights, load_indices
from housing_germany.indices.hybrid import hybrid_from_long

TOL = 1e-9

session = Session.builder.config(
    "connection_name", os.environ.get("SNOWFLAKE_CONNECTION_NAME", "aviv-prod")
).create()
session.sql("USE WAREHOUSE WH_PROD_PERSONA").collect()

worst, compared, failures = 0.0, 0, []
for level, limit in (("AD02", None), ("AD04", None), ("AD06", 40)):
    weights = load_housing_weights(session, level)
    for txn in ("TRANSACTION_TYPE.SELL", "TRANSACTION_TYPE.RENT"):
        idx = load_indices(session, level, txn)
        idx = idx[idx["item"].isin(["house", "apartment"])]
        places = sorted(idx["PLACE_ID"].unique())[:limit]
        idx = idx[idx["PLACE_ID"].isin(places)]
        long_df = pd.DataFrame([
            {"PLACE_ID": r.PLACE_ID, "TRANSACTION_TYPE": txn, "item": r.item,
             "INDEX_DATE": d, "INDEX_VALUE": float(v), "VOLUME": float(vol)}
            for r in idx.itertuples() for d, v, vol in zip(r.DATES, r.VALUES, r.VOLUMES)
        ])
        fast = hybrid_from_long(long_df, weights)
        for pid in places:
            ref = build_index_series(idx, "hybrid", [pid], stock_weights=weights)
            got = fast[fast.PLACE_ID == pid].set_index(pd.to_datetime(fast[fast.PLACE_ID == pid].INDEX_DATE))["INDEX_VALUE"]
            if ref.empty and got.empty:
                continue
            ref.index = pd.to_datetime(ref.index)
            if not ref.index.equals(got.index):
                failures.append((level, txn, pid, "dates differ"))
                continue
            diff = float((ref - got).abs().max())
            worst, compared = max(worst, diff), compared + 1
            if diff > TOL:
                failures.append((level, txn, pid, diff))

session.close()
print(f"compared {compared} place/transaction series, max abs diff {worst:.3e}")
for f in failures[:10]:
    print("FAIL", f)
print("PASS" if not failures else f"{len(failures)} FAILURES")
sys.exit(1 if failures else 0)

"""
Vectorised hybrid index for many places at once.

Same maths as build_index_series(item="hybrid") for a single place, applied to
a long-format frame so the refresh pipeline can compute ~23k place/transaction
series in one pass. Parity with build_index_series is asserted in
tests/test_hybrid_parity.py.

Per place and transaction:
  1. reference date = latest of the house / apartment series start dates
  2. each series rebased to 100 at its first observation on or after that date
  3. weighted mean with housing stock counts (fallback when missing or 0: last transaction volume),
     divided by the total weight of all usable series (missing dates count as 0)
  4. first observation pinned to 100
"""
from __future__ import annotations

import numpy as np
import pandas as pd

KEYS = ["PLACE_ID", "TRANSACTION_TYPE"]


def hybrid_from_long(long_df: pd.DataFrame, stock_weights: dict | None = None) -> pd.DataFrame:
    """
    long_df columns: PLACE_ID, TRANSACTION_TYPE, item ('house'|'apartment'),
                     INDEX_DATE, INDEX_VALUE, VOLUME
    stock_weights:   {place_id: {'apartment': n, 'house': n}} as from load_housing_weights

    Returns PLACE_ID, TRANSACTION_TYPE, INDEX_DATE, INDEX_VALUE, VOLUME (sum of item volumes).
    """
    df = long_df[long_df["item"].isin(["house", "apartment"])].copy()
    if df.empty:
        return pd.DataFrame(columns=KEYS + ["INDEX_DATE", "INDEX_VALUE", "VOLUME"])
    df["INDEX_DATE"] = pd.to_datetime(df["INDEX_DATE"])
    df = df.sort_values(KEYS + ["item", "INDEX_DATE"])
    series_keys = KEYS + ["item"]

    # 1. common start date per place/transaction
    first = df.groupby(series_keys)["INDEX_DATE"].transform("min")
    df["ref"] = first.groupby([df[k] for k in KEYS]).transform("max")
    df = df[df["INDEX_DATE"] >= df["ref"]]

    # 2. rebase each series to 100 at its first remaining observation
    base = df.groupby(series_keys)["INDEX_VALUE"].transform("first")
    df["rebased"] = 100.0 * df["INDEX_VALUE"] / base

    # 3. weights: stock count per item if the place has a positive count, else last volume
    last_vol = df.groupby(series_keys)["VOLUME"].transform("last")
    sw = stock_weights or {}
    if sw:
        stock = [sw.get(p, {}).get(i) for p, i in zip(df["PLACE_ID"], df["item"])]
        stock = pd.Series(stock, index=df.index, dtype="float64")
        df["w"] = stock.where(stock > 0).fillna(last_vol)
    else:
        df["w"] = last_vol
    df.loc[~(df["w"] > 0), "w"] = 0.0          # NaN, None, 0 or negative -> unusable
    totals = (df.drop_duplicates(series_keys).groupby(KEYS)["w"].sum().rename("total"))

    agg = (df.assign(wv=df["w"] * df["rebased"])
             .groupby(KEYS + ["INDEX_DATE"])
             .agg(num=("wv", "sum"), VOLUME=("VOLUME", "sum"))
             .reset_index()
             .merge(totals, on=KEYS))
    agg["INDEX_VALUE"] = np.where(agg["total"] > 0, agg["num"] / agg["total"].where(agg["total"] > 0, 1), agg["num"])

    # 4. first observation pinned to 100
    agg = agg.sort_values(KEYS + ["INDEX_DATE"])
    is_first = ~agg.duplicated(KEYS)
    agg.loc[is_first, "INDEX_VALUE"] = 100.0
    agg["INDEX_DATE"] = agg["INDEX_DATE"].dt.date
    return agg[KEYS + ["INDEX_DATE", "INDEX_VALUE", "VOLUME"]].reset_index(drop=True)

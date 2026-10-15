"""
Snowflake-backed index functions.

Requires a Snowpark session. Ported from market_insights.py.
"""
from __future__ import annotations

import json
import numpy as np
import pandas as pd

from housing_germany.indices.core import (
    common_start_date,
    rebase_series,
    volume_weighted_index,
    evolution_between,
)
from housing_germany.config import ITEM_TYPE_KEYS

# Module-level cache for housing stock weights.
_STOCK_WEIGHTS: dict = {}


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple, np.ndarray)):
        return list(v)
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return []
    return list(v)


def load_indices(session, place_type="AD02", transaction_type="TRANSACTION_TYPE.SELL") -> pd.DataFrame:
    """
    Tidy index frame from INDICE_DE. One row per (place, item, transaction)
    with dates, values and volumes as Python lists.
    """
    df = session.sql(
        """
        SELECT PLACE_ID, ITEM_TYPE, TRANSACTION_TYPE, DATES, "VALUES", VOLUMES
        FROM SILVER_PROD.MKKN_INDICE.INDICE_DE
        WHERE PLACE_TYPE = ?
          AND METRIC = 'METRIC.SQM_PRICE'
          AND TRANSACTION_TYPE = ?
        QUALIFY ROW_NUMBER() OVER (PARTITION BY PLACE_ID, ITEM_TYPE ORDER BY RECORD_TS DESC) = 1
        """,
        params=[place_type, transaction_type],
    ).to_pandas()
    df["item"] = df["ITEM_TYPE"].map(ITEM_TYPE_KEYS)
    for col in ("DATES", "VALUES", "VOLUMES"):
        df[col] = df[col].apply(_as_list)
    df["last_volume"] = df["VOLUMES"].apply(lambda v: v[-1] if v else np.nan)
    return df


def load_housing_weights(session, place_type="AD02") -> dict:
    """
    Housing stock counts from GEO_PLACE_LATEST, keyed by place_id.
    Returns {place_id: {'apartment': count, 'house': count}}.
    Also populates the module-level _STOCK_WEIGHTS cache.
    """
    global _STOCK_WEIGHTS
    df = session.sql(
        """
        SELECT ID,
               STATISTICS:"STATS.DE.MA.HOUSING.APARTMENT_COUNT"::FLOAT AS NBAPA,
               STATISTICS:"STATS.DE.MA.HOUSING.HOUSE_COUNT"::FLOAT      AS NBMA
        FROM GEO_DEV.GEOR_GEOREF.GEO_PLACE_LATEST
        WHERE TYPE = ?
          AND ID LIKE '____DE%'
          AND STATISTICS:"STATS.DE.MA.HOUSING.APARTMENT_COUNT" IS NOT NULL
        """,
        params=[place_type],
    ).to_pandas()
    result = {
        row["ID"]: {"apartment": float(row["NBAPA"] or 0), "house": float(row["NBMA"] or 0)}
        for _, row in df.iterrows()
    }
    _STOCK_WEIGHTS.update(result)
    return result


def build_index_series(indices_df: pd.DataFrame, item: str, place_ids,
                       ref_date=None, stock_weights=None) -> pd.Series:
    """
    Volume-weighted, optionally re-rebased index for one item type across
    the selected regions.

    For item='hybrid', computes from house + apartment components.
    """
    if item == "hybrid":
        subset = indices_df[
            (indices_df["item"].isin(["house", "apartment"]))
            & (indices_df["PLACE_ID"].isin(place_ids))
        ]
    else:
        subset = indices_df[(indices_df["item"] == item) & (indices_df["PLACE_ID"].isin(place_ids))]

    if subset.empty:
        return pd.Series(dtype=float)

    effective_ref = ref_date
    if item == "hybrid" and effective_ref is None:
        effective_ref = common_start_date(subset["DATES"].tolist())

    series_list, volume_list = [], []
    for _, row in subset.iterrows():
        series_list.append(rebase_series(row["DATES"], row["VALUES"], effective_ref))
        if item == "hybrid":
            sw = stock_weights if stock_weights is not None else _STOCK_WEIGHTS
            if sw:
                pid = row["PLACE_ID"]
                item_key = row["item"]
                vol = sw.get(pid, {}).get(item_key, row["last_volume"])
            else:
                vol = row["last_volume"]
        else:
            vol = row["last_volume"]
        volume_list.append(vol)
    return volume_weighted_index(series_list, volume_list)


def variation_windows(index_series: pd.Series, as_of=None) -> dict:
    """
    Cumulative % change of a rebased index over standard windows:
    1m, 3m, 6m, YTD, 1y, 2y, 3y, 5y, all-time.
    """
    if index_series is None or index_series.empty:
        return {k: np.nan for k in ("1m", "3m", "6m", "YTD", "1y", "2y", "3y", "5y", "all")}
    s = index_series.sort_index()
    end = pd.to_datetime(as_of) if as_of is not None else s.index.max()
    offsets = {
        "1m": end - pd.DateOffset(months=1),
        "3m": end - pd.DateOffset(months=3),
        "6m": end - pd.DateOffset(months=6),
        "YTD": pd.Timestamp(year=end.year - 1, month=12, day=1),
        "1y": end - pd.DateOffset(years=1),
        "2y": end - pd.DateOffset(years=2),
        "3y": end - pd.DateOffset(years=3),
        "5y": end - pd.DateOffset(years=5),
        "all": s.index.min(),
    }
    return {k: evolution_between(s, d, end) for k, d in offsets.items()}

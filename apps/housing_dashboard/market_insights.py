# Standardized German housing-market calculations for the Streamlit dashboard.
# Co-authored with CoCo
"""
Standardized German housing-market calculations.

Harmonized from the `iwt_barometer` PostgreSQL pipeline
(indice_functions, price_functions, indices_aggregation,
indices_with_prices, realestate_data) to run against the Snowflake
silver tables:

  - SILVER_PROD.MKKN_INDICE.INDICE_DE  indices (rebased to 100) + volumes,
                                       per place / item / transaction
  - SILVER_PROD.MKKN_PRICE.PRICE_DE    current sqm prices per place / item

Why this is not a 1:1 port of the Postgres code
------------------------------------------------
The original code fetched *raw* indices from Postgres and rebuilt everything
in Python because the source stored raw data. The Snowflake indices are
already:
  * rebased to 100 at the series start,
  * accompanied by a VOLUMES array (the transaction counts used as weights,
    i.e. the old `log_count` that used to come from `miracle.feature`),
  * available as a ready-made ITEM_TYPE.HYBRID series.

So the upstream fetch / rebase / hybrid-build steps collapse. What stays
standardized (and is preserved here verbatim in its math) are the primitives:
evolution, re-rebasing to a chosen reference date, volume-weighted
aggregation across regions, and implied past prices.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Snowflake INDICE_DE item labels -> short keys used across the app.
ITEM_TYPE_KEYS = {
    "ITEM_TYPE.HOUSE": "house",
    "ITEM_TYPE.APARTMENT": "apartment",
    "ITEM_TYPE.HYBRID": "hybrid",
}
# PRICE_DE uses the short, prefix-less form.
PRICE_ITEM_TYPES = {"house": "HOUSE", "apartment": "APARTMENT", "hybrid": "HYBRID"}

TXN_SELL = "TRANSACTION_TYPE.SELL"
TXN_RENT = "TRANSACTION_TYPE.RENT"

# Absolute-price source for RENT. None = use PRICE_DE (auto-enables when AVIV
# lands rent rows). Set to "classifieds" to derive listing-based rent prices.
RENT_PRICE_SOURCE = None

# Module-level cache for housing stock weights (populated by load_housing_weights).
# build_index_series uses this automatically for hybrid weighting so callers
# don't need to pass stock_weights explicitly.
_STOCK_WEIGHTS: dict = {}


# ---------------------------------------------------------------------------
# Calculation primitives (backend-agnostic; math preserved from iwt_barometer)
# ---------------------------------------------------------------------------

def pct_evolution(series: pd.Series) -> pd.Series:
    """Period-over-period % change. Port of `calcul_evol_from_series`."""
    return series.pct_change().fillna(0)


def rebase_to_100(evolution: pd.Series) -> list[float]:
    """
    Compound an evolution series into an index starting at 100.
    Port of `calcul_rebase`: rebase[i] = rebase[i-1] * (1 + evol[i]).
    """
    out = [100.0]
    for i in range(1, len(evolution)):
        out.append(out[i - 1] * (1 + evolution.iloc[i]))
    return out


def common_start_date(date_lists: list[list]) -> pd.Timestamp | None:
    """
    Latest of each series' first date, so co-plotted series share a start.
    Port of `find_first_date_rebase`.
    """
    firsts = [pd.to_datetime(lst[0]) for lst in date_lists if lst is not None and len(lst)]
    return max(firsts) if firsts else None


def rebase_series(dates, values, ref_date=None) -> pd.Series:
    """
    Re-index a (dates, values) pair to 100 at `ref_date`.

    Mirrors `rebase_indices_common`: cut at the reference date, recompute
    evolution, compound back to 100. If `ref_date` is None the series is
    returned unchanged (Snowflake values are already rebased at series start).
    """
    if values is None or len(values) == 0:
        return pd.Series(dtype=float)
    s = pd.Series(values, index=pd.to_datetime(list(dates))).astype(float).sort_index()
    if ref_date is None:
        return s
    cut = pd.to_datetime(ref_date)
    if cut < s.index.min():
        cut = s.index.min()
    s = s[s.index >= cut]
    if s.empty:
        return s
    evol = pct_evolution(s)
    return pd.Series(rebase_to_100(evol), index=s.index)


def volume_weighted_index(series_list: list[pd.Series], volume_list: list[float]) -> pd.Series:
    """
    Volume-weighted aggregation of several rebased index series.
    Port of `aggregate_rebase_indices`: Σ(volume · series) / Σ(volume),
    with the first observation pinned to 100.
    """
    if not series_list:
        return pd.Series(dtype=float)
    all_dates = pd.to_datetime(
        pd.Index(np.concatenate([s.index.values for s in series_list if not s.empty])).unique()
    ).sort_values()
    agg = pd.Series(0.0, index=all_dates)
    total = 0.0
    for s, vol in zip(series_list, volume_list):
        if s.empty or vol is None or pd.isna(vol) or vol <= 0:
            continue
        total += vol
        agg = agg.add(vol * s, fill_value=0)
    if total > 0:
        agg = agg / total
    if not agg.empty:
        agg.iloc[0] = 100.0
    return agg


def implied_price_at(current_price: float, evolution: float) -> float:
    """
    Past price implied by the current price and a cumulative evolution.
    Port of the `current / (1 + evolution)` step in
    `get_FINAL_evolution_and_price`.
    """
    if current_price is None or pd.isna(current_price):
        return np.nan
    return current_price / (1 + evolution)


def evolution_between(index_series: pd.Series, date_from, date_to=None) -> float:
    """
    Cumulative % evolution of a rebased index between two dates.
    Distilled from `get_indice_value_two_dates` (uses nearest available
    observation at or before each target date; latest point if date_to None).
    """
    if index_series.empty:
        return np.nan
    s = index_series.sort_index()
    v_to = s.iloc[-1] if date_to is None else _value_at_or_before(s, date_to)
    v_from = _value_at_or_before(s, date_from)
    if v_from is None or v_to is None or v_from == 0:
        return np.nan
    return (v_to / v_from) - 1


def _value_at_or_before(series: pd.Series, target_date) -> float | None:
    target = pd.to_datetime(target_date)
    eligible = series[series.index <= target]
    return float(eligible.iloc[-1]) if not eligible.empty else None


# ---------------------------------------------------------------------------
# Snowflake data access (replaces psycopg2 / miracle.feature / clustered_indice)
# ---------------------------------------------------------------------------

def load_indices(session, place_type="AD02", transaction_type=TXN_SELL) -> pd.DataFrame:
    """
    Tidy index frame from INDICE_DE. One row per (place, item, transaction)
    with `dates`, `values` and `volumes` as Python lists.
    Replaces `get_indices_house_apa` + `get_hybrid_indice`.
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
    Port of `calculate_weight_apa_house` which reads
    STATS.DE.MA.HOUSING.{APARTMENT_COUNT,HOUSE_COUNT} from miracle.feature.
    Used as hybrid volume weights instead of last_volume.

    Also populates the module-level _STOCK_WEIGHTS cache so that all
    subsequent build_index_series calls automatically use stock weights
    for hybrid computation (no explicit parameter needed).
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


def load_current_prices(session, place_type="AD02", min_accuracy=3, years=3) -> pd.DataFrame:
    """Current sqm prices from PRICE_DE. Replaces `request_actual_price_classical`."""
    return session.sql(
        """
        SELECT ITEM_TYPE, PLACE_ID, PRICE_DATE, VALUE, HIGH, LOW
        FROM SILVER_PROD.MKKN_PRICE.PRICE_DE
        WHERE PLACE_TYPE = ?
          AND ACCURACY >= ?
          AND PRICE_DATE >= DATEADD(YEAR, ?, CURRENT_DATE())
        ORDER BY PRICE_DATE DESC
        """,
        params=[place_type, min_accuracy, -abs(years)],
    ).to_pandas()


def build_index_series(indices_df: pd.DataFrame, item: str, place_ids,
                       ref_date=None, stock_weights=None) -> pd.Series:
    """
    Volume-weighted, optionally re-rebased index for one item type across the
    selected regions. Combines `load_indices` rows through the standardized
    `rebase_series` + `volume_weighted_index` primitives.

    For item='hybrid', always computes from house + apartment components
    (volume-weighted), matching the barometer's `get_hybrid_indice` /
    `aggregate_rebase_indices` logic. Pre-built HYBRID rows from INDICE_DE
    are intentionally ignored to ensure formula parity.

    `stock_weights` (optional): dict from `load_housing_weights`, keyed by
    place_id → {'apartment': count, 'house': count}. When provided and
    computing hybrid, uses housing stock counts as weights instead of
    last_volume (transaction counts), matching the barometer's
    `calculate_weight_apa_house` convention.
    """
    if item == "hybrid":
        # Always compute hybrid from house + apartment components, matching
        # the barometer's get_hybrid_indice / aggregate_rebase_indices logic.
        # Never use pre-built HYBRID rows (they may differ in weighting).
        subset = indices_df[
            (indices_df["item"].isin(["house", "apartment"]))
            & (indices_df["PLACE_ID"].isin(place_ids))
        ]
    else:
        subset = indices_df[(indices_df["item"] == item) & (indices_df["PLACE_ID"].isin(place_ids))]

    if subset.empty:
        return pd.Series(dtype=float)

    # For hybrid (and any multi-series aggregation), the barometer cuts all
    # component series to the common start date and rebases each to 100 from
    # that point.  Honour the same convention here so evolution ratios match.
    effective_ref = ref_date
    if item == "hybrid" and effective_ref is None:
        effective_ref = common_start_date(subset["DATES"].tolist())

    series_list, volume_list = [], []
    for _, row in subset.iterrows():
        series_list.append(rebase_series(row["DATES"], row["VALUES"], effective_ref))
        # For hybrid, prefer housing stock weights (barometer convention).
        # Use explicitly passed weights, then module cache, then last_volume.
        if item == "hybrid":
            sw = stock_weights if stock_weights is not None else _STOCK_WEIGHTS
            if sw:
                pid = row["PLACE_ID"]
                item_key = row["item"]  # 'apartment' or 'house'
                vol = sw.get(pid, {}).get(item_key, row["last_volume"])
            else:
                vol = row["last_volume"]
        else:
            vol = row["last_volume"]
        volume_list.append(vol)
    return volume_weighted_index(series_list, volume_list)


def load_prices(session, transaction_type=TXN_SELL, place_type="AD02",
                min_accuracy=3, years=3) -> pd.DataFrame:
    """
    Absolute sqm prices from PRICE_DE, filtered by transaction type.

    Forward-compatible: PRICE_DE is partitioned by TRANSACTION_TYPE, so the day
    AVIV lands rent rows (TRANSACTION_TYPE.RENT) this returns them with no code
    change. Today RENT yields an empty frame. `RENT_PRICE_SOURCE` is a seam for
    an alternate (listing-based) rent source if ever needed.
    """
    if transaction_type == TXN_RENT and RENT_PRICE_SOURCE == "classifieds":
        return _load_rent_prices_from_classifieds(session, place_type, years)
    return session.sql(
        """
        SELECT ITEM_TYPE, PLACE_ID, PRICE_DATE, VALUE, HIGH, LOW, ACCURACY
        FROM SILVER_PROD.MKKN_PRICE.PRICE_DE
        WHERE PLACE_TYPE = ?
          AND TRANSACTION_TYPE = ?
          AND ACCURACY >= ?
          AND PRICE_DATE >= DATEADD(YEAR, ?, CURRENT_DATE())
        ORDER BY PRICE_DATE DESC
        """,
        params=[place_type, transaction_type, min_accuracy, -abs(years)],
    ).to_pandas()


def _load_rent_prices_from_classifieds(session, place_type, years):
    """Placeholder for the optional listing-based rent source (Option B)."""
    return pd.DataFrame(columns=["ITEM_TYPE", "PLACE_ID", "PRICE_DATE", "VALUE", "HIGH", "LOW", "ACCURACY"])


def has_absolute_price(session, transaction_type=TXN_SELL) -> bool:
    """True if PRICE_DE holds any rows for the transaction type (cheap probe)."""
    df = session.sql(
        "SELECT 1 FROM SILVER_PROD.MKKN_PRICE.PRICE_DE WHERE TRANSACTION_TYPE = ? LIMIT 1",
        params=[transaction_type],
    ).to_pandas()
    return len(df) > 0


# ---------------------------------------------------------------------------
# Variation windows (built on the validated evolution_between primitive)
# ---------------------------------------------------------------------------

def variation_windows(index_series: pd.Series, as_of=None) -> dict:
    """
    Cumulative % change of a rebased index over standard windows:
    1m, 3m, 6m, YTD, 1y, 2y, 3y, 5y, all-time. Each uses `evolution_between`.
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


# ---------------------------------------------------------------------------
# Geo access: DE_GEO_PLACE (names, coordinates, polygons, hierarchy)
# ---------------------------------------------------------------------------
# DE_GEO_PLACE has many rows per level and duplicate IDs across snapshots, so we
# always dedupe with GROUP BY ID. Callers should inner-join to priced/indexed
# place lists to exclude non-market aggregates.

GEO_TABLE = "SILVER_PROD.GEOR_GEOREF.DE_GEO_PLACE"
_PARENT_COL = {"AD04": "AD04", "AD06": "AD06", "AD08": "AD08"}  # allowlist
# German entities only: ID is TYPE(4 chars) + COUNTRY(2) + number, e.g. AD08DE8634.
_DE_FILTER = "AND ID LIKE '____DE%'"


def load_geo(session, place_type) -> pd.DataFrame:
    """id, name, display_name, population, lat, lon, parent ids for a level."""
    return session.sql(
        f"""
        SELECT ID AS "place_id",
               MAX(NAME) AS "name",
               MAX(DISPLAY_NAME) AS "display_name",
               MAX(POPULATION) AS "population",
               MAX(ST_Y(TO_GEOGRAPHY(CENTROID))) AS "lat",
               MAX(ST_X(TO_GEOGRAPHY(CENTROID))) AS "lon",
               MAX(AD04[0]::string) AS "parent_ad04",
               MAX(AD06[0]::string) AS "parent_ad06"
        FROM {GEO_TABLE}
        WHERE TYPE = ? {_DE_FILTER}
        GROUP BY ID
        """,
        params=[place_type],
    ).to_pandas()


def load_geo_polygons(session, place_type) -> pd.DataFrame:
    """id, name + simplified GeoJSON (GEOM9) polygon string for choropleths."""
    return session.sql(
        f"""
        SELECT ID AS "place_id",
               MAX(NAME) AS "name",
               MAX(ST_ASGEOJSON(GEOM9)::string) AS "geojson"
        FROM {GEO_TABLE}
        WHERE TYPE = ? {_DE_FILTER}
        GROUP BY ID
        """,
        params=[place_type],
    ).to_pandas()


def children_of(session, parent_id, parent_level, child_type) -> pd.DataFrame:
    """
    Child places (with coordinates) whose `parent_level` parent equals parent_id.
    e.g. sibling cities in a district: children_of(s, ad06_id, 'AD06', 'AD08').
    """
    col = _PARENT_COL.get(parent_level)
    if col is None:
        raise ValueError(f"Unsupported parent level: {parent_level}")
    return session.sql(
        f"""
        SELECT ID AS "place_id",
               MAX(NAME) AS "name",
               MAX(POPULATION) AS "population",
               MAX(ST_Y(TO_GEOGRAPHY(CENTROID))) AS "lat",
               MAX(ST_X(TO_GEOGRAPHY(CENTROID))) AS "lon"
        FROM {GEO_TABLE}
        WHERE TYPE = ? AND {col}[0]::string = ?
        GROUP BY ID
        """,
        params=[child_type, parent_id],
    ).to_pandas()


def parent_at_level(session, place_id) -> dict:
    """Parent ids (AD04/AD06/AD08) and type for a single place."""
    df = session.sql(
        f"""
        SELECT MAX(TYPE) AS "type", MAX(NAME) AS "name",
               MAX(AD04[0]::string) AS "ad04",
               MAX(AD06[0]::string) AS "ad06",
               MAX(AD08[0]::string) AS "ad08"
        FROM {GEO_TABLE} WHERE ID = ?
        """,
        params=[place_id],
    ).to_pandas()
    return {} if df.empty else df.iloc[0].to_dict()


def place_directory(session, types=("AD04", "AD06", "AD08")) -> pd.DataFrame:
    """
    Small (id, name, type) directory for autofill search across admin levels.
    Deduped by ID; cache this in the app (types set is fixed, not user input).
    """
    type_list = ",".join(f"'{t}'" for t in types if t in ("AD04", "AD05", "AD06", "AD08", "AD09"))
    return session.sql(
        f"""
        SELECT ID AS "place_id", MAX(NAME) AS "name", MAX(TYPE) AS "type"
        FROM {GEO_TABLE}
        WHERE TYPE IN ({type_list}) {_DE_FILTER}
        GROUP BY ID
        """
    ).to_pandas()


# ---------------------------------------------------------------------------
# UI helper (Streamlit imported lazily so the pure-math module stays importable
# without Streamlit, e.g. in validate_harmonization.py).
# ---------------------------------------------------------------------------

def insight_card(title, value, breakdown=None):
    """
    Yellow-header insight card: a headline metric plus optional labeled
    sub-metrics with green-up / red-down arrows. `breakdown` = list of
    (label, pct_value) tuples.
    """
    import streamlit as st

    st.markdown(
        f"""<div style="background:#FFC72C;border-radius:14px;padding:12px 18px;
        margin-bottom:8px;color:#1a1a2e;font-weight:600;font-size:1.05rem;">
        {title}: <span style="font-weight:800;">{value}</span></div>""",
        unsafe_allow_html=True,
    )
    if breakdown:
        for label, pct in breakdown:
            if pct is None or (isinstance(pct, float) and np.isnan(pct)):
                st.markdown(f"&nbsp;&nbsp;{label}: n/a", unsafe_allow_html=True)
            else:
                up = pct >= 0
                arrow = "\u2191" if up else "\u2193"
                color = "#1Db954" if up else "#e74c3c"
                st.markdown(
                    f"""<div style="padding:2px 10px;color:{color};font-weight:600;">
                    {arrow} {label}: {pct * 100:+.1f}%</div>""",
                    unsafe_allow_html=True,
                )


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple, np.ndarray)):
        return list(v)
    if isinstance(v, str):
        import json
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return []
    return list(v)

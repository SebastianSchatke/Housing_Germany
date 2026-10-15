"""
Pure math primitives for index calculations.

These are backend-agnostic (no database calls). Deduplicated from:
- indice_functions.py: calcul_evol_from_series, calcul_rebase, find_first_date_rebase
- market_insights.py: pct_evolution, rebase_to_100, common_start_date, rebase_series, etc.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def pct_evolution(series: pd.Series) -> pd.Series:
    """Period-over-period % change."""
    return series.pct_change().fillna(0)


# Backward-compatible alias
calcul_evol_from_series = pct_evolution


def rebase_to_100(evolution: pd.Series) -> list[float]:
    """Compound an evolution series into an index starting at 100."""
    out = [100.0]
    for i in range(1, len(evolution)):
        out.append(out[i - 1] * (1 + evolution.iloc[i]))
    return out


# Backward-compatible alias
calcul_rebase = rebase_to_100


def common_start_date(date_lists: list[list]) -> pd.Timestamp | None:
    """Latest of each series' first date, so co-plotted series share a start."""
    firsts = [pd.to_datetime(lst[0]) for lst in date_lists if lst is not None and len(lst)]
    return max(firsts) if firsts else None


# Backward-compatible alias
find_first_date_rebase = common_start_date


def rebase_series(dates, values, ref_date=None) -> pd.Series:
    """
    Re-index a (dates, values) pair to 100 at ref_date.

    If ref_date is None the series is returned unchanged (Snowflake values
    are already rebased at series start).
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
    """Volume-weighted aggregation of several rebased index series."""
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


# Backward-compatible alias
aggregate_rebase_indices_math = volume_weighted_index


def evolution_between(index_series: pd.Series, date_from, date_to=None) -> float:
    """Cumulative % evolution of a rebased index between two dates."""
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

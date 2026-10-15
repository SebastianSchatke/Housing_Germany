"""
Mortgage and financial calculation helpers.
"""
from __future__ import annotations

import pandas as pd


def mortgage_formula(annual_interest_rate: float, years: int = 30) -> float:
    """
    Calculate mortgage factor (PVIFA) for given interest rate and term.

    Args:
        annual_interest_rate: Annual interest rate as a percentage (e.g. 3.5 for 3.5%)
        years: Loan term in years (default 30)

    Returns:
        Present value interest factor of annuity
    """
    monthly_interest_rate = annual_interest_rate / 100 / 12
    nper = years * 12
    if monthly_interest_rate == 0:
        return nper
    mortgage_factor = ((1 + monthly_interest_rate) ** nper - 1) / (
        monthly_interest_rate * (1 + monthly_interest_rate) ** nper
    )
    return mortgage_factor


def calc_percent_change(series: pd.Series) -> pd.Series:
    """Calculate percent change for a series."""
    return series.pct_change()

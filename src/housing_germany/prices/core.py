"""
Pure math primitives for price calculations (backend-agnostic).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def implied_price_at(current_price: float, evolution: float) -> float:
    """Past price implied by the current price and a cumulative evolution."""
    if current_price is None or pd.isna(current_price):
        return np.nan
    return current_price / (1 + evolution)


def calculate_new_price(row, item_type, months):
    """Calculate new prices for aggregates given evolution columns."""
    prices_col = f'Prices {item_type}'
    for month in months:
        month_col = f'{month}{item_type}'
        new_price_col = f'new {month} {item_type}'
        row[new_price_col] = row[prices_col] / (1 + row[month_col])
    return row

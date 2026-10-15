"""
Snowflake-backed price functions.

Requires a Snowpark session. Ported from market_insights.py.
"""
from __future__ import annotations

import pandas as pd

TXN_SELL = "TRANSACTION_TYPE.SELL"
TXN_RENT = "TRANSACTION_TYPE.RENT"
RENT_PRICE_SOURCE = None


def load_prices(session, transaction_type=TXN_SELL, place_type="AD02",
                min_accuracy=3, years=3) -> pd.DataFrame:
    """Absolute sqm prices from PRICE_DE, filtered by transaction type."""
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


def load_current_prices(session, place_type="AD02", min_accuracy=3, years=3) -> pd.DataFrame:
    """Current sqm prices from PRICE_DE (sell only)."""
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


def has_absolute_price(session, transaction_type=TXN_SELL) -> bool:
    """True if PRICE_DE holds any rows for the transaction type."""
    df = session.sql(
        "SELECT 1 FROM SILVER_PROD.MKKN_PRICE.PRICE_DE WHERE TRANSACTION_TYPE = ? LIMIT 1",
        params=[transaction_type],
    ).to_pandas()
    return len(df) > 0


def _load_rent_prices_from_classifieds(session, place_type, years):
    """Placeholder for the optional listing-based rent source."""
    return pd.DataFrame(columns=["ITEM_TYPE", "PLACE_ID", "PRICE_DATE", "VALUE", "HIGH", "LOW", "ACCURACY"])

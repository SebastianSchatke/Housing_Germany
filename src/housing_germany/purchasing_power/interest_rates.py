"""
Interest rate data fetching and loading.

Unified from load_interest_rates.py and the notebook's get_interest_rate_series.
Uses the baudarlehen-24 API for German mortgage interest rates.
"""
from __future__ import annotations

import os
import pandas as pd
import requests

_DEFAULT_API_KEY = os.environ.get("BAUDARLEHEN_API_KEY", "")
_BASE_URL = "https://baudarlehen-24.azurewebsites.net/api/interest-rates-history"


def fetch_interest_rates(
    start_date: str = "2010-01-01",
    end_date: str = "2026-08-01",
    fixed_years: int = 10,
    api_key: str | None = None,
) -> pd.DataFrame:
    """
    Fetch historical interest rate data from baudarlehen-24 API.

    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        fixed_years: Fixed interest rate period in years
        api_key: API key (defaults to env var BAUDARLEHEN_API_KEY)

    Returns:
        DataFrame with date, nominalInterestRate, effectiveInterestRate, etc.
    """
    params = {
        "startDate": start_date,
        "endDate": end_date,
        "fixedInterestRateInYears": fixed_years,
        "code": api_key or _DEFAULT_API_KEY,
    }
    response = requests.get(_BASE_URL, params=params)
    response.raise_for_status()
    df = pd.DataFrame(response.json())
    df['date'] = pd.to_datetime(df['date']).dt.normalize()
    return df


# Backward-compatible alias
get_interest_rate_series = fetch_interest_rates


def load_to_snowflake(session, start_date="2010-01-01", end_date="2026-08-01"):
    """Fetch API data and insert into Snowflake INTEREST_RATES table."""
    df = fetch_interest_rates(start_date=start_date, end_date=end_date)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

    schema = "HOUSING_DEV.MARKET_INSIGHTS_STREAMLIT_APPS"
    session.sql(f"DELETE FROM {schema}.INTEREST_RATES").collect()

    rows = []
    for _, r in df.iterrows():
        rows.append(
            f"('{r['date']}', {r['fixedInterestPeriod']}, {r['loanToValueRatio']}, "
            f"{r['nominalInterestRate']}, {r['nominalInterestRateFromPreviousMonth']}, "
            f"{r['nominalInterestRateDifference']}, {r['effectiveInterestRate']}, "
            f"{r['trendInInterestRatesFromPreviousMonthValue']})"
        )

    for i in range(0, len(rows), 50):
        batch = rows[i: i + 50]
        sql = f"""INSERT INTO {schema}.INTEREST_RATES
            (RATE_DATE, FIXED_PERIOD, LTV_RATIO, NOMINAL_RATE, NOMINAL_RATE_PREV, RATE_CHANGE, EFFECTIVE_RATE, TREND)
            VALUES {','.join(batch)}"""
        session.sql(sql).collect()

    print(f"Loaded {len(rows)} rows into {schema}.INTEREST_RATES")

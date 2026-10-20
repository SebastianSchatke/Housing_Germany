"""
German income data processing.

Fetches median net income data from the Bundesagentur fuer Arbeit
(via macro_fetcher) and matches it to geo IDs via administrative codes.
"""
from __future__ import annotations

import pandas as pd

from housing_germany.geo.functions import GEO_TABLE, _DE_FILTER
from housing_germany.macro_fetcher import fetch_income

# Backward-compatible alias
income_raw_DE_task = fetch_income


def get_income_DE_common(session, geotype=None) -> tuple[pd.DataFrame, str]:
    """
    Get income data matched to geo IDs.

    Args:
        session: Snowpark session
        geotype: 'AD02DE' (country), 'AD04DE' (Bundeslaender),
                 'AD06DE' (Landkreise/Stadtkreise), or None (all levels)

    Returns:
        Tuple of (income DataFrame with geo IDs, year string)
    """
    income_raw, year = fetch_income()

    type_map = {
        'AD02DE': "'AD02'",
        'AD04DE': "'AD04'",
        'AD06DE': "'AD06'",
        None:     "'AD02', 'AD04', 'AD06'",
    }
    type_filter = type_map[geotype]

    geo = session.sql(
        f"""
        SELECT ID AS "id",
               MAX(TYPE) AS "key",
               MAX(ADMINISTRATIVE_CODE) AS "administrative_code",
               MAX(TYPE) AS "type",
               MAX(DISPLAY_NAME) AS "display_name"
        FROM {GEO_TABLE}
        WHERE TYPE IN ({type_filter})
          AND ACTIVE = TRUE
          {_DE_FILTER}
        GROUP BY ID
        """
    ).to_pandas()

    # Normalize administrative_code to match cog format
    geo["administrative_code"] = geo["administrative_code"].astype(str).str.strip()
    geo.loc[geo["key"] == "AD04", "administrative_code"] = (
        geo.loc[geo["key"] == "AD04", "administrative_code"].str.zfill(2)
    )
    geo.loc[geo["key"] == "AD06", "administrative_code"] = (
        geo.loc[geo["key"] == "AD06", "administrative_code"].str.zfill(5)
    )

    # Primary join: administrative_code = cog
    merged = geo.merge(
        income_raw, left_on="administrative_code", right_on="cog",
        how="left", indicator=True
    )
    matched = merged[merged["_merge"] == "both"].drop(columns=["_merge"])

    # Fallback join for unmatched: display_name = place
    unmatched_geo = (
        merged[merged["_merge"] == "left_only"]
        .drop(columns=["_merge"] + [c for c in income_raw.columns if c in merged.columns and c != "display_name"])
    )

    if not unmatched_geo.empty:
        unmatched_geo["display_name_clean"] = (
            unmatched_geo["display_name"].str.strip().str.rstrip(",").str.strip()
        )
        income_raw["place_clean"] = income_raw["place"].str.strip()
        fallback = unmatched_geo.merge(
            income_raw, left_on="display_name_clean", right_on="place_clean",
            how="inner"
        )
        if not fallback.empty:
            fallback = fallback.drop(columns=["display_name_clean", "place_clean"], errors="ignore")
            matched = pd.concat([matched, fallback], ignore_index=True)

    return matched, year

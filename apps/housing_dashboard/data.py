# Cached Snowflake loaders for pre-computed housing market tables.
# Co-authored with CoCo
"""Cached Snowflake loaders shared across pages.

Reads from pre-computed tables in HOUSING_DEV.MARKET_INSIGHTS_STREAMLIT_APPS:
  - INDEX_SERIES:   flattened time series (one row per place/item/txn/date)
  - MARKET_SUMMARY: pre-computed variation windows + latest absolute prices
  - PRICE_HISTORY:  back-calculated €/m² prices from index + current price
  - GEO_PLACES:     coordinates, names, parent hierarchy
  - GEO_POLYGONS:   simplified GeoJSON for choropleth maps
"""

import streamlit as st
import pandas as pd
import market_insights as mi

_SCHEMA = "HOUSING_DEV.MARKET_INSIGHTS_STREAMLIT_APPS"
_PARENT_COLS = {"PARENT_AD04", "PARENT_AD06"}


# ---------------------------------------------------------------------------
# Source-based loaders (via market_insights, same formulas as the barometer)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=3600, show_spinner=False)
def get_indices(_session, transaction_type, place_type):
    """Source indices from INDICE_DE via market_insights.load_indices.

    Also ensures housing stock weights are cached in market_insights so
    that build_index_series uses barometer-consistent hybrid weighting.
    """
    mi.load_housing_weights(_session, place_type)
    return mi.load_indices(_session, place_type, transaction_type)


@st.cache_data(ttl=3600, show_spinner=False)
def get_index_series(_session, transaction_type, place_type):
    """Full time series from pre-computed INDEX_SERIES table."""
    return _session.sql(f"""
        SELECT PLACE_ID, ITEM_TYPE, INDEX_DATE, INDEX_VALUE, VOLUME
        FROM {_SCHEMA}.INDEX_SERIES
        WHERE TRANSACTION_TYPE = ?
          AND PLACE_TYPE = ?
        ORDER BY PLACE_ID, ITEM_TYPE, INDEX_DATE
    """, params=[transaction_type, place_type]).to_pandas()


@st.cache_data(ttl=3600, show_spinner=False)
def get_price_history(_session, transaction_type, place_id):
    """Historical €/m² prices for a single place (back-calculated from index)."""
    return _session.sql(f"""
        SELECT ITEM_TYPE, INDEX_DATE, PRICE_SQM
        FROM {_SCHEMA}.PRICE_HISTORY
        WHERE TRANSACTION_TYPE = ?
          AND PLACE_ID = ?
        ORDER BY ITEM_TYPE, INDEX_DATE
    """, params=[transaction_type, place_id]).to_pandas()


@st.cache_data(ttl=3600, show_spinner=False)
def get_market_summary(_session, transaction_type, place_type):
    """Wide summary with variation windows and latest price."""
    return _session.sql(f"""
        SELECT PLACE_ID, ITEM_TYPE, LATEST_DATE, LATEST_INDEX,
               PRICE_SQM, PRICE_ACCURACY,
               VAR_1M, VAR_3M, VAR_6M, VAR_YTD,
               VAR_1Y, VAR_2Y, VAR_3Y, VAR_5Y, VAR_ALL
        FROM {_SCHEMA}.MARKET_SUMMARY
        WHERE TRANSACTION_TYPE = ?
          AND PLACE_TYPE = ?
    """, params=[transaction_type, place_type]).to_pandas()


# --- Geo loaders ---

@st.cache_data(ttl=86400, show_spinner=False)
def get_geo(_session, place_type):
    """Place geo data (id, name, lat, lon, parents) from pre-computed table."""
    return _session.sql(f"""
        SELECT PLACE_ID AS "place_id", NAME AS "name", DISPLAY_NAME AS "display_name",
               POPULATION AS "population", LAT AS "lat", LON AS "lon",
               PARENT_AD04 AS "parent_ad04", PARENT_AD06 AS "parent_ad06"
        FROM {_SCHEMA}.GEO_PLACES
        WHERE PLACE_TYPE = ?
    """, params=[place_type]).to_pandas()


@st.cache_data(ttl=86400, show_spinner=False)
def get_directory(_session):
    """Deduplicated place directory with parent context for search labels."""
    return _session.sql(f"""
        SELECT 
            c.PLACE_ID AS "place_id",
            c.NAME AS "name",
            c.PLACE_TYPE AS "type",
            s.NAME AS "state_name",
            d.NAME AS "district_name"
        FROM {_SCHEMA}.GEO_PLACES c
        LEFT JOIN {_SCHEMA}.GEO_PLACES s ON c.PARENT_AD04 = s.PLACE_ID AND s.PLACE_TYPE = 'AD04'
        LEFT JOIN {_SCHEMA}.GEO_PLACES d ON c.PARENT_AD06 = d.PLACE_ID AND d.PLACE_TYPE = 'AD06'
        WHERE c.PLACE_TYPE IN ('AD04', 'AD08')
    """).to_pandas()


@st.cache_data(ttl=3600, show_spinner=False)
def get_parent(_session, place_id):
    """Parent IDs and type for a single place."""
    df = _session.sql(f"""
        SELECT PLACE_TYPE AS "type", NAME AS "name",
               PARENT_AD04 AS "ad04", PARENT_AD06 AS "ad06"
        FROM {_SCHEMA}.GEO_PLACES
        WHERE PLACE_ID = ?
    """, params=[place_id]).to_pandas()
    return {} if df.empty else df.iloc[0].to_dict()


@st.cache_data(ttl=86400, show_spinner=False)
def get_nearby_places(_session, lat, lon, place_type, limit=40):
    """Find nearest places by Haversine distance from a reference point."""
    return _session.sql(f"""
        SELECT PLACE_ID AS "place_id", NAME AS "name", LAT AS "lat", LON AS "lon",
               PARENT_AD04 AS "parent_ad04", PARENT_AD06 AS "parent_ad06",
               HAVERSINE(?, ?, LAT, LON) AS "distance_km"
        FROM {_SCHEMA}.GEO_PLACES
        WHERE PLACE_TYPE = ? AND LAT IS NOT NULL
        ORDER BY "distance_km"
        LIMIT {int(limit)}
    """, params=[float(lat), float(lon), place_type]).to_pandas()


@st.cache_data(ttl=86400, show_spinner=False)
def get_polygons(_session, place_type, parent_col=None, parent_id=None):
    """GeoJSON polygons for choropleth maps, optionally filtered by parent."""
    where = "WHERE p.PLACE_TYPE = ?"
    params = [place_type]
    if parent_col and parent_id:
        if parent_col not in _PARENT_COLS:
            raise ValueError(f"Unsupported parent column: {parent_col}")
        where += f" AND g.{parent_col} = ?"
        params.append(parent_id)
    return _session.sql(f"""
        SELECT p.PLACE_ID AS "place_id", p.NAME AS "name", p.GEOJSON AS "geojson"
        FROM {_SCHEMA}.GEO_POLYGONS p
        JOIN {_SCHEMA}.GEO_PLACES g ON p.PLACE_ID = g.PLACE_ID
        {where}
    """, params=params).to_pandas()


@st.cache_data(ttl=86400, show_spinner=False)
def get_nearby_polygons(_session, lat, lon, place_type, limit=50):
    """GeoJSON polygons for the nearest places by distance."""
    return _session.sql(f"""
        SELECT p.PLACE_ID AS "place_id", p.NAME AS "name", p.GEOJSON AS "geojson",
               HAVERSINE(?, ?, g.LAT, g.LON) AS "distance_km"
        FROM {_SCHEMA}.GEO_POLYGONS p
        JOIN {_SCHEMA}.GEO_PLACES g ON p.PLACE_ID = g.PLACE_ID
        WHERE p.PLACE_TYPE = ? AND g.LAT IS NOT NULL
        ORDER BY "distance_km"
        LIMIT {int(limit)}
    """, params=[float(lat), float(lon), place_type]).to_pandas()


@st.cache_data(ttl=86400, show_spinner=False)
def get_sibling_count(_session, district_id):
    """Count AD08 cities in a district (to detect kreisfreie Städte)."""
    df = _session.sql(f"""
        SELECT COUNT(*) AS "cnt" FROM {_SCHEMA}.GEO_PLACES
        WHERE PLACE_TYPE = 'AD08' AND PARENT_AD06 = ?
    """, params=[district_id]).to_pandas()
    return int(df.iloc[0]["cnt"]) if not df.empty else 0


# --- Income loaders ---

@st.cache_data(ttl=86400, show_spinner=False)
def get_income(_session, place_id):
    """Income data for a place (all categories, mapped via district)."""
    return _session.sql(f"""
        SELECT CATEGORY, MEDIAN_2021, MEDIAN_2022, MEDIAN_2023, MEDIAN_2024, MEDIAN_2025
        FROM {_SCHEMA}.INCOME_MAPPED
        WHERE PLACE_ID = ?
        ORDER BY CASE CATEGORY
            WHEN 'Insgesamt' THEN 1
            WHEN 'Männer' THEN 2
            WHEN 'Frauen' THEN 3
            WHEN 'unter 25 Jahre' THEN 4
            WHEN '25 bis unter 55 Jahre' THEN 5
            WHEN '55 Jahre und älter' THEN 6
            WHEN 'Deutsche' THEN 7
            WHEN 'Ausländer' THEN 8
            WHEN 'ohne Berufsabschluss' THEN 9
            WHEN 'anerkannter Berufsabschluss' THEN 10
            WHEN 'akademischer Berufsabschluss' THEN 11
            WHEN 'Helfer' THEN 12
            WHEN 'Fachkraft' THEN 13
            WHEN 'Spezialist' THEN 14
            WHEN 'Experte' THEN 15
            ELSE 99
        END
    """, params=[place_id]).to_pandas()


@st.cache_data(ttl=86400, show_spinner=False)
def get_income_national(_session):
    """National income for comparison."""
    return _session.sql(f"""
        SELECT CATEGORY, MEDIAN_2025
        FROM {_SCHEMA}.INCOME_MAPPED
        WHERE PLACE_ID = 'AD02DE1'
    """).to_pandas()


# --- Demographics loaders ---

_SRC_GEO = "SILVER_PROD.GEOR_GEOREF.GEO_PLACE"


@st.cache_data(ttl=86400, show_spinner=False)
def get_demographics(_session, place_id, parent_ad06=None):
    """Socio-economic stats from source geo STATISTICS field.

    For AD08 cities, pass parent_ad06 to look up the district stats.
    Returns {} if the source geo table is unavailable/unauthorized for the
    current role, so callers degrade gracefully instead of crashing.
    """
    lookup_id = parent_ad06 if parent_ad06 else place_id
    try:
        df = _session.sql(f"""
            SELECT
                STATISTICS:"STATS.DEM.AVG_AGE"::FLOAT AS "avg_age",
                STATISTICS:"STATS.DEM.POP"::INT AS "population",
                STATISTICS:"STATS.DEM.DENSITY"::FLOAT AS "density",
                STATISTICS:"STATS.FAM.HOUSEHOLDS"::INT AS "households",
                STATISTICS:"STATS.FAM.HH_SINGLE"::INT AS "hh_single",
                STATISTICS:"STATS.FAM.HH_FAMILY"::INT AS "hh_family",
                STATISTICS:"STATS.FAM.PCT_HH_SINGLE"::FLOAT AS "pct_hh_single",
                STATISTICS:"STATS.FAM.PCT_HH_FAMILY"::FLOAT AS "pct_hh_family"
            FROM {_SRC_GEO}
            WHERE ID = ? AND ACTIVE = TRUE
            QUALIFY ROW_NUMBER() OVER (ORDER BY SNAPSHOT_DATE DESC) = 1
        """, params=[lookup_id]).to_pandas()
    except Exception:
        # Source geo table not accessible for this role — degrade gracefully.
        return {}
    return df.iloc[0].to_dict() if not df.empty else {}


@st.cache_data(ttl=86400, show_spinner=False)
def get_population(_session, place_id):
    """Registered population for a place, from the prepared GEO_PLACES table.

    Fallback for the Population highlight when demographic stats are unavailable.
    """
    try:
        df = _session.sql(f"""
            SELECT POPULATION AS "population"
            FROM {_SCHEMA}.GEO_PLACES
            WHERE PLACE_ID = ?
            LIMIT 1
        """, params=[place_id]).to_pandas()
    except Exception:
        return None
    if df.empty or pd.isna(df.iloc[0]["population"]):
        return None
    return int(df.iloc[0]["population"])


# --- Interest rates loaders ---

@st.cache_data(ttl=86400, show_spinner=False)
def get_interest_rates(_session):
    """Monthly interest rate history (10yr fixed, 90% LTV)."""
    return _session.sql(f"""
        SELECT RATE_DATE, NOMINAL_RATE, NOMINAL_RATE_PREV, RATE_CHANGE, EFFECTIVE_RATE, TREND
        FROM {_SCHEMA}.INTEREST_RATES
        WHERE FIXED_PERIOD = 10
        ORDER BY RATE_DATE
    """).to_pandas()


# --- Purchasing power loaders ---

@st.cache_data(ttl=3600, show_spinner=False)
def get_purchasing_power(_session, place_id):
    """Current purchasing power (m² affordable) for a place."""
    return _session.sql(f"""
        SELECT ITEM_TYPE, PRICE_SQM,
               PP_SQM_SINGLE_W_REALTOR, PP_SQM_FAMILY_W_REALTOR,
               PP_SQM_SINGLE_NO_REALTOR, PP_SQM_FAMILY_NO_REALTOR,
               ANNUAL_RATE, MORTGAGE_FACTOR, TAX_RATE
        FROM {_SCHEMA}.PURCHASING_POWER
        WHERE PLACE_ID = ?
    """, params=[place_id]).to_pandas()


@st.cache_data(ttl=3600, show_spinner=False)
def get_purchasing_power_history(_session, place_id):
    """Monthly historical purchasing power for a place (all item types)."""
    return _session.sql(f"""
        SELECT ITEM_TYPE, INDEX_DATE,
               PP_SQM_SINGLE_W_REALTOR, PP_SQM_FAMILY_W_REALTOR,
               PP_SQM_SINGLE_NO_REALTOR, PP_SQM_FAMILY_NO_REALTOR
        FROM {_SCHEMA}.PURCHASING_POWER_HISTORY
        WHERE PLACE_ID = ?
        ORDER BY ITEM_TYPE, INDEX_DATE
    """, params=[place_id]).to_pandas()


# --- Demand loaders ---

@st.cache_data(ttl=3600, show_spinner=False)
def get_demand(_session, place_name, place_type):
    """Monthly demand (clicks + leads) for a place by name."""
    level_map = {"AD02": "NATIONAL", "AD04": "REGION", "AD06": "PROVINCE", "AD08": "PROVINCE"}
    level = level_map.get(place_type, "PROVINCE")
    lookup_name = "Deutschland" if place_type == "AD02" else place_name
    return _session.sql(f"""
        SELECT MONTH_DATE, CLICKS_BUY, CLICKS_RENT, SEARCH_VIEWS_BUY, SEARCH_VIEWS_RENT, LEADS_BUY, LEADS_RENT
        FROM {_SCHEMA}.DEMAND_MONTHLY
        WHERE PLACE_NAME = ? AND PLACE_LEVEL = ?
        ORDER BY MONTH_DATE
    """, params=[lookup_name, level]).to_pandas()


# --- Listings insights loaders ---

@st.cache_data(ttl=3600, show_spinner=False)
def get_listings_insights(_session, place_name, place_type, txn_type):
    """Monthly listings metrics (supply, time on market, size) for a place."""
    level_map = {"AD02": "NATIONAL", "AD04": "REGION", "AD06": "PROVINCE", "AD08": "PROVINCE"}
    level = level_map.get(place_type, "PROVINCE")
    lookup_name = "Deutschland" if place_type == "AD02" else place_name
    txn = "BUY" if "SELL" in txn_type else "RENT"
    return _session.sql(f"""
        SELECT MONTH_DATE, ESTATE_TYPE, NEW_LISTINGS, ACTIVE_LISTINGS,
               MEDIAN_DAYS_ON_MARKET, AVG_PRICE_SQM, AVG_SIZE_SQM, AVG_ROOMS
        FROM {_SCHEMA}.LISTINGS_INSIGHTS
        WHERE PLACE_NAME = ? AND PLACE_LEVEL = ?
          AND TXN_TYPE = ?
          AND ESTATE_TYPE IN ('APARTMENT', 'HOUSE')
        ORDER BY ESTATE_TYPE, MONTH_DATE
    """, params=[lookup_name, level, txn]).to_pandas()

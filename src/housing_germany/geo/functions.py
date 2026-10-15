"""
Snowflake-backed geographic functions.

Requires a Snowpark session. Ported from market_insights.py.
"""
from __future__ import annotations

import pandas as pd

GEO_TABLE = "SILVER_PROD.GEOR_GEOREF.GEO_PLACE"
_PARENT_COL = {"AD04": "AD04", "AD06": "AD06", "AD08": "AD08"}
_DE_FILTER = "AND ID LIKE '____DE%'"


def load_geo(session, place_type: str) -> pd.DataFrame:
    """Load id, name, display_name, population, lat, lon, parent ids for a level."""
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


def load_geo_polygons(session, place_type: str) -> pd.DataFrame:
    """Load id, name + simplified GeoJSON polygon string for choropleths."""
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


def children_of(session, parent_id: str, parent_level: str, child_type: str) -> pd.DataFrame:
    """
    Child places (with coordinates) whose parent_level parent equals parent_id.
    e.g. children_of(s, ad06_id, 'AD06', 'AD08') for cities in a district.
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


def parent_at_level(session, place_id: str) -> dict:
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
    """Small (id, name, type) directory for autofill search across admin levels."""
    type_list = ",".join(f"'{t}'" for t in types if t in ("AD04", "AD05", "AD06", "AD08", "AD09"))
    return session.sql(
        f"""
        SELECT ID AS "place_id", MAX(NAME) AS "name", MAX(TYPE) AS "type"
        FROM {GEO_TABLE}
        WHERE TYPE IN ({type_list}) {_DE_FILTER}
        GROUP BY ID
        """
    ).to_pandas()

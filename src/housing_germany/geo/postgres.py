"""
Postgres-backed geographic helper functions.

Ported from indice_functions.py and price_functions.py (iwt_barometer pipeline).
These functions are used by the indices and prices modules for geo lookups.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from functools import lru_cache


def init_tab(geoid_list: list[str], conn) -> pd.DataFrame:
    """Get basic info (id, type, title) for a list of geo IDs."""
    geoid_list1 = "','".join(geoid_list)
    sql = f"SELECT id, type, name AS title, '' AS zip, '' AS cog FROM miracle.feature WHERE id IN ('{geoid_list1}')"
    return pd.read_sql(sql, conn).drop_duplicates()


def get_type(geo_id: str, conn) -> str:
    """Get the type/key of a single geo ID."""
    sql = f"SELECT key FROM miracle.feature WHERE id = '{geo_id}' LIMIT 1"
    result = pd.read_sql(sql, conn)
    if result.empty:
        raise ValueError(f"No geo entity found for id: {geo_id}")
    return result['key'].iloc[0]


def get_type_hierarchy_level(entity_type: str) -> int:
    """Map entity type to hierarchy level number."""
    hierarchy = {
        'AD02DE': 10,
        'AD04DE': 20,
        'AD06DE': 35,
        'AD08DE': 40,
    }
    return hierarchy.get(entity_type, 100)


def find_small_entity_in_bigger_entity(big_entity_list_id, big_entity_geotype,
                                        small_entity_geotype, conn) -> pd.DataFrame:
    """Find smaller geo entities inside a bigger one via miracle.feature.parents."""
    big_entity_list_id_str = "','".join(big_entity_list_id)
    sql = f"""
    SELECT
        child.id,
        child.name,
        child.type,
        parent.id AS parent_id,
        parent.name AS parent_name
    FROM
        miracle.feature AS child,
        jsonb_array_elements_text(child.parents) AS parent_id_element
    JOIN
        miracle.feature AS parent ON parent_id_element = parent.id
    WHERE
        child.type = '{small_entity_geotype}' AND parent.id IN ('{big_entity_list_id_str}');
    """
    result = pd.read_sql(sql, conn)
    result = result.rename(columns={
        'parent_id': f"{big_entity_geotype.lower()}_id",
        'parent_name': f"{big_entity_geotype.lower()}_title",
    })
    return result


def calculate_weight_apa_house(geoid_list: list[str], conn) -> pd.DataFrame:
    """Get apartment and house counts for volume weighting."""
    geoid_list1 = "','".join(geoid_list)
    sql = f"""
    SELECT
        id,
        (statistics->>'STATS.DE.MA.HOUSING.APARTMENT_COUNT')::float AS nbapa,
        (statistics->>'STATS.DE.MA.HOUSING.HOUSE_COUNT')::float AS nbma
    FROM miracle.feature
    WHERE id IN ('{geoid_list1}')
    """
    return pd.read_sql(sql, conn)


def get_last_price_date(conn, schema='price', transaction_type='TRANSACTION_TYPE.SELL',
                        batch_id=None) -> str:
    """Get the most recent price date from the price database."""
    sql = f"""
    SELECT MAX(DATE(created)) AS price_date
    FROM {schema}.price
    """
    result = pd.read_sql(sql, conn)
    d = result.price_date.iloc[0]
    return f"{d.year}-{d.month:02d}-{d.day:02d}"


def request_actual_price_classical(geoid_list, entity, conn,
                                   schema='price', transaction_type='TRANSACTION_TYPE.SELL',
                                   batch_id=None) -> pd.DataFrame:
    """Get current prices for a list of geo IDs."""
    geoid_str = "','".join(geoid_list)
    sql = f"""
    SELECT place_id AS id, item_type, value AS price_value
    FROM {schema}.price
    WHERE place_id IN ('{geoid_str}')
    AND transaction_type = '{transaction_type}'
    AND DATE(created) = (SELECT MAX(DATE(created)) FROM {schema}.price)
    """
    df = pd.read_sql(sql, conn)
    if df.empty:
        return pd.DataFrame()

    # Pivot item types into columns
    pivot = df.pivot_table(index='id', columns='item_type', values='price_value', aggfunc='first')
    pivot.columns = [f"{col.split('.')[-1].lower()}_value" for col in pivot.columns]
    return pivot.reset_index()


def get_district_types(conn_ind) -> pd.DataFrame:
    """Get district type classification (Stadtkreis vs Landkreis)."""
    sql = """
    SELECT
        miracle.id AS id,
        INITCAP(miracle.name) AS District,
        CASE
            WHEN geo.subtype = 1 THEN 'Stadtkreis'
            WHEN geo.subtype = 2 THEN 'Landkreis'
            ELSE 'Other'
        END AS district_type
    FROM miracle_latest.feature AS miracle
    JOIN geo.geo_place2 AS geo
        ON miracle.legacy_ma_id = geo.id
    WHERE miracle.key = 'AD06DE'
    GROUP BY miracle.id, miracle.name, geo.subtype
    """
    return pd.read_sql(sql, conn_ind)


def get_top_cities(conn, top_n: int = 200) -> pd.DataFrame:
    """Get top cities by population."""
    sql = """
    SELECT
        id,
        name AS title,
        (statistics ->> 'STATS.DEM.POP')::bigint AS population
    FROM miracle.feature
    WHERE
        key = 'AD08DE'
        AND statistics ? 'STATS.DEM.POP'
    ORDER BY population DESC
    """
    return pd.read_sql(sql, conn).head(top_n)

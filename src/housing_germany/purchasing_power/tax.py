"""
German real estate tax (Grunderwerbsteuer) rates and lookup functions.
"""
from __future__ import annotations

import pandas as pd


def get_tax_rates_DE() -> dict[str, float]:
    """Return German Grunderwerbsteuer rates by Bundesland."""
    return {
        'Baden-Württemberg': 0.05,
        'Bayern': 0.035,
        'Berlin': 0.06,
        'Brandenburg': 0.065,
        'Bremen': 0.05,
        'Hamburg': 0.045,
        'Hessen': 0.06,
        'Mecklenburg-Vorpommern': 0.06,
        'Niedersachsen': 0.05,
        'Nordrhein-Westfalen': 0.065,
        'Rheinland-Pfalz': 0.05,
        'Saarland': 0.065,
        'Sachsen': 0.035,
        'Sachsen-Anhalt': 0.05,
        'Schleswig-Holstein': 0.065,
        'Thüringen': 0.065,
    }


def get_tax_by_geo(geoid_list: list[str], conn_mkt, geotype: str = 'AD06DE') -> pd.DataFrame:
    """
    Get tax rates for geo IDs by looking up their parent Bundesland.

    Args:
        geoid_list: list of geo IDs
        conn_mkt: database connection (psycopg2)
        geotype: geo type key (AD06DE for Kreise, AD04DE for Laender, AD02DE for country)

    Returns:
        DataFrame with id and tax columns
    """
    geoid_str = "','".join(str(g) for g in geoid_list)
    tax_rates = get_tax_rates_DE()

    if geotype == 'AD02DE':
        return pd.DataFrame({
            'id': geoid_list,
            'tax': [0.05] * len(geoid_list),
        })

    if geotype == 'AD04DE':
        sql = f"""
            SELECT id, display_name
            FROM miracle.feature
            WHERE id IN ('{geoid_str}')
            AND active = True
        """
        geo = pd.read_sql(sql, conn_mkt)
        geo['display_name'] = geo['display_name'].str.strip().str.rstrip(',').str.strip()
        geo['tax'] = geo['display_name'].map(tax_rates).fillna(0.05)
        return geo[['id', 'tax']]

    # AD06DE or AD08DE: look up parent Bundesland
    sql = f"""
        SELECT
            child.id,
            parent.display_name AS bundesland
        FROM miracle.feature AS child
        JOIN miracle.feature AS parent
            ON parent.id = ANY(
                SELECT jsonb_array_elements_text(child.parents)
            )
        WHERE child.id IN ('{geoid_str}')
        AND parent.key = 'AD04DE'
        AND parent.active = True
    """
    geo = pd.read_sql(sql, conn_mkt)
    geo['bundesland'] = geo['bundesland'].str.strip().str.rstrip(',').str.strip()
    geo['tax'] = geo['bundesland'].map(tax_rates).fillna(0.05)
    return geo[['id', 'tax']]

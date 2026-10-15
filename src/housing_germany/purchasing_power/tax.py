"""
German real estate tax (Grunderwerbsteuer) rates and lookup functions.
"""
from __future__ import annotations

import pandas as pd

from housing_germany.geo.functions import GEO_TABLE, _DE_FILTER


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


def get_tax_by_geo(geoid_list: list[str], session, geotype: str = 'AD06DE') -> pd.DataFrame:
    """
    Get tax rates for geo IDs by looking up their parent Bundesland.

    Args:
        geoid_list: list of geo IDs
        session: Snowpark session
        geotype: geo type key (AD06DE for Kreise, AD04DE for Laender, AD02DE for country)

    Returns:
        DataFrame with id and tax columns
    """
    tax_rates = get_tax_rates_DE()

    if geotype == 'AD02DE':
        return pd.DataFrame({
            'id': geoid_list,
            'tax': [0.05] * len(geoid_list),
        })

    geoid_str = "','".join(str(g) for g in geoid_list)

    if geotype == 'AD04DE':
        df = session.sql(
            f"""
            SELECT ID AS "id", MAX(NAME) AS "name"
            FROM {GEO_TABLE}
            WHERE ID IN ('{geoid_str}') {_DE_FILTER}
            GROUP BY ID
            """
        ).to_pandas()
        df['tax'] = df['name'].map(tax_rates).fillna(0.05)
        return df[['id', 'tax']]

    # AD06DE or AD08DE: look up parent Bundesland via AD04 array
    df = session.sql(
        f"""
        SELECT ID AS "id",
               MAX(AD04_NAMES[0]::string) AS "bundesland"
        FROM {GEO_TABLE}
        WHERE ID IN ('{geoid_str}') {_DE_FILTER}
        GROUP BY ID
        """
    ).to_pandas()
    df['bundesland'] = df['bundesland'].str.strip().str.rstrip(',').str.strip()
    df['tax'] = df['bundesland'].map(tax_rates).fillna(0.05)
    return df[['id', 'tax']]

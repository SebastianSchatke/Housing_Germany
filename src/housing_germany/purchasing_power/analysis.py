"""
Purchasing power analysis and summary builders.

Extracted from the purchasing_power notebook workflow.
"""
from __future__ import annotations

import pandas as pd
from dateutil.relativedelta import relativedelta


def build_purchasing_power_summary(
    purchasing_power_frames: dict,
    dict_prep_price_evol: dict,
    geo_names: pd.DataFrame,
    geo_config: dict | None = None,
) -> dict:
    """
    Build summary DataFrames from purchasing_power_frames.

    Args:
        purchasing_power_frames: dict of {level: {place_id: DataFrame}} from purchasing_power_DE
        dict_prep_price_evol: mapping of level names to geo keys
        geo_names: DataFrame with id, key, display_name columns
        geo_config: optional geo hierarchy config

    Returns:
        Dict like {'apartment': {level: DataFrame}, 'house': {level: DataFrame}}
    """
    if geo_config is None:
        geo_config = {
            'country': {'key': 'AD02DE', 'id_col': 'country_id', 'name_col': 'country'},
            'state': {'key': 'AD04DE', 'id_col': 'state_id', 'name_col': 'state'},
            'district': {'key': 'AD06DE', 'id_col': 'district_id', 'name_col': 'district'},
            'city': {'key': 'AD08DE', 'id_col': 'city_id', 'name_col': 'city'},
        }

    summary = {'apartment': {}, 'house': {}}

    for title, granularity in dict_prep_price_evol.items():
        if title not in purchasing_power_frames:
            continue

        apartment_data = {}
        house_data = {}

        for place_id in purchasing_power_frames[title].keys():
            df = purchasing_power_frames[title][place_id]
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

            apartment_data[place_id] = df.set_index('date')['apartment_purchasing_power'].round(1)
            house_data[place_id] = df.set_index('date')['house_purchasing_power'].round(1)

        apartment_frame = pd.DataFrame(apartment_data).T.reset_index().rename(columns={'index': 'id'})
        house_frame = pd.DataFrame(house_data).T.reset_index().rename(columns={'index': 'id'})

        # Add geo names
        config = geo_config.get(title, {})
        id_col = config.get('id_col', f'{title}_id')
        name_col = config.get('name_col', title)
        key = config.get('key', granularity)

        names = geo_names[geo_names['key'] == key][['id', 'display_name']].copy()
        names['display_name'] = names['display_name'].str.strip().str.rstrip(',').str.strip()

        for frame in [apartment_frame, house_frame]:
            frame.rename(columns={'id': id_col}, inplace=True)
            merged = frame.merge(names, left_on=id_col, right_on='id', how='left')
            merged = merged.rename(columns={'display_name': name_col})
            merged = merged.drop(columns=['id'], errors='ignore')

        summary['apartment'][title] = apartment_frame
        summary['house'][title] = house_frame

    return summary


def purchasing_power_change(
    df: pd.DataFrame,
    target_places: list[str],
    place_col: str = 'district',
    horizons: dict | None = None,
) -> pd.DataFrame:
    """
    Calculate purchasing power change over time horizons.

    Args:
        df: Summary DataFrame with place names as rows and date columns
        target_places: List of place names to include
        place_col: Column name containing place names
        horizons: Dict of {label: relativedelta offset}. Defaults to standard horizons.

    Returns:
        DataFrame with % change for each horizon
    """
    if horizons is None:
        horizons = {
            '3M ago': relativedelta(months=3),
            '6M ago': relativedelta(months=6),
            '1Y ago': relativedelta(years=1),
            '2Y ago': relativedelta(years=2),
            '5Y ago': relativedelta(years=5),
        }

    date_cols = [c for c in df.columns if c not in [place_col] and not c.endswith('_id')]
    df_dates = pd.to_datetime(date_cols)

    latest_col = date_cols[-1]
    latest_date = df_dates[-1]

    def col_at_offset(off):
        target = latest_date - off
        return min(zip(df_dates, date_cols), key=lambda x: abs((x[0] - target).days))

    horizon_info = {}
    for label, off in horizons.items():
        past_date, past_col = col_at_offset(off)
        nice_label = f"{label} ({past_date:%b %Y})"
        horizon_info[nice_label] = past_col

    sub = df.set_index(place_col).loc[target_places].copy()

    out = pd.DataFrame(index=sub.index)
    for label, past_col in horizon_info.items():
        out[label] = (sub[latest_col] / sub[past_col] - 1)

    out.index.name = 'Place'
    return out

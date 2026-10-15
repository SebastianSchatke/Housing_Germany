"""
Postgres-backed aggregate index functions.

Ported from indices_aggregation.py (iwt_barometer pipeline).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from housing_germany.indices.core import calcul_evol_from_series, calcul_rebase
from housing_germany.indices.postgres import get_indices_house_apa


def add_count(df, geoid_list, conn_prix):
    """Add apartment and house counts to the dataframe."""
    from housing_germany.geo.postgres import calculate_weight_apa_house
    from housing_germany.indices.core import find_first_date_rebase

    weights = calculate_weight_apa_house(geoid_list, conn_prix)
    output = df.merge(weights, how="left", on="id")

    output["date_ref"] = output["id"].apply(
        lambda x: find_first_date_rebase(output.loc[output.id == x, "dates"].tolist())
    )
    output["log_count"] = np.nan
    output.loc[output.item_type == "INDICE.METRIC.PRIX_VENTE_APPARTEMENTS_ANCIENS", "log_count"] = output["nbapa"]
    output.loc[output.item_type == "INDICE.METRIC.PRIX_VENTE_MAISONS_ANCIENNES", "log_count"] = output["nbma"]
    return output


def get_raw_indices(geo_id_list, entity, conn_indice, conn_prix,
                    schema='price', transaction_type='TRANSACTION_TYPE.SELL',
                    batch_id=None):
    """Get raw indices with counts, handling geo hierarchy."""
    from housing_germany.geo.postgres import (
        get_type, get_type_hierarchy_level, find_small_entity_in_bigger_entity,
    )

    type_id = get_type(geo_id_list[0], conn_prix)
    id_list_to_process = geo_id_list

    if get_type_hierarchy_level(type_id) < get_type_hierarchy_level(entity):
        result1 = find_small_entity_in_bigger_entity(geo_id_list, type_id, entity, conn_prix)
        id_list_to_process = result1.id.tolist()
        if not id_list_to_process:
            return pd.DataFrame()
        indices = get_indices_house_apa(
            id_list_to_process, conn_indice, conn_prix,
            schema=schema, transaction_type=transaction_type, batch_id=batch_id,
        )
        indices = result1.merge(indices, how="left", on="id")
    else:
        indices = get_indices_house_apa(
            id_list_to_process, conn_indice, conn_prix,
            schema=schema, transaction_type=transaction_type, batch_id=batch_id,
        )

    if indices.empty:
        return pd.DataFrame()

    indices_with_counts = add_count(indices, id_list_to_process, conn_prix)
    return indices_with_counts.sort_values(by=indices_with_counts.columns[-1], ascending=False)


def rebase_indices_common(df, rebase_date, date_ref_col='date_ref'):
    """Common logic for rebasing indices."""
    rebased_dates_list = []
    rebased_indices_list = []
    message_printed = False

    for i, row in df.iterrows():
        if not isinstance(row.get('values'), (list, np.ndarray, pd.Series)):
            rebased_dates_list.append([])
            rebased_indices_list.append([])
            continue

        indice = pd.Series(row["values"], index=row['dates']).to_frame().rename(columns={0: "indice"})
        indice.index = pd.to_datetime(indice.index)

        cut_date = rebase_date if rebase_date else row.get(date_ref_col)
        if pd.isna(cut_date):
            rebased_dates_list.append([])
            rebased_indices_list.append([])
            continue

        cut_date = pd.to_datetime(cut_date)

        if not indice.empty and cut_date < indice.index.min():
            if not message_printed:
                print(f"Rebase date {cut_date.date()} is before the start of some indices. "
                      "Using index start date instead.")
                message_printed = True
            cut_date = indice.index.min()

        indice = indice[indice.index >= cut_date]
        if indice.empty:
            rebased_dates_list.append([])
            rebased_indices_list.append([])
            continue

        indice['evol'] = calcul_evol_from_series(indice['indice'])
        indice['rebase'] = calcul_rebase(indice['evol'])
        rebased_dates_list.append([d.date() for d in indice.index])
        rebased_indices_list.append(list(indice['rebase']))

    df['rebased_dates'] = rebased_dates_list
    df['rebased_indices'] = rebased_indices_list
    return df


def rebase_indices(geo_id_list, entity, conn_indice, conn_prix, rebase_date=None,
                   schema='price', transaction_type='TRANSACTION_TYPE.SELL',
                   batch_id=None):
    """Rebase indices for a list of geo IDs."""
    df = get_raw_indices(geo_id_list, entity, conn_indice, conn_prix,
                         schema=schema, transaction_type=transaction_type, batch_id=batch_id)
    if df.empty:
        return df
    return rebase_indices_common(df, rebase_date)


def final_indices(geo_id_list, entity, conn_indice, conn_prix, plot,
                  rebase_date=None, schema='price',
                  transaction_type='TRANSACTION_TYPE.SELL', batch_id=None):
    """Compute individual place indices with optional plotting."""
    from housing_germany.geo.postgres import init_tab

    df_rebase = rebase_indices(
        geo_id_list, entity, conn_indice, conn_prix,
        rebase_date=rebase_date, schema=schema,
        transaction_type=transaction_type, batch_id=batch_id,
    )
    indice_dict = {}

    for geo_id in df_rebase['id'].unique():
        name = init_tab([geo_id], conn_prix).title.iloc[0]
        df = df_rebase[df_rebase["id"] == geo_id]
        if df.empty:
            continue

        unique_dates = pd.to_datetime(df['rebased_dates'].explode().dropna().unique()).sort_values()
        if unique_dates.empty:
            continue

        series_dict = {
            'hyb_indice':   pd.Series(0, index=unique_dates),
            'apa_indice':   pd.Series(0, index=unique_dates),
            'house_indice': pd.Series(0, index=unique_dates),
        }
        counts = {'hyb_indice': 0, 'apa_indice': 0, 'house_indice': 0}

        for _, row in df.iterrows():
            if not row.get('rebased_indices') or pd.isna(row.get('log_count')):
                continue
            series = pd.Series(row.rebased_indices, index=pd.to_datetime(row.rebased_dates))
            counts['hyb_indice'] += row.log_count
            series_dict['hyb_indice'] = series_dict['hyb_indice'].add(row.log_count * series, fill_value=0)

            if row.item_type == 'INDICE.METRIC.PRIX_VENTE_APPARTEMENTS_ANCIENS':
                counts['apa_indice'] += row.log_count
                series_dict['apa_indice'] = series_dict['apa_indice'].add(row.log_count * series, fill_value=0)
            else:
                counts['house_indice'] += row.log_count
                series_dict['house_indice'] = series_dict['house_indice'].add(row.log_count * series, fill_value=0)

        for key in series_dict:
            if counts[key] > 0:
                series_dict[key] /= counts[key]

        output = pd.DataFrame(series_dict)
        indice_dict[geo_id] = output

        if plot:
            plt.figure(figsize=(15, 5))
            plt.title(f"{name}")
            plt.plot(output.index, output['hyb_indice'], label=f'{name} - HYB', color='b')
            plt.plot(output.index, output['apa_indice'], label=f'{name} - APA', color='r')
            plt.plot(output.index, output['house_indice'], label=f'{name} - HOUSE', color='g')
            plt.legend()
            plt.show()

    return indice_dict


def rebase_indices_aggregate(geo_id_list, entity, conn_indice, conn_prix,
                             rebase_date=None, schema='price',
                             transaction_type='TRANSACTION_TYPE.SELL', batch_id=None):
    """Rebase indices for aggregation (uses most recent date_ref across all)."""
    df = get_raw_indices(geo_id_list, entity, conn_indice, conn_prix,
                         schema=schema, transaction_type=transaction_type, batch_id=batch_id)
    if df.empty:
        return df
    most_recent_date_ref = df['date_ref'].max()
    effective_rebase_date = rebase_date if rebase_date else most_recent_date_ref
    return rebase_indices_common(df, effective_rebase_date, date_ref_col=None)


def final_aggregate_indice(geo_id_list, entity, conn_indice, conn_prix, plot,
                           rebase_date=None, name=None, schema='price',
                           transaction_type='TRANSACTION_TYPE.SELL', batch_id=None):
    """Compute aggregate index across places with optional plotting."""
    df_rebase = rebase_indices_aggregate(
        geo_id_list, entity, conn_indice, conn_prix,
        rebase_date=rebase_date, schema=schema,
        transaction_type=transaction_type, batch_id=batch_id,
    )
    if df_rebase.empty:
        return pd.DataFrame()

    unique_dates = pd.to_datetime(df_rebase['rebased_dates'].explode().dropna().unique()).sort_values()
    if unique_dates.empty:
        return pd.DataFrame()

    series_dict = {
        'hyb_indice':   pd.Series(0, index=unique_dates),
        'apa_indice':   pd.Series(0, index=unique_dates),
        'house_indice': pd.Series(0, index=unique_dates),
    }
    counts = {'hyb_indice': 0, 'apa_indice': 0, 'house_indice': 0}

    for _, row in df_rebase.iterrows():
        if not row.get('rebased_indices') or pd.isna(row.get('log_count')):
            continue
        series = pd.Series(row.rebased_indices, index=pd.to_datetime(row.rebased_dates))
        counts['hyb_indice'] += row.log_count
        series_dict['hyb_indice'] = series_dict['hyb_indice'].add(row.log_count * series, fill_value=0)

        if row.item_type == 'INDICE.METRIC.PRIX_VENTE_APPARTEMENTS_ANCIENS':
            counts['apa_indice'] += row.log_count
            series_dict['apa_indice'] = series_dict['apa_indice'].add(row.log_count * series, fill_value=0)
        else:
            counts['house_indice'] += row.log_count
            series_dict['house_indice'] = series_dict['house_indice'].add(row.log_count * series, fill_value=0)

    for key in series_dict:
        if counts[key] > 0:
            series_dict[key] /= counts[key]

    output = pd.DataFrame(series_dict)

    if plot:
        if name is None:
            raise ValueError("If 'plot' is True, you must provide a 'name' for the aggregate.")
        plt.figure(figsize=(15, 5))
        plt.title(f"{name}")
        plt.plot(output.index, output['hyb_indice'], label=f'{name} - HYB', color='b')
        plt.plot(output.index, output['apa_indice'], label=f'{name} - APA', color='r')
        plt.plot(output.index, output['house_indice'], label=f'{name} - HOUSE', color='g')
        plt.legend()
        plt.show()

    return output

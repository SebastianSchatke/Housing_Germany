"""
Postgres-backed index functions.

Requires psycopg2 connections to the indice and price databases.
Ported from indice_functions.py (iwt_barometer pipeline).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta

from housing_germany.indices.core import (
    calcul_evol_from_series,
    calcul_rebase,
    find_first_date_rebase,
)


def get_indices_house_apa(geoid_list, conn_indice, conn_prix, schema='price',
                          transaction_type='TRANSACTION_TYPE.SELL', batch_id=None):
    """Fetch house/apartment indices from clustered_indice."""
    from housing_germany.geo.postgres import get_last_price_date, get_type

    most_recent_price_date = get_last_price_date(
        conn_prix, schema=schema,
        transaction_type=transaction_type, batch_id=batch_id,
    )
    geoid_list1 = "','".join(geoid_list)

    sql = f"""
    SELECT
        geo.place_id AS id,
        geo.place_type AS type,
        geo.title AS title,
        '' AS zip,
        '' AS cog,
        indice.indice_dates AS dates,
        indice.indice_values AS values,
        indice.item_type
    FROM clustered_indice.cluster_content geo
    LEFT JOIN clustered_indice.indice_long indice
        ON geo.place_id = indice.place_id
        AND indice.transaction_type = '{transaction_type}'
        AND indice.item_type IN ('ITEM_TYPE.HOUSE','ITEM_TYPE.APARTMENT')
        AND indice.price_date = '{most_recent_price_date}'
        AND indice.metric = 'METRIC.SQM_PRICE'
    WHERE
        geo.place_id IN ('{geoid_list1}')
        AND geo.enabled = TRUE
    """
    value_var = pd.read_sql(sql, conn_indice)
    value_var["item_type"] = value_var["item_type"].replace({
        "ITEM_TYPE.HOUSE": "INDICE.METRIC.PRIX_VENTE_MAISONS_ANCIENNES",
        "ITEM_TYPE.APARTMENT": "INDICE.METRIC.PRIX_VENTE_APPARTEMENTS_ANCIENS"
    })

    value_var["values"] = value_var["values"].fillna(np.nan)
    no_ind = list(value_var.loc[value_var["values"].isna(), "title"].unique())
    if len(no_ind) != 0:
        print("NO INDICES FOR : ", no_ind)
    value_var = value_var[value_var["values"].notna()]
    if len(value_var) == 0:
        raise ValueError("No Indices at all for the given ids")

    return value_var


def get_hybrid_indice(geoid_list, conn_indice, conn_prix, schema='price',
                      transaction_type='TRANSACTION_TYPE.SELL', batch_id=None):
    """Compute volume-weighted hybrid index from house + apartment components."""
    from housing_germany.geo.postgres import calculate_weight_apa_house

    df_indice1 = get_indices_house_apa(geoid_list, conn_indice, conn_prix,
                                       schema=schema, transaction_type=transaction_type,
                                       batch_id=batch_id)
    df_indice = df_indice1.merge(calculate_weight_apa_house(geoid_list, conn_prix),
                                 how="left", on="id")
    df_indice["date_ref"] = df_indice["id"].apply(
        lambda x: find_first_date_rebase(df_indice.loc[df_indice.id == x, "dates"].tolist())
    )
    df_indice["log_count"] = np.nan
    df_indice.loc[df_indice.item_type == "INDICE.METRIC.PRIX_VENTE_APPARTEMENTS_ANCIENS", "log_count"] = \
        df_indice.loc[df_indice.item_type == "INDICE.METRIC.PRIX_VENTE_APPARTEMENTS_ANCIENS", "nbapa"]
    df_indice.loc[df_indice.item_type == "INDICE.METRIC.PRIX_VENTE_MAISONS_ANCIENNES", "log_count"] = \
        df_indice.loc[df_indice.item_type == "INDICE.METRIC.PRIX_VENTE_MAISONS_ANCIENNES", "nbma"]

    dict_indice = {entity: df_indice[df_indice['id'] == entity] for entity in df_indice.id.unique()}
    df_indice_agg = _aggregate_rebase_indices(dict_indice)

    result = pd.DataFrame()
    for key, s in df_indice_agg.items():
        info = df_indice1[df_indice1.id == key].iloc[0]
        row = {'id': key, 'type': info['type'], 'title': info['title'], 'zip': info['zip'], 'cog': info['cog'],
               'dates': s.index.tolist(), 'values': s.values.tolist(), 'item_type': "HYBRID"}
        result = pd.concat([result, pd.DataFrame([row])], ignore_index=True)
    return result


def _rebase_indices(dictionary):
    """Rebase indices in a dictionary of DataFrames."""
    for name, df in dictionary.items():
        rebased_dates_list, rebased_indices_list = [], []
        for i, row in df.iterrows():
            indice = pd.Series(row["values"], index=row['dates']).to_frame().rename(columns={0: "indice"})
            indice.index = pd.to_datetime(indice.index)
            if row.date_ref is None:
                continue
            indice = indice[row.date_ref:]
            indice['evol'] = calcul_evol_from_series(indice['indice'])
            indice['rebase'] = calcul_rebase(indice['evol'])
            rebased_dates_list.append([d.date() for d in indice.index])
            rebased_indices_list.append(list(indice['rebase']))
        df['rebased_dates'] = rebased_dates_list
        df['rebased_indices'] = rebased_indices_list
    return dictionary


def _aggregate_rebase_indices(dictionary):
    """Volume-weighted aggregation of rebased indices."""
    hyb_dict = {}
    dictionary_rebase = _rebase_indices(dictionary)
    for name, df in dictionary_rebase.items():
        if df.empty or 'rebased_dates' not in df.columns or df['rebased_dates'].iloc[0] is None:
            continue
        unique_dates = pd.to_datetime(df['rebased_dates'].explode().unique()).sort_values()
        hyb_indice = pd.Series(0, index=unique_dates)
        nb_log = 0
        for i, row in df.iterrows():
            if not row.rebased_indices:
                continue
            nb_log += row.log_count
            hyb_indice = hyb_indice.add(
                row.log_count * pd.Series(row.rebased_indices,
                                          index=pd.to_datetime(row.rebased_dates)),
                fill_value=0,
            )
        if nb_log > 0:
            hyb_indice = hyb_indice / nb_log
        hyb_indice.iloc[0] = 100
        hyb_dict[name] = hyb_indice
    return hyb_dict


def get_indice_value_two_dates(geoid_list, date_to, date_from, conn_indice, conn_prix,
                               item_type, schema='price',
                               transaction_type='TRANSACTION_TYPE.SELL', batch_id=None):
    """Get index values at two specific dates for evolution calculation."""
    from housing_germany.geo.postgres import get_last_price_date

    if item_type == "apartment":
        df_indice = get_indices_house_apa(geoid_list, conn_indice, conn_prix,
                                          schema=schema, transaction_type=transaction_type,
                                          batch_id=batch_id)
        df_indice = df_indice[df_indice.item_type == 'INDICE.METRIC.PRIX_VENTE_APPARTEMENTS_ANCIENS']
    elif item_type == "house":
        df_indice = get_indices_house_apa(geoid_list, conn_indice, conn_prix,
                                          schema=schema, transaction_type=transaction_type,
                                          batch_id=batch_id)
        df_indice = df_indice[df_indice.item_type == 'INDICE.METRIC.PRIX_VENTE_MAISONS_ANCIENNES']
    elif item_type == "hybrid":
        df_indice = get_hybrid_indice(geoid_list, conn_indice, conn_prix,
                                      schema=schema, transaction_type=transaction_type,
                                      batch_id=batch_id)
    else:
        raise ValueError("Choose between apartment/house/hybrid")

    if df_indice.empty:
        return pd.DataFrame()

    def get_val_at_date(row, date):
        try:
            idx = row['dates'].index(pd.to_datetime(date) - relativedelta(months=1))
            return row['values'][idx]
        except (ValueError, IndexError):
            return np.nan

    df_indice[f"{item_type}_value_to"] = df_indice.apply(get_val_at_date, args=(date_to,), axis=1)
    df_indice[f"{item_type}_value_from"] = df_indice.apply(get_val_at_date, args=(date_from,), axis=1)
    df_indice[f"{item_type}_current_value"] = df_indice.apply(
        lambda row: row['values'][-1] if row['values'] else np.nan, axis=1
    )
    df_indice["date_to"] = pd.to_datetime(date_to)
    df_indice["date_from"] = date_from
    df_indice["current_date"] = get_last_price_date(
        conn_prix, schema=schema,
        transaction_type=transaction_type, batch_id=batch_id,
    )
    return df_indice[["id", "type", "title", "zip", "cog", "current_date", "date_to", "date_from",
                      f"{item_type}_current_value", f"{item_type}_value_to", f"{item_type}_value_from"]]


def _get_val_at_date_fast(dates_list, values_list, target_date):
    try:
        idx = dates_list.index(target_date)
        return values_list[idx]
    except (ValueError, IndexError):
        return np.nan


def get_all_indices_optimized(geoid_list, conn_indice, conn_prix, schema='price',
                              transaction_type='TRANSACTION_TYPE.SELL', batch_id=None):
    """Fetch all indices (house, apartment, hybrid) in one optimized call."""
    from housing_germany.geo.postgres import calculate_weight_apa_house

    df_base = get_indices_house_apa(geoid_list, conn_indice, conn_prix,
                                    schema=schema, transaction_type=transaction_type,
                                    batch_id=batch_id)
    if df_base.empty:
        return {'apartment': pd.DataFrame(), 'house': pd.DataFrame(), 'hybrid': pd.DataFrame()}

    df_apartment = df_base[df_base.item_type == 'INDICE.METRIC.PRIX_VENTE_APPARTEMENTS_ANCIENS'].copy()
    df_house = df_base[df_base.item_type == 'INDICE.METRIC.PRIX_VENTE_MAISONS_ANCIENNES'].copy()

    weights = calculate_weight_apa_house(geoid_list, conn_prix)
    df_indice = df_base.merge(weights, how="left", on="id")
    df_indice["date_ref"] = df_indice["id"].apply(
        lambda x: find_first_date_rebase(df_indice.loc[df_indice.id == x, "dates"].tolist())
    )
    df_indice["log_count"] = np.nan
    df_indice.loc[df_indice.item_type == "INDICE.METRIC.PRIX_VENTE_APPARTEMENTS_ANCIENS", "log_count"] = \
        df_indice.loc[df_indice.item_type == "INDICE.METRIC.PRIX_VENTE_APPARTEMENTS_ANCIENS", "nbapa"]
    df_indice.loc[df_indice.item_type == "INDICE.METRIC.PRIX_VENTE_MAISONS_ANCIENNES", "log_count"] = \
        df_indice.loc[df_indice.item_type == "INDICE.METRIC.PRIX_VENTE_MAISONS_ANCIENNES", "nbma"]

    dict_indice = {entity: df_indice[df_indice['id'] == entity] for entity in df_indice.id.unique()}
    df_indice_agg = _aggregate_rebase_indices(dict_indice)

    df_hybrid = pd.DataFrame()
    for key, s in df_indice_agg.items():
        info = df_base[df_base.id == key].iloc[0]
        row = {'id': key, 'type': info['type'], 'title': info['title'], 'zip': info['zip'], 'cog': info['cog'],
               'dates': s.index.tolist(), 'values': s.values.tolist(), 'item_type': "HYBRID"}
        df_hybrid = pd.concat([df_hybrid, pd.DataFrame([row])], ignore_index=True)

    return {'apartment': df_apartment, 'house': df_house, 'hybrid': df_hybrid}


def get_FINAL_evolution_optimized(geo_id_list, entity, date_to, date_from_list,
                                  conn_indice, conn_prix, item_type_list,
                                  schema='price', transaction_type='TRANSACTION_TYPE.SELL',
                                  batch_id=None):
    """Optimized: fetches all data once and computes all evolutions in memory."""
    from housing_germany.geo.postgres import get_last_price_date, get_type, init_tab

    id_list_to_process = geo_id_list
    if not id_list_to_process:
        return pd.DataFrame()

    actual_price_date = get_last_price_date(
        conn_prix, schema=schema,
        transaction_type=transaction_type, batch_id=batch_id,
    )

    all_indices = get_all_indices_optimized(
        id_list_to_process, conn_indice, conn_prix,
        schema=schema, transaction_type=transaction_type, batch_id=batch_id,
    )

    init = init_tab(id_list_to_process, conn_prix)
    final_result = init.copy()

    date_to_lookup = pd.to_datetime(date_to) - relativedelta(months=1)

    for item_type in item_type_list:
        df_indice = all_indices.get(item_type, pd.DataFrame())
        if df_indice.empty:
            continue

        df_indice = df_indice.copy()
        df_indice['dates_converted'] = df_indice['dates'].apply(
            lambda x: [pd.to_datetime(d) for d in x] if x else []
        )
        df_indice['current_value'] = df_indice.apply(
            lambda row: row['values'][-1] if row['values'] else np.nan, axis=1
        )
        df_indice['value_to'] = df_indice.apply(
            lambda row: _get_val_at_date_fast(row['dates_converted'], row['values'], date_to_lookup),
            axis=1,
        )

        for date_from in date_from_list:
            date_from_lookup = pd.to_datetime(date_from) - relativedelta(months=1)
            df_indice['value_from'] = df_indice.apply(
                lambda row: _get_val_at_date_fast(row['dates_converted'], row['values'], date_from_lookup),
                axis=1,
            )
            evol_data = df_indice[['id', 'current_value', 'value_to', 'value_from']].copy()
            evol_data = evol_data.groupby('id').agg({
                'current_value': 'first', 'value_to': 'first', 'value_from': 'first',
            }).reset_index()

            col_name = f"Evolution {item_type} from {actual_price_date} to {date_from}"
            evol_data[col_name] = evol_data['current_value'] / evol_data['value_from'] - 1
            final_result = final_result.merge(evol_data[['id', col_name]], how="left", on="id")

        current_date = (datetime.strptime(actual_price_date, '%Y-%m-%d')
                        - timedelta(days=1)).replace(day=1).strftime('%Y-%m-%d')
        print(f"Item Type : {item_type}\nCurrent Date Indice : {current_date}\n"
              f"Processed {len(date_from_list)} date periods")

    return (final_result.sort_values(by=final_result.columns[-1], ascending=False)
            if len(final_result.columns) > 5 else final_result)


def get_FINAL_evolution_optimized_safe(
    geo_id_list, entity, date_to, date_from_list,
    conn_indice, conn_prix, item_type_list,
    schema='price', transaction_type='TRANSACTION_TYPE.SELL',
    batch_id=None, batch_size=500,
):
    """Memory-safe, batched version of get_FINAL_evolution_optimized."""
    final_df = None
    for batch_start in range(0, len(geo_id_list), batch_size):
        batch_ids = geo_id_list[batch_start: batch_start + batch_size]
        df_batch = get_FINAL_evolution_optimized(
            batch_ids, entity, date_to, date_from_list,
            conn_indice, conn_prix, item_type_list,
            schema=schema, transaction_type=transaction_type, batch_id=batch_id,
        )
        if final_df is None:
            final_df = df_batch
        else:
            final_df = pd.concat([final_df, df_batch], ignore_index=True)

    if final_df is None or final_df.empty:
        return pd.DataFrame()

    for item_type in item_type_list:
        evol_cols = [col for col in final_df.columns if col.startswith(f"Evolution {item_type}")]
        for col in evol_cols:
            final_df[col] = pd.to_numeric(final_df[col], errors='coerce')

    return final_df


def get_FINAL_evolution(geo_id_list, entity, date_to, date_from_list,
                        conn_indice, conn_prix, item_type_list,
                        schema='price', transaction_type='TRANSACTION_TYPE.SELL',
                        batch_id=None):
    """Delegates to the optimized implementation."""
    return get_FINAL_evolution_optimized(
        geo_id_list, entity, date_to, date_from_list,
        conn_indice, conn_prix, item_type_list,
        schema=schema, transaction_type=transaction_type, batch_id=batch_id,
    )


def get_evolution_between_two_dates(geoid_list, date_to, date_from, conn_indice, conn_prix,
                                    item_type, schema='price',
                                    transaction_type='TRANSACTION_TYPE.SELL', batch_id=None):
    """Evolution between two specific dates for a given item type."""
    result1 = get_indice_value_two_dates(
        geoid_list, date_to, date_from, conn_indice, conn_prix, item_type,
        schema=schema, transaction_type=transaction_type, batch_id=batch_id,
    )
    if result1.empty:
        return pd.DataFrame()
    result1[f"Evolution_current_date_dateto_{item_type}"] = \
        result1[f"{item_type}_current_value"] / result1[f"{item_type}_value_to"] - 1
    result1[f"Evolution_current_date_datefrom_{item_type}"] = \
        result1[f"{item_type}_current_value"] / result1[f"{item_type}_value_from"] - 1
    result1[f"Evolution_dateto_datefrom_{item_type}"] = \
        result1[f"{item_type}_value_to"] / result1[f"{item_type}_value_from"] - 1
    return result1.drop(columns=[f"{item_type}_current_value", f"{item_type}_value_to",
                                 f"{item_type}_value_from"])


def get_evolution_between_two_dates_idlist_final(geoid_list, date_to, date_from,
                                                 conn_indice, conn_prix,
                                                 item_type, place_type,
                                                 schema='price',
                                                 transaction_type='TRANSACTION_TYPE.SELL',
                                                 batch_id=None):
    """Final formatted evolution with renamed columns."""
    from housing_germany.geo.postgres import get_last_price_date, init_tab

    result = get_evolution_between_two_dates(
        geoid_list, date_to, date_from, conn_indice, conn_prix, item_type,
        schema=schema, transaction_type=transaction_type, batch_id=batch_id,
    )
    if result.empty:
        return pd.DataFrame()

    actual_price = get_last_price_date(conn_prix, schema=schema,
                                       transaction_type=transaction_type, batch_id=batch_id)
    current_date = (datetime.strptime(actual_price, '%Y-%m-%d')
                    - timedelta(days=1)).replace(day=1).strftime('%Y-%m-%d')
    date_to1 = (datetime.strptime(date_to, '%Y-%m-%d') - timedelta(days=1)).replace(day=1).strftime('%Y-%m-%d')

    print(f"Item Type : {item_type}\nCurrent Date Indice : {current_date}\n"
          f"Evolution from {date_to} to {date_from}")

    result = result.rename(columns={
        f"Evolution_current_date_dateto_{item_type}":   f"Evolution {place_type} {item_type} from {actual_price} to {date_to}",
        f"Evolution_current_date_datefrom_{item_type}": f"Evolution {place_type} {item_type} from {actual_price} to {date_from}",
        f"Evolution_dateto_datefrom_{item_type}":       f"Evolution {place_type} {item_type} from {date_to} to {date_from}",
    })
    result = result.drop(columns=["current_date", "date_from", "date_to"])
    if date_to1 == current_date:
        result = result.drop(columns=[f"Evolution {place_type} {item_type} from {actual_price} to {date_to}"])
        result = result.iloc[:, :-1]

    init = init_tab(geoid_list, conn_prix)
    return init.merge(
        result.replace(np.nan, '').fillna(''),
        how="left", on=["id", "type", "title", "zip", "cog"],
    ).replace("", np.nan)


def get_children_from_parents(parent_id_list, child_geotype, conn):
    """Find all child entities of a specific type for the given parent IDs."""
    if not parent_id_list:
        return pd.DataFrame()
    parent_id_list_str = "','".join(parent_id_list)
    sql = f"""
    SELECT
        child.id as child_id,
        child.name as child_name,
        child.type as child_type
    FROM
        miracle.feature AS child,
        jsonb_array_elements_text(child.parents) AS parent_id_element
    WHERE
        parent_id_element IN ('{parent_id_list_str}')
        AND child.key = '{child_geotype}';
    """
    return pd.read_sql(sql, conn)

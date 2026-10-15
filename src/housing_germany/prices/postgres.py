"""
Postgres-backed price functions.

Combines evolution data with actual prices. Ported from indices_with_prices.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta


def get_FINAL_evolution_and_price_old(geo_id_list, entity, date_to, date_from_list,
                                      conn_indice, conn_prix, item_type_list, if_price,
                                      schema='price', transaction_type='TRANSACTION_TYPE.SELL',
                                      batch_id=None):
    """Original (non-batched) version."""
    from housing_germany.indices.postgres import get_FINAL_evolution_optimized
    from housing_germany.geo.postgres import (
        get_last_price_date, request_actual_price_classical, get_type_hierarchy_level,
    )

    df_evol = get_FINAL_evolution_optimized(
        geo_id_list, entity, date_to, date_from_list,
        conn_indice, conn_prix, item_type_list,
        schema=schema, transaction_type=transaction_type, batch_id=batch_id,
    )

    if not if_price:
        return df_evol

    ids_in_evol_df = df_evol['id'].unique().tolist()
    if not ids_in_evol_df:
        return df_evol

    df_prix = request_actual_price_classical(
        ids_in_evol_df, entity, conn_prix,
        schema=schema, transaction_type=transaction_type, batch_id=batch_id,
    )

    if df_prix.empty:
        return df_evol

    col_prix_to_keep = ["id"]
    for item in item_type_list:
        if item + "_value" in df_prix.columns:
            col_prix_to_keep.append(item + "_value")

    df_prix = df_prix[col_prix_to_keep]
    df_total = df_evol.merge(df_prix, how="left", on="id")

    current_date = get_last_price_date(
        conn_prix, schema=schema,
        transaction_type=transaction_type, batch_id=batch_id,
    )

    for item in item_type_list:
        evolution_col_pattern = f"Evolution {entity} {item} from"
        value_col = f"{item}_value"

        if value_col not in df_total.columns:
            continue

        for evol_col in df_total.columns:
            if evol_col.startswith(evolution_col_pattern):
                past_date = evol_col.split(' to ')[-1]
                new_price_col_name = f"{entity} {item} Price {past_date}"
                df_total[new_price_col_name] = df_total[value_col] / (1 + df_total[evol_col])

        if value_col in df_total.columns:
            df_total = df_total.rename(columns={value_col: f"{entity} {item} Price {current_date}"})

    df_col = df_total.columns.to_list()
    evolution_index = next((i for i, col in enumerate(df_col) if col.startswith('Evolution')), len(df_col))
    id_cols = df_col[:evolution_index]

    grouped_columns = {'id_cols': id_cols}
    for item in item_type_list:
        grouped_columns["Evolution_" + item] = [col for col in df_col if f'Evolution {entity} {item}' in col]
        grouped_columns[item + "_value"] = [col for col in df_col if f'{entity} {item} Price' in col]

    new_column_order = sum(grouped_columns.values(), [])
    df_total = df_total.reindex(columns=new_column_order)

    return df_total


def get_FINAL_evolution_and_price(geo_id_list, entity, date_to, date_from_list,
                                  conn_indice, conn_prix, item_type_list,
                                  if_price=True, schema='price',
                                  transaction_type='TRANSACTION_TYPE.SELL',
                                  batch_id=None, batch_size=5000):
    """Memory-safe wrapper for evolution + price calculation."""
    from housing_germany.indices.postgres import get_FINAL_evolution_optimized_safe
    from housing_germany.geo.postgres import (
        get_last_price_date, request_actual_price_classical, get_type_hierarchy_level,
    )

    df_evol = get_FINAL_evolution_optimized_safe(
        geo_id_list, entity, date_to, date_from_list,
        conn_indice, conn_prix, item_type_list,
        schema=schema, transaction_type=transaction_type,
        batch_id=batch_id, batch_size=batch_size,
    )

    if not if_price or df_evol.empty or get_type_hierarchy_level(entity) == 100:
        return df_evol

    ids_in_evol_df = df_evol['id'].unique().tolist()
    df_prix = request_actual_price_classical(
        ids_in_evol_df, entity, conn_prix,
        schema=schema, transaction_type=transaction_type, batch_id=batch_id,
    )

    if df_prix.empty:
        return df_evol

    col_prix_to_keep = ['id'] + [item + '_value' for item in item_type_list if item + '_value' in df_prix.columns]
    df_prix = df_prix[col_prix_to_keep]

    df_total = df_evol.merge(df_prix, how='left', on='id')
    current_date = get_last_price_date(
        conn_prix, schema=schema,
        transaction_type=transaction_type, batch_id=batch_id,
    )

    for item in item_type_list:
        value_col = f"{item}_value"
        if value_col not in df_total.columns:
            continue
        evol_cols = [c for c in df_total.columns if f"Evolution {item} from" in c]
        for evol_col in evol_cols:
            past_date = evol_col.split(' to ')[-1]
            df_total[f"{item} Price {past_date}"] = df_total[value_col] / (1 + df_total[evol_col])
        df_total = df_total.rename(columns={value_col: f"{item} Price {current_date}"})

    readable_names = {item: item.capitalize() for item in item_type_list}

    df_col = df_total.columns.to_list()
    evolution_index = next((i for i, col in enumerate(df_col) if col.startswith('Evolution')), None)
    id_type_title_cols = df_col[:evolution_index]

    grouped_columns = {}
    for item in item_type_list:
        grouped_columns[f"Evolution_{item}"] = [col for col in df_col if f"Evolution {item}" in col]
        grouped_columns[f"{item}_Price"] = [col for col in df_col if col.startswith(f"{item} Price")]

    new_column_order = id_type_title_cols + sum(grouped_columns.values(), [])
    df_total = df_total[new_column_order]

    new_column_names = {}
    for col in df_total.columns:
        for item in item_type_list:
            if col.startswith(f"Evolution {item}"):
                new_column_names[col] = col.replace(f"Evolution {item}", f"Evolution {readable_names[item]}")
            elif col.startswith(f"{item} Price"):
                new_column_names[col] = col.replace(f"{item} Price", f"{readable_names[item]} Price")

    df_total = df_total.rename(columns=new_column_names)
    return df_total

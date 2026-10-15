"""
Shared constants, type mappings, and connection helpers for housing_germany.
"""
from __future__ import annotations

import os

# Snowflake INDICE_DE item labels -> short keys
ITEM_TYPE_KEYS = {
    "ITEM_TYPE.HOUSE": "house",
    "ITEM_TYPE.APARTMENT": "apartment",
    "ITEM_TYPE.HYBRID": "hybrid",
}

# PRICE_DE uses the short, prefix-less form
PRICE_ITEM_TYPES = {"house": "HOUSE", "apartment": "APARTMENT", "hybrid": "HYBRID"}

# Transaction type constants
TXN_SELL = "TRANSACTION_TYPE.SELL"
TXN_RENT = "TRANSACTION_TYPE.RENT"

# Granularity-to-entity-type mapping
GRANULARITY_TO_ENTITY_TYPE = {
    10: 'AD02',  # country
    20: 'AD04',  # state
    35: 'AD06',  # district
    40: 'AD08',  # city
}

# Classification mappings
CLASSIFICATIONS = {
    'AD02DE': 'country',
    'AD04DE': 'state',
    'AD06DE': 'district',
    'AD08DE': 'city',
}

DICT_PREP_PRICE_EVOL = {
    'country': 'AD02DE',
    'state': 'AD04DE',
    'district': 'AD06DE',
    'top_15': 'AD08DE',
    'top_80': 'AD08DE',
    'top_cities': 'AD08DE',
}

DICT_PREP_AGGREGATE = {
    7: 'top_7',
    15: 'top_15',
    50: 'top_50',
    80: 'top_80',
    100: 'top_100',
    1: 'rural',
}


def get_snowpark_session(
    account: str | None = None,
    user: str | None = None,
    password: str | None = None,
    warehouse: str | None = None,
    role: str | None = None,
):
    """
    Create a Snowpark session from explicit args or environment variables.

    Environment variables: SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PASSWORD,
                          SNOWFLAKE_WAREHOUSE, SNOWFLAKE_ROLE
    """
    from snowflake.snowpark import Session

    return Session.builder.configs({
        "account": account or os.environ.get("SNOWFLAKE_ACCOUNT"),
        "user": user or os.environ.get("SNOWFLAKE_USER"),
        "password": password or os.environ.get("SNOWFLAKE_PASSWORD"),
        "warehouse": warehouse or os.environ.get("SNOWFLAKE_WAREHOUSE", "WH_HOUSING_DEV"),
        "role": role or os.environ.get("SNOWFLAKE_ROLE", "R_HOUSING_RW_DATA_ACCESS_DEV"),
    }).create()

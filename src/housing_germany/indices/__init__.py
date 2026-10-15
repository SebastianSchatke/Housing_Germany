"""
Index calculation module.

Submodules:
    core       - Pure math primitives (pct_evolution, rebase_to_100, etc.)
    postgres   - Postgres-backed index fetching
    snowflake  - Snowflake-backed index fetching
    aggregation - Aggregate index computation
"""
from housing_germany.indices.core import (
    pct_evolution,
    rebase_to_100,
    common_start_date,
    rebase_series,
    volume_weighted_index,
    evolution_between,
    # Backward-compatible aliases
    calcul_evol_from_series,
    calcul_rebase,
    find_first_date_rebase,
)

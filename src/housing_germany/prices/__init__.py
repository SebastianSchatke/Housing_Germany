"""
Price calculation module.

Submodules:
    core      - Pure math (implied_price_at, calculate_new_price)
    postgres  - Postgres-backed price + evolution combination
    snowflake - Snowflake-backed price loading
"""
from housing_germany.prices.core import implied_price_at, calculate_new_price

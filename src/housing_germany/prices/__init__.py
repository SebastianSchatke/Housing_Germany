"""
Price calculation module.

Submodules:
    core      - Pure math (implied_price_at, calculate_new_price)
    functions - Snowflake-backed price loading
"""
from housing_germany.prices.core import implied_price_at, calculate_new_price
from housing_germany.prices.functions import (
    load_prices,
    load_current_prices,
    has_absolute_price,
)

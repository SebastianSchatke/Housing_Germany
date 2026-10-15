"""
Purchasing power analysis module.

Submodules:
    mortgage       - Mortgage factor (PVIFA) calculations
    tax            - German Grunderwerbsteuer rates and lookup
    income         - Income data scraping from Bundesagentur fuer Arbeit
    interest_rates - Historical interest rate fetching and Snowflake loading
    analysis       - Purchasing power summary builders and change calculations
"""
from housing_germany.purchasing_power.mortgage import mortgage_formula
from housing_germany.purchasing_power.tax import get_tax_rates_DE, get_tax_by_geo
from housing_germany.purchasing_power.interest_rates import fetch_interest_rates

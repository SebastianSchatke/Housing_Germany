# Housing Germany

Predefined functions for German housing market analysis, backed by Snowflake.

## Modules

- **`indices`** -- Price index calculations, rebasing, volume-weighted aggregation, evolution between dates
- **`prices`** -- Absolute price retrieval, implied past prices, variation windows
- **`purchasing_power`** -- Mortgage formulas, tax rates, income data, interest rates, affordability analysis
- **`geo`** -- Geographic entity lookups (countries, states, districts, cities)
- **`plotting`** -- Charts and PDF generation for purchasing power trends
- **`config`** -- Shared constants, type mappings, connection helpers

## Snowflake Tables

| Table | Purpose |
|---|---|
| `SILVER_PROD.MKKN_INDICE.INDICE_DE` | Price indices (rebased to 100) with volumes |
| `SILVER_PROD.MKKN_PRICE.PRICE_DE` | Absolute sqm prices per place/item |
| `SILVER_PROD.GEOR_GEOREF.GEO_PLACE` | Geo entities: names, coordinates, polygons, hierarchy |
| `GEO_DEV.GEOR_GEOREF.GEO_PLACE_LATEST` | Housing stock counts for volume weighting |

## Install

```bash
pip install -e .
```

## Usage

```python
from housing_germany.config import get_snowpark_session

session = get_snowpark_session()

# Load indices
from housing_germany.indices.functions import load_indices, build_index_series
indices = load_indices(session, place_type="AD04")
series = build_index_series(indices, "hybrid", place_ids=["AD04DE9"])

# Load prices
from housing_germany.prices.functions import load_prices
prices = load_prices(session, place_type="AD04")

# Purchasing power (no DB required)
from housing_germany.purchasing_power.mortgage import mortgage_formula
factor = mortgage_formula(3.5, years=30)

from housing_germany.purchasing_power.tax import get_tax_rates_DE
tax_rates = get_tax_rates_DE()
```

## Configuration

Set Snowflake credentials via environment variables:

```
SNOWFLAKE_ACCOUNT=your-account
SNOWFLAKE_USER=your-user
SNOWFLAKE_PASSWORD=your-password
SNOWFLAKE_WAREHOUSE=WH_HOUSING_DEV
SNOWFLAKE_ROLE=R_HOUSING_RW_DATA_ACCESS_DEV
```

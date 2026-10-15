# Housing Germany

Predefined functions for German housing market analysis. Organized into three domain modules:

- **`indices`** -- Price index calculations, rebasing, volume-weighted aggregation, evolution between dates
- **`prices`** -- Absolute price retrieval, implied past prices, variation windows
- **`purchasing_power`** -- Mortgage formulas, tax rates, income data, interest rates, affordability analysis

Plus shared utilities:
- **`geo`** -- Geographic entity lookups (countries, states, districts, cities)
- **`plotting`** -- Charts and PDF generation for purchasing power trends
- **`config`** -- Shared constants, type mappings, connection helpers

## Install

```bash
pip install -e .
```

For Snowflake support:

```bash
pip install -e ".[snowflake]"
```

## Usage

```python
from housing_germany.indices.core import pct_evolution, rebase_to_100, evolution_between
from housing_germany.prices.core import implied_price_at
from housing_germany.purchasing_power.mortgage import mortgage_formula
from housing_germany.purchasing_power.tax import get_tax_rates_DE
```

## Configuration

Database credentials are loaded from environment variables. Create a `.env` file (not committed):

```
DB_HOST=your-host.rds.amazonaws.com
DB_NAME=indice
DB_USER=indice
DB_PASSWORD=your-password
DB_PORT=6543
```

## Backends

Each module supports two backends:
- **Postgres** (`postgres.py`) -- Original iwt_barometer pipeline via psycopg2
- **Snowflake** (`snowflake.py`) -- Snowflake-native via Snowpark sessions

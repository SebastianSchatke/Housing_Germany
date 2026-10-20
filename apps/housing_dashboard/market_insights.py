# Compatibility shim: the dashboard's maths now lives in the shared housing_germany package.
# Co-authored with CoCo
"""
Thin re-export of housing_germany so existing `import market_insights as mi`
call sites keep working. Do not add calculations here; add them to
src/housing_germany and expose them below.
"""
from housing_germany.config import ITEM_TYPE_KEYS, PRICE_ITEM_TYPES, TXN_SELL, TXN_RENT
from housing_germany.indices.core import (
    pct_evolution,
    rebase_to_100,
    common_start_date,
    rebase_series,
    volume_weighted_index,
    evolution_between,
)
from housing_germany.indices.functions import (
    load_indices,
    load_housing_weights,
    build_index_series,
    variation_windows,
)
from housing_germany.prices.core import implied_price_at
from housing_germany.prices.functions import (
    RENT_PRICE_SOURCE,
    load_prices,
    load_current_prices,
    has_absolute_price,
)
from housing_germany.geo.functions import (
    GEO_TABLE,
    load_geo,
    load_geo_polygons,
    children_of,
    parent_at_level,
    place_directory,
)
from housing_germany.plotting.streamlit_ui import insight_card

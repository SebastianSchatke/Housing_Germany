"""
Parity check for data.py: call every loader in the baseline version (git
revision 3480027) and in the current version with the same arguments and
assert the returned frames are identical.

Run: python3 test_data_parity.py
"""
import importlib.util
import os
import subprocess
import sys
import types

import pandas as pd
from snowflake.snowpark import Session

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BASELINE_REV = "3480027"

# Create the session before stubbing streamlit (Snowpark inspects the real module).
session = Session.builder.config(
    "connection_name", os.environ.get("SNOWFLAKE_CONNECTION_NAME", "aviv-prod")
).create()
session.sql("USE ROLE SNF_R_DATA_ANALYST").collect()
session.sql("USE WAREHOUSE WH_PROD_PERSONA").collect()

# st.cache_data is a no-op here so both versions really hit Snowflake.
st = types.ModuleType("streamlit")
st.cache_data = lambda *a, **k: (lambda f: f)
sys.modules["streamlit"] = st


def load_module(name, source):
    spec = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    exec(compile(source, f"<{name}>", "exec"), mod.__dict__)
    return mod


baseline_src = subprocess.check_output(
    ["git", "show", f"{BASELINE_REV}:apps/housing_dashboard/data.py"], cwd=HERE, text=True
)
old = load_module("data_baseline", baseline_src)
new = load_module("data_current", open(os.path.join(HERE, "data.py")).read())

SELL = "TRANSACTION_TYPE.SELL"
BERLIN, MUNICH_DISTRICT = "AD08DE8634", "AD06DE285"
CASES = [
    ("get_index_series", (SELL, "AD04")),
    ("get_price_history", (SELL, "AD04DE9")),
    ("get_market_summary", (SELL, "AD06")),
    ("get_geo", ("AD04",)),
    ("get_directory", ()),
    ("get_parent", ("AD06DE285",)),
    ("get_nearby_places", (52.52, 13.40, "AD08", 10)),
    ("get_polygons", ("AD06", "PARENT_AD04", "AD04DE9")),
    ("get_nearby_polygons", (48.14, 11.58, "AD06", 5)),
    ("get_sibling_count", (MUNICH_DISTRICT,)),
    ("get_income", ("AD04DE9",)),
    ("get_income_national", ()),
    ("get_population", ("AD04DE9",)),
    ("get_interest_rates", ()),
    ("get_purchasing_power", ("AD04DE9",)),
    ("get_purchasing_power_history", ("AD04DE9",)),
    ("get_demand", ("Bayern", "AD04")),
    ("get_listings_insights", ("Bayern", "AD04", SELL)),
]

failures = 0
for name, args in CASES:
    a = getattr(old, name)(session, *args)
    b = getattr(new, name)(session, *args)
    if isinstance(a, pd.DataFrame):
        try:
            # Several queries have no ORDER BY, so compare as sorted row sets.
            cols = list(a.columns)
            pd.testing.assert_frame_equal(
                a.sort_values(cols).reset_index(drop=True),
                b.sort_values(cols).reset_index(drop=True),
            )
            status = f"OK   rows={len(a)}"
        except AssertionError as e:
            status, failures = f"DIFF {str(e).splitlines()[0]}", failures + 1
    else:
        status = "OK" if a == b else f"DIFF {a!r} != {b!r}"
        failures += status != "OK"
    print(f"{name:32} {status}")

# get_demographics intentionally changed source table (DE_GEO_PLACE -> GEO_PLACE):
# the baseline returns {} when DE_GEO_PLACE is not authorized, so only the new one is shown.
print(f"{'get_demographics (new source)':32} {new.get_demographics(session, MUNICH_DISTRICT)}")

session.close()
print(f"\n{'ALL MATCH' if not failures else f'{failures} DIFFERENCES'}")
sys.exit(1 if failures else 0)

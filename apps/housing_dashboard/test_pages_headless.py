"""Headless test: execute each app page's real code against a live Snowpark
session, with a stubbed Streamlit, to surface column-reference / data-shape
errors without the UI. Run: python3 test_pages_headless.py
"""
import os
import sys
import types
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# --- Build a live Snowpark session from the container session token ---
from snowflake.snowpark import Session

token = open(os.environ.get("SNOWFLAKE_TOKEN_FILE_PATH", "/snowflake/session/token")).read().strip()
session = Session.builder.configs({
    "account": os.environ["SNOWFLAKE_ACCOUNT"],
    "host": os.environ["SNOWFLAKE_HOST"],
    "authenticator": "oauth",
    "token": token,
}).create()
session.sql("USE ROLE SNF_R_DATA_ANALYST").collect()
session.sql("USE WAREHOUSE WH_PROD_PERSONA").collect()


# --- Minimal Streamlit stub that records calls and returns sane defaults ---
class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _SessionState(dict):
    def __getattr__(self, k):
        try: return self[k]
        except KeyError: raise AttributeError(k)
    def __setattr__(self, k, v): self[k] = v


class _Col(_Ctx):
    def metric(self, *a, **k): pass
    def markdown(self, *a, **k): pass
    def __getattr__(self, name): return lambda *a, **k: _Ctx()


class StopExc(Exception):
    pass


def _make_st():
    st = types.ModuleType("streamlit")
    st.session_state = _SessionState()

    def columns(spec, **k):
        n = spec if isinstance(spec, int) else len(spec)
        return [_Col() for _ in range(n)]

    st.columns = columns
    st.container = lambda *a, **k: _Col()
    st.tabs = lambda labels, **k: [_Col() for _ in labels]
    st.sidebar = _Col()
    st.expander = lambda *a, **k: _Col()
    st.stop = lambda: (_ for _ in ()).throw(StopExc())
    # widgets return a deterministic default (first option / provided default)
    st.selectbox = lambda label, options, index=0, **k: list(options)[index] if len(options) else None
    st.radio = lambda label, options, index=0, **k: list(options)[index]
    st.multiselect = lambda label, options, default=None, **k: (default if default is not None else list(options))
    st.select_slider = lambda label, options, value=None, **k: (value if value is not None else list(options)[0])
    st.slider = lambda *a, **k: k.get("value", 0)
    st.toggle = lambda *a, **k: k.get("value", False)
    st.button = lambda *a, **k: False
    st.date_input = lambda *a, **k: k.get("value")
    # no-op renderers
    for fn in ("title", "caption", "subheader", "header", "markdown", "write", "info",
               "warning", "error", "metric", "dataframe", "table", "line_chart",
               "bar_chart", "area_chart", "map", "pydeck_chart", "plotly_chart",
               "divider", "set_page_config", "image", "altair_chart", "json", "rerun"):
        setattr(st, fn, lambda *a, **k: None)
    # cache decorators: pass-through
    def cache_data(*d_a, **d_k):
        if d_a and callable(d_a[0]):
            return d_a[0]
        def wrap(fn):
            fn.clear = lambda: None
            return fn
        return wrap
    st.cache_data = cache_data
    st.cache_resource = cache_data

    class Page:
        def __init__(self, path, **k): self.path = path; self.title = k.get("title", ""); self.icon = k.get("icon", "")
        def run(self): pass
    st.Page = Page
    st.navigation = lambda pages, **k: (pages[0] if pages else Page(""))
    return st


st = _make_st()
sys.modules["streamlit"] = st

# Seed shared session_state as streamlit_app.py would (bypassing st.connection).
st.session_state.session = session
st.session_state.conn = None


def run_page(path, txn, item):
    import market_insights as mi
    st.session_state.transaction_type = txn
    st.session_state.item = item
    st.session_state.has_price = mi.has_absolute_price(session, txn)
    src = open(path).read()
    g = {"__name__": "__main__", "__file__": path}
    try:
        exec(compile(src, path, "exec"), g)
        return "OK"
    except StopExc:
        return "OK (st.stop)"
    except Exception:
        return "FAIL\n" + traceback.format_exc()


PAGES = ["app_pages/national.py", "app_pages/states.py", "app_pages/districts_map.py",
         "app_pages/top_cities.py", "app_pages/place_search.py"]

TXN = {"SELL": "TRANSACTION_TYPE.SELL", "RENT": "TRANSACTION_TYPE.RENT"}

failures = 0
for page in PAGES:
    for txn_name, txn in TXN.items():
        for item in ("hybrid", "house", "apartment"):
            res = run_page(page, txn, item)
            tag = res.splitlines()[0]
            print(f"[{tag:14}] {page:28} txn={txn_name:4} item={item}")
            if res.startswith("FAIL"):
                failures += 1
                print(res)
            # only test all items for the default; other combos test hybrid only
            if item == "hybrid" and txn_name == "SELL":
                continue
    print()

print(f"\n{'ALL PAGES OK' if failures == 0 else f'{failures} FAILURES'}")
session.close()
sys.exit(1 if failures else 0)

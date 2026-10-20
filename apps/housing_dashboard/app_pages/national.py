import pandas as pd
import streamlit as st

import market_insights as mi
import data

session = st.session_state.session
txn = st.session_state.transaction_type
item = st.session_state.item

idx = data.get_indices(session, txn, "AD02")
if idx.empty:
    st.info("No index data available for this market.")
    st.stop()

germany_ids = idx["PLACE_ID"].unique().tolist()

# Build the three index series (validated volume-weighted build; hybrid fallback).
series = {i: mi.build_index_series(idx, i, germany_ids) for i in ("house", "apartment", "hybrid")}
sel = series[item]

# --- Index chart ---
chart_df = pd.DataFrame({k.capitalize(): v for k, v in series.items() if not v.empty})
with st.container(border=True):
    st.markdown("**Price Index — Germany (rebased to 100)**")
    if not chart_df.empty:
        st.line_chart(chart_df)

# --- Insight cards ---
vw = {i: mi.variation_windows(series[i]) for i in series}
c1, c2 = st.columns(2)
with c1:
    mi.insight_card("Germany price change (1 year)",
                    f"{vw[item]['1y'] * 100:+.1f}%" if pd.notna(vw[item]["1y"]) else "n/a",
                    [("Houses", vw["house"]["1y"]), ("Apartments", vw["apartment"]["1y"])])
with c2:
    mi.insight_card("2-year comparison",
                    f"{vw[item]['2y'] * 100:+.1f}%" if pd.notna(vw[item]["2y"]) else "n/a",
                    [("Houses", vw["house"]["2y"]), ("Apartments", vw["apartment"]["2y"])])

# --- Variation table across all windows ---
st.subheader("Price Variation by Window")
windows = ["1m", "3m", "6m", "YTD", "1y", "2y", "3y", "5y", "all"]
rows = []
for i, label in (("house", "House"), ("apartment", "Apartment"), ("hybrid", "Hybrid")):
    r = {"Property": label}
    for w in windows:
        v = vw[i][w]
        r[w] = None if pd.isna(v) else round(v * 100, 1)
    rows.append(r)
var_df = pd.DataFrame(rows).set_index("Property")
st.dataframe(
    var_df.style.format("{:+.1f}%", na_rep="—")
    .map(lambda v: "color:#1db954" if (pd.notna(v) and v >= 0)
         else ("color:#e74c3c" if pd.notna(v) else "")),
    use_container_width=True,
)
st.caption("Values are cumulative % change of the rebased price index over each window.")

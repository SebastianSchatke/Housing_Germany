import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

import market_insights as mi
import data

session = st.session_state.session
txn = st.session_state.transaction_type
item = st.session_state.item
has_price = st.session_state.has_price
price_item = mi.PRICE_ITEM_TYPES[item]

idx = data.get_indices(session, txn, "AD08")
geo = data.get_geo(session, "AD08")
if idx.empty or geo.empty:
    st.info("No city data available.")
    st.stop()

# Rank cities by population, restricted to those with index data.
indexed_ids = set(idx["PLACE_ID"].unique())
geo = geo[geo["place_id"].isin(indexed_ids) & geo["population"].fillna(0).gt(0)]
geo = geo.sort_values("population", ascending=False).reset_index(drop=True)
top80 = geo.head(80)
idx_top = idx[idx["PLACE_ID"].isin(top80["place_id"])]

window = st.radio("Variation window", ["1m", "3m", "6m", "YTD", "1y", "2y", "3y", "5y", "all"],
                  index=4, horizontal=True)

# --- Cohort aggregate insight cards (population cohorts) ---
st.subheader("Top-city cohorts")
cols = st.columns(3)
for col, n in zip(cols, (15, 50, 80)):
    ids = top80.head(n)["place_id"].tolist()
    agg = {i: mi.variation_windows(mi.build_index_series(idx_top, i, ids)) for i in ("house", "apartment", item)}
    with col:
        v = agg[item][window]
        mi.insight_card(f"Top {n} cities · {window}",
                        f"{v * 100:+.1f}%" if pd.notna(v) else "n/a",
                        [("Houses", agg["house"][window]), ("Apartments", agg["apartment"][window])])

# --- Top 15 map (price + variation badge) ---
st.subheader("Top 15 cities")
top15 = top80.head(15).copy()
prices = data.get_prices(session, txn, "AD08") if has_price else pd.DataFrame()
recs = []
for _, c in top15.iterrows():
    pid = c["place_id"]
    s = mi.build_index_series(idx_top, item, [pid])
    vw = mi.variation_windows(s)
    var = vw[window]
    price = np.nan
    if has_price and not prices.empty:
        pp = prices[(prices["PLACE_ID"] == pid) & (prices["ITEM_TYPE"] == price_item)]
        if not pp.empty:
            price = pp.sort_values("PRICE_DATE")["VALUE"].iloc[-1]
    label = c["name"]
    if pd.notna(price):
        label += f"\n€{int(price):,}/m²"
    if pd.notna(var):
        label += f"  {var * 100:+.1f}%"
    recs.append({
        "name": c["name"], "lat": c["lat"], "lon": c["lon"],
        "label": label, "var": None if pd.isna(var) else round(var * 100, 1),
        "price": None if pd.isna(price) else int(price),
        "color": [29, 185, 84] if (pd.notna(var) and var >= 0) else [231, 76, 60],
    })

map_df = pd.DataFrame(recs).dropna(subset=["lat", "lon"])
if not map_df.empty:
    scatter = pdk.Layer("ScatterplotLayer", map_df, get_position="[lon, lat]",
                        get_fill_color="color", get_radius=14000, pickable=True, opacity=0.8)
    text = pdk.Layer("TextLayer", map_df, get_position="[lon, lat]", get_text="name",
                     get_size=13, get_color=[30, 30, 46], get_alignment_baseline="'bottom'")
    st.pydeck_chart(pdk.Deck(
        layers=[scatter, text], map_style=None,
        initial_view_state=pdk.ViewState(latitude=51.1, longitude=10.4, zoom=4.7),
        tooltip={"text": "{label}"},
    ), use_container_width=True)

# --- Detail table ---
tbl = map_df[["name", "price", "var"]].rename(
    columns={"name": "City", "price": "€/m²", "var": f"{window} change %"})
fmt = {f"{window} change %": "{:+.1f}%"}
if has_price:
    fmt["€/m²"] = "€{:,.0f}"
st.dataframe(tbl.set_index("City").style.format(fmt, na_rep="—"), use_container_width=True)
if not has_price:
    st.caption("Rent shows variation only — absolute €/m² not yet available.")

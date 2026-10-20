import json

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

idx = data.get_indices(session, txn, "AD06")
polys = data.get_polygons(session, "AD06")
if idx.empty or polys.empty:
    st.info("No district data available.")
    st.stop()

windows = ["1m", "3m", "6m", "YTD", "1y", "2y", "3y", "5y", "all"]
metric_options = (["Price €/m²"] if has_price else []) + [f"Change {w}" for w in windows]
metric = st.radio("Color districts by", metric_options, horizontal=True,
                  index=len(metric_options) - 5)  # default ~1y

# Per-district metric value
prices = data.get_prices(session, txn, "AD06") if has_price else pd.DataFrame()
values = {}
for pid in idx["PLACE_ID"].unique():
    if metric == "Price €/m²":
        pp = prices[(prices["PLACE_ID"] == pid) & (prices["ITEM_TYPE"] == price_item)] if not prices.empty else pd.DataFrame()
        values[pid] = pp.sort_values("PRICE_DATE")["VALUE"].iloc[-1] if not pp.empty else np.nan
    else:
        w = metric.replace("Change ", "")
        s = mi.build_index_series(idx, item, [pid])
        vw = mi.variation_windows(s)
        values[pid] = vw[w] * 100 if pd.notna(vw[w]) else np.nan

vals = pd.Series(values).dropna()
if vals.empty:
    st.info("No values for the selected metric.")
    st.stop()

is_price = metric == "Price €/m²"
lo, hi = vals.quantile(0.05), vals.quantile(0.95)


def color_for(v):
    if pd.isna(v):
        return [200, 200, 200, 60]
    if is_price:  # sequential blue→red (low→high price)
        t = np.clip((v - lo) / (hi - lo + 1e-9), 0, 1)
        return [int(60 + 195 * t), int(90 * (1 - t)), int(200 * (1 - t)), 170]
    # diverging red(neg)→green(pos) centered at 0
    m = max(abs(lo), abs(hi), 1e-9)
    t = np.clip(v / m, -1, 1)
    if t >= 0:
        return [int(230 * (1 - t)), 180, int(80 * (1 - t)), 170]
    return [220, int(180 * (1 + t)), int(80 * (1 + t)), 170]


features = []
name_by_id = dict(zip(polys["place_id"], polys["name"]))
for _, row in polys.iterrows():
    pid = row["place_id"]
    if pid not in values or not row["geojson"]:
        continue
    try:
        geom = json.loads(row["geojson"])
    except (ValueError, TypeError):
        continue
    v = values[pid]
    features.append({
        "type": "Feature", "geometry": geom,
        "properties": {
            "name": row["name"],
            "value": None if pd.isna(v) else round(float(v), 1),
            "fill": color_for(v),
        },
    })

layer = pdk.Layer(
    "GeoJsonLayer", {"type": "FeatureCollection", "features": features},
    stroked=True, filled=True, get_fill_color="properties.fill",
    get_line_color=[255, 255, 255, 120], line_width_min_pixels=0.5, pickable=True,
)
unit = "€/m²" if is_price else "%"
st.pydeck_chart(pdk.Deck(
    layers=[layer], map_style=None,
    initial_view_state=pdk.ViewState(latitude=51.1, longitude=10.4, zoom=4.7),
    tooltip={"text": "{name}\n{value} " + unit},
), use_container_width=True)

# --- Insight cards ---
c1, c2 = st.columns(2)
if has_price and is_price:
    with c1:
        top_id = vals.idxmax()
        mi.insight_card("Most expensive district", name_by_id.get(top_id, top_id))
        st.metric("", f"€{int(vals.max()):,}/m²")
if not is_price:
    with c1:
        mi.insight_card("Highest-growing district", name_by_id.get(vals.idxmax(), ""),
                        [(metric.replace("Change ", "") + " change", vals.max() / 100)])
    with c2:
        mi.insight_card("Biggest faller", name_by_id.get(vals.idxmin(), ""),
                        [(metric.replace("Change ", "") + " change", vals.min() / 100)])

st.caption(f"{len(features)} districts shown. Map renders offline (no basemap); shape is formed by district polygons.")

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

TYPE_LABEL = {"AD04": "State", "AD06": "District", "AD08": "City"}

directory = data.get_directory(session)
directory = directory[directory["type"].isin(TYPE_LABEL)].dropna(subset=["name"])
directory = directory.sort_values("name")
directory["label"] = directory["name"] + " · " + directory["type"].map(TYPE_LABEL)

options = directory["place_id"].tolist()
labels = dict(zip(directory["place_id"], directory["label"]))

# Default to Berlin if present.
default_ix = options.index("AD08DE8634") if "AD08DE8634" in options else 0
pid = st.selectbox("Search a place (type to filter by name)", options, index=default_ix,
                   format_func=lambda x: labels.get(x, x))

ptype = directory.set_index("place_id")["type"].get(pid)
pname = directory.set_index("place_id")["name"].get(pid, pid)
st.subheader(f"{pname} · {TYPE_LABEL.get(ptype, ptype)}")

# --- Selected place metrics ---
idx = data.get_indices(session, txn, ptype)
series = mi.build_index_series(idx, item, [pid])
vw = mi.variation_windows(series)

price = np.nan
if has_price:
    prices = data.get_prices(session, txn, ptype)
    pp = prices[(prices["PLACE_ID"] == pid) & (prices["ITEM_TYPE"] == price_item)] if not prices.empty else pd.DataFrame()
    if not pp.empty:
        price = pp.sort_values("PRICE_DATE")["VALUE"].iloc[-1]

k = st.columns(4)
k[0].metric("Price €/m²", f"€{int(price):,}" if pd.notna(price) else "n/a", border=True)
k[1].metric("1y", f"{vw['1y'] * 100:+.1f}%" if pd.notna(vw["1y"]) else "n/a", border=True)
k[2].metric("3y", f"{vw['3y'] * 100:+.1f}%" if pd.notna(vw["3y"]) else "n/a", border=True)
k[3].metric("All-time", f"{vw['all'] * 100:+.1f}%" if pd.notna(vw["all"]) else "n/a", border=True)

if not series.empty:
    with st.container(border=True):
        st.markdown(f"**Price index — {pname} ({item.capitalize()})**")
        st.line_chart(series.rename("Index"))

windows = ["1m", "3m", "6m", "YTD", "1y", "2y", "3y", "5y", "all"]
vrow = pd.DataFrame([{w: (None if pd.isna(vw[w]) else round(vw[w] * 100, 1)) for w in windows}], index=[item.capitalize()])
st.dataframe(vrow.style.format("{:+.1f}%", na_rep="—"), use_container_width=True)

# --- Context: sibling cities in the same district (only for cities) ---
if ptype == "AD08":
    parent = data.get_parent(session, pid)
    ad06 = parent.get("ad06") if parent else None
    if ad06:
        siblings = data.get_children(session, ad06, "AD06", "AD08").dropna(subset=["lat", "lon"])
        if not siblings.empty:
            st.subheader("Cities in the same district")
            siblings["color"] = siblings["place_id"].apply(
                lambda x: [255, 199, 44] if x == pid else [90, 120, 200])
            layer = pdk.Layer("ScatterplotLayer", siblings, get_position="[lon, lat]",
                              get_fill_color="color", get_radius=6000, pickable=True)
            text = pdk.Layer("TextLayer", siblings, get_position="[lon, lat]", get_text="name",
                             get_size=12, get_color=[30, 30, 46], get_alignment_baseline="'bottom'")
            view = pdk.ViewState(latitude=float(siblings["lat"].mean()),
                                 longitude=float(siblings["lon"].mean()), zoom=8)
            st.pydeck_chart(pdk.Deck(layers=[layer, text], map_style=None,
                            initial_view_state=view, tooltip={"text": "{name}"}),
                            use_container_width=True)

    # --- Neighbourhoods (NBH2): current SELL price snapshot only ---
    nbh = data.get_children(session, pid, "AD08", "NBH2")
    if not nbh.empty:
        nbh_prices = data.get_prices(session, mi.TXN_SELL, "NBH2", min_accuracy=2)
        if not nbh_prices.empty:
            npp = nbh_prices[nbh_prices["ITEM_TYPE"] == mi.PRICE_ITEM_TYPES[item]]
            npp = npp.sort_values("PRICE_DATE").groupby("PLACE_ID").tail(1)
            merged = nbh.merge(npp[["PLACE_ID", "VALUE"]], left_on="place_id", right_on="PLACE_ID")
            if not merged.empty:
                st.subheader("Neighbourhoods (current sell €/m²)")
                merged = merged.sort_values("VALUE", ascending=False)
                cc = st.columns(2)
                with cc[0]:
                    mi.insight_card("Most expensive neighbourhood", merged.iloc[0]["name"])
                    st.metric("", f"€{int(merged.iloc[0]['VALUE']):,}/m²")
                with cc[1]:
                    mi.insight_card("Cheapest neighbourhood", merged.iloc[-1]["name"])
                    st.metric("", f"€{int(merged.iloc[-1]['VALUE']):,}/m²")
                st.dataframe(
                    merged[["name", "VALUE"]].rename(columns={"name": "Neighbourhood", "VALUE": "€/m²"})
                    .set_index("Neighbourhood").style.format({"€/m²": "€{:,.0f}"}),
                    use_container_width=True, height=280)
                st.caption(f"{len(merged)} of {len(nbh)} neighbourhoods priced · current snapshot, sell only (no history/rent at this level).")

import pandas as pd
import streamlit as st

import market_insights as mi
import data

session = st.session_state.session
txn = st.session_state.transaction_type
item = st.session_state.item
has_price = st.session_state.has_price

idx = data.get_indices(session, txn, "AD04")
geo = data.get_geo(session, "AD04").set_index("place_id")
if idx.empty:
    st.info("No index data available.")
    st.stop()

price_item = mi.PRICE_ITEM_TYPES[item]
prices = data.get_prices(session, txn, "AD04") if has_price else pd.DataFrame()

windows = ["1m", "3m", "6m", "YTD", "1y", "2y", "3y", "5y", "all"]
rows = []
for pid in idx["PLACE_ID"].unique():
    series = mi.build_index_series(idx, item, [pid])
    if series.empty:
        continue
    vw = mi.variation_windows(series)
    name = geo["name"].get(pid, pid)
    row = {"Bundesland": name}
    if has_price and not prices.empty:
        pp = prices[(prices["PLACE_ID"] == pid) & (prices["ITEM_TYPE"] == price_item)]
        row["€/m²"] = round(pp.sort_values("PRICE_DATE")["VALUE"].iloc[-1]) if not pp.empty else None
    for w in windows:
        row[w] = None if pd.isna(vw[w]) else round(vw[w] * 100, 1)
    row["_1y"] = vw["1y"]
    rows.append(row)

df = pd.DataFrame(rows)
if df.empty:
    st.info("No data for the selected filters.")
    st.stop()

# --- Insight cards ---
c1, c2, c3 = st.columns(3)
if "€/m²" in df.columns and df["€/m²"].notna().any():
    top = df.loc[df["€/m²"].idxmax()]
    with c1:
        mi.insight_card("Most expensive state", f"{top['Bundesland']}",
                        [("Price €/m²", None)])
        st.metric("", f"€{int(top['€/m²']):,}/m²")
grow = df.dropna(subset=["_1y"])
if not grow.empty:
    fast = grow.loc[grow["_1y"].idxmax()]
    slow = grow.loc[grow["_1y"].idxmin()]
    with c2:
        mi.insight_card("Fastest growing (1y)", fast["Bundesland"], [("1y change", fast["_1y"])])
    with c3:
        mi.insight_card("Biggest faller (1y)", slow["Bundesland"], [("1y change", slow["_1y"])])

# --- Table ---
st.subheader("All Bundesländer")
show = df.drop(columns=["_1y"]).set_index("Bundesland").sort_values(
    "€/m²" if "€/m²" in df.columns else "1y", ascending=False, na_position="last")
fmt = {w: "{:+.1f}%" for w in windows}
if "€/m²" in show.columns:
    fmt["€/m²"] = "€{:,.0f}"
st.dataframe(
    show.style.format(fmt, na_rep="—")
    .map(lambda v: "color:#1db954" if (pd.notna(v) and v >= 0)
         else ("color:#e74c3c" if pd.notna(v) else ""), subset=windows),
    use_container_width=True,
)
if not has_price:
    st.caption("Rent shows variation only — absolute €/m² not yet available.")

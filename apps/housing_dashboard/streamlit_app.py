# Immowelt-style German Housing Market dashboard with polished UI/UX.
# Co-authored with CoCo
import json
import math
import os

import altair as alt
import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st

st.set_page_config(page_title="Immowelt Market Insights", page_icon=":house:", layout="wide")

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
if "conn" not in st.session_state:
    st.session_state.conn = st.connection("snowflake", ttl=os.getenv("SNOWFLAKE_CONNECTION_TTL"))
    st.session_state.session = st.session_state.conn.session()
    for stmt in ("USE ROLE SNF_R_DATA_ANALYST", "USE SECONDARY ROLES ALL"):
        try:
            st.session_state.session.sql(stmt).collect()
        except Exception:
            pass

session = st.session_state.session

import importlib
import data
importlib.reload(data)
import report as report_mod
importlib.reload(report_mod)

# Report builder (populated as the dashboard renders; used by the HTML export button)
report = None

# ---------------------------------------------------------------------------
# German number formatting helper
# ---------------------------------------------------------------------------
def fmt_eur(v):
    """Format price as €X.XXX/m² (German thousands separator)."""
    if pd.isna(v):
        return "n/a"
    return f"€{int(v):,.0f}/m²".replace(",", ".")


def fmt_salary(v):
    """Format salary as €X.XXX (German thousands separator)."""
    if pd.isna(v) or v is None:
        return "n/a"
    return f"€{int(v):,.0f}".replace(",", ".")


# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    .block-container { padding-top: 0.5rem; }
    /* Hide default Streamlit header for cleaner look */
    header[data-testid="stHeader"] { height: 2rem; }
    /* KPI cards */
    .kpi-card {
        background: #fff; border-radius: 10px; padding: 16px;
        border-left: 4px solid #ffcc00; margin-bottom: 6px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    .kpi-card .kpi-value { font-size: 1.5rem; font-weight: 800; color: #333; }
    .kpi-card .kpi-label { font-size: 0.82rem; color: #666; margin-top: 3px; }
    .kpi-card .kpi-sub { font-size: 0.75rem; color: #999; }
    /* Placeholders */
    .placeholder-card {
        background: #f9f9f9; border-radius: 10px; padding: 20px;
        border: 1px dashed #ddd; text-align: center;
    }
    .placeholder-card .title { font-size: 0.9rem; font-weight: 600; color: #999; }
    .placeholder-card .sub { font-size: 0.75rem; margin-top: 4px; color: #bbb; }
    /* Variation colors */
    .var-positive { color: #1db954; font-weight: 600; }
    .var-negative { color: #e74c3c; font-weight: 600; }
    /* Map container - fixed height */
    .map-container { height: 500px; border-radius: 10px; overflow: hidden; border: 1px solid #eee; }
    /* Price list scrollable */
    .price-list { max-height: 500px; overflow-y: auto; padding-right: 8px; }
    /* Prevent scroll-trapping on chart and map containers */
    [data-testid="stVegaLiteChart"] { overflow: hidden; }
    iframe[title*="streamlit_pydeck"] { pointer-events: auto; }
    [data-testid="stDeckGlJsonChart"] { overflow: hidden; }
    /* Make radio buttons look like large pill toggles */
    div[data-testid="stRadio"] > div { gap: 0 !important; }
    div[data-testid="stRadio"] label {
        background: #f0f0f0; padding: 10px 24px !important; border-radius: 8px;
        font-size: 1rem !important; font-weight: 600 !important; margin-right: 4px;
        border: 2px solid transparent; cursor: pointer;
    }
    div[data-testid="stRadio"] label[data-checked="true"],
    div[data-testid="stRadio"] label:has(input:checked) {
        background: #ffcc00 !important; border-color: #e6b800;
        color: #333 !important;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar: Controls (always visible regardless of scroll)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("#### Immowelt Market Insights")
    export_slot = st.empty()
    st.markdown("---")
    
    directory = data.get_directory(session)
    if directory.empty:
        st.warning("Geo directory not available.")
        st.stop()

    def make_label(row):
        if row["type"] == "AD04":
            return f"{row['name']} (Bundesland)"
        context = row.get("district_name") or row.get("state_name") or ""
        if context:
            return f"{row['name']}, {context}"
        return row["name"]

    directory["label"] = directory.apply(make_label, axis=1)
    directory = directory.drop_duplicates(subset=["label"]).sort_values("label")

    TXN_SELL = "TRANSACTION_TYPE.SELL"
    TXN_RENT = "TRANSACTION_TYPE.RENT"
    txn_label = st.radio("Market", ["Kaufen (Buy)", "Mieten (Rent)"], horizontal=True, key="txn_toggle")
    txn = TXN_SELL if "Kaufen" in txn_label else TXN_RENT

    selected_label = st.selectbox(
        "Stadt oder Bundesland suchen",
        options=["Germany (National)"] + directory["label"].tolist(),
        index=0,
        placeholder="Stadt oder Bundesland suchen...",
    )
    
    st.markdown("---")
    growth_options = {"1 Month": "VAR_1M", "3 Months": "VAR_3M", "6 Months": "VAR_6M",
                     "YTD": "VAR_YTD", "1 Year": "VAR_1Y", "2 Years": "VAR_2Y",
                     "3 Years": "VAR_3Y", "5 Years": "VAR_5Y", "All Time": "VAR_ALL"}
    growth_label = st.selectbox("Growth period", list(growth_options.keys()), index=4, key="growth_period")
    growth_col = growth_options[growth_label]

    st.markdown("---")
    st.markdown("**Purchasing Power**")
    household_type = st.radio("Household", ["Single", "Family"], horizontal=True, key="household")
    include_realtor = st.checkbox("Include realtor fee (3.56%)", value=True, key="realtor")

    st.markdown("---")
    if st.button("Clear cache", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

if selected_label == "Germany (National)":
    place_id = "AD02DE1"
    place_type = "AD02"
    place_name = "Germany"
else:
    match = directory[directory["label"] == selected_label].iloc[0]
    place_id = match["place_id"]
    place_type = match["type"]
    place_name = match["name"]

st.markdown(f"## {place_name}")
st.caption(f"{'Sell' if 'Kaufen' in txn_label else 'Rent'} market")

# Initialise the HTML export report for this selection
report = report_mod.ReportBuilder(place_name, txn_label)
report.set_meta(growth=growth_label, household=household_type, realtor=include_realtor)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
with st.spinner("Loading market data..."):
    summary = data.get_market_summary(session, txn, place_type)
    place_summary = summary[summary["PLACE_ID"] == place_id]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
item_map = {"ITEM_TYPE.APARTMENT": "Apartment", "ITEM_TYPE.HOUSE": "House", "ITEM_TYPE.HYBRID": "Hybrid"}


def kpi_card(label, value_str, sub_text="", var_pct=None, invert_color=False):
    var_html = ""
    if var_pct is not None and pd.notna(var_pct):
        # Arrow follows actual direction; color can be inverted (e.g. rates down = green)
        arrow = "\u2191" if var_pct >= 0 else "\u2193"
        if invert_color:
            cls = "var-positive" if var_pct < 0 else "var-negative"
        else:
            cls = "var-positive" if var_pct >= 0 else "var-negative"
        var_html = f'<span class="{cls}"> {arrow} {var_pct*100:+.1f}%</span>'
    html = f"""
    <div class="kpi-card">
        <div class="kpi-value">{value_str} {var_html}</div>
        <div class="kpi-label">{label}</div>
        <div class="kpi-sub">{sub_text}</div>
    </div>
    """
    if report is not None:
        report.card(html)
    return html


def placeholder_card(title):
    return f"""
    <div class="placeholder-card">
        <div class="title">{title}</div>
        <div class="sub">Coming soon</div>
    </div>
    """


def auto_zoom(lats, lons):
    """Calculate zoom level from bounding box of coordinates."""
    if len(lats) == 0:
        return 5
    lat_range = max(lats) - min(lats)
    lon_range = max(lons) - min(lons)
    max_range = max(lat_range, lon_range, 0.01)
    zoom = math.log2(360 / max_range) - 1
    return max(3, min(zoom, 12))


# ---------------------------------------------------------------------------
# KPI Cards + Date context
# ---------------------------------------------------------------------------
latest_date = None
report.heading(f"Prices (\u20ac/m\u00b2) \u2014 {growth_label} change")
c1, c2, c3 = st.columns(3)
for col, item_key in zip([c1, c2, c3], ["ITEM_TYPE.APARTMENT", "ITEM_TYPE.HOUSE", "ITEM_TYPE.HYBRID"]):
    row = place_summary[place_summary["ITEM_TYPE"] == item_key]
    with col:
        if not row.empty:
            r = row.iloc[0]
            price = r.get("PRICE_SQM")
            var_sel = r.get(growth_col)
            val_str = fmt_eur(price) if pd.notna(price) else "Index only"
            latest_date = r.get("LATEST_DATE") or latest_date
            st.markdown(kpi_card(item_map[item_key], val_str, growth_label, var_sel), unsafe_allow_html=True)
        else:
            st.markdown(kpi_card(item_map[item_key], "n/a"), unsafe_allow_html=True)

if latest_date:
    st.caption(f"Data as of {latest_date} · {txn_label} market")
    report.set_meta(as_of=latest_date)

# ---------------------------------------------------------------------------
# Price History Chart (€/m²)
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Price History (€/m²)")

with st.spinner("Loading price history..."):
    price_hist = data.get_price_history(session, txn, place_id)

if not price_hist.empty:
    pivot = price_hist.pivot_table(index="INDEX_DATE", columns="ITEM_TYPE", values="PRICE_SQM")
    pivot.columns = [item_map.get(c, c) for c in pivot.columns]
    pivot = pivot.sort_index().reset_index()
    pivot["INDEX_DATE"] = pd.to_datetime(pivot["INDEX_DATE"])

    # Round values based on transaction type
    dec = 0 if "Kaufen" in txn_label else 2
    value_cols = [c for c in pivot.columns if c != "INDEX_DATE"]
    for vc in value_cols:
        pivot[vc] = pivot[vc].round(dec)

    melted = pivot.melt(id_vars="INDEX_DATE", value_vars=value_cols, var_name="Type", value_name="€/m²")
    fmt_str = ",.0f" if dec == 0 else ",.2f"

    chart = alt.Chart(melted).mark_line(strokeWidth=2.5).encode(
        x=alt.X("INDEX_DATE:T", title=""),
        y=alt.Y("€/m²:Q", title="€/m²", axis=alt.Axis(format=fmt_str)),
        color=alt.Color("Type:N", scale=alt.Scale(
            domain=value_cols,
            range=["#ffcc00", "#333333", "#999999"][:len(value_cols)]
        )),
        tooltip=[
            alt.Tooltip("INDEX_DATE:T", title="Date", format="%b %Y"),
            alt.Tooltip("Type:N"),
            alt.Tooltip("€/m²:Q", format=fmt_str),
        ],
    ).properties(height=320).interactive(bind_x=True, bind_y=False)

    st.altair_chart(chart, use_container_width=True)

    _price_colors = ["#ffcc00", "#333333", "#999999"]
    report.add_time_series(
        "Price History (\u20ac/m\u00b2)", pivot, "INDEX_DATE",
        primary=[(c, c, _price_colors[i % 3]) for i, c in enumerate(value_cols)],
        primary_label="\u20ac/m\u00b2",
        fmt=("{:,.0f}" if dec == 0 else "{:,.2f}"),
    )
else:
    st.info("No price history available (absolute price data required).")

# ---------------------------------------------------------------------------
# Price Evolution Table (below chart)
# ---------------------------------------------------------------------------
with st.expander("Price Evolution (all periods)", expanded=False):
    windows = ["VAR_1M", "VAR_3M", "VAR_6M", "VAR_YTD", "VAR_1Y", "VAR_2Y", "VAR_3Y", "VAR_5Y", "VAR_ALL"]
    window_labels = ["1m", "3m", "6m", "YTD", "1y", "2y", "3y", "5y", "All"]

    if not place_summary.empty:
        var_rows = []
        for item_key, label in item_map.items():
            row = place_summary[place_summary["ITEM_TYPE"] == item_key]
            if row.empty:
                continue
            r = row.iloc[0]
            var_row = {"Property": label}
            for w, wl in zip(windows, window_labels):
                v = r.get(w)
                var_row[wl] = round(v * 100, 1) if pd.notna(v) else None
            var_rows.append(var_row)
        if var_rows:
            var_df = pd.DataFrame(var_rows).set_index("Property")
            var_styler = (
                var_df.style.format("{:+.1f}%", na_rep="\u2014")
                .map(lambda v: "color:#1db954" if (pd.notna(v) and v >= 0)
                     else ("color:#e74c3c" if pd.notna(v) else ""))
            )
            st.dataframe(var_styler, use_container_width=True)
            report.add_table("Price Evolution (all periods)", var_styler.to_html())
    else:
        st.info("No variation data available.")

# ---------------------------------------------------------------------------
# Regional Overview: Map + Price Comparison (50/50, taller)
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Regional Overview")
report.heading("Regional Overview")

map_metric = st.radio("Color by", ["Price €/m²", "1-year change %"], horizontal=True,
                      key="map_metric", label_visibility="collapsed")

# Determine which polygons to load
use_nearby = False
poly_level = None
poly_parent_col = None
poly_parent_id = None
ref_lat, ref_lon = None, None

if place_type == "AD08":
    parent_info = data.get_parent(session, place_id)
    parent_district = parent_info.get("ad06") if parent_info else None
    parent_state = parent_info.get("ad04") if parent_info else None
    sibling_count = data.get_sibling_count(session, parent_district) if parent_district else 0
    if sibling_count <= 1:
        geo_self = data.get_geo(session, "AD08")
        me = geo_self[geo_self["place_id"] == place_id]
        if not me.empty:
            ref_lat, ref_lon = float(me.iloc[0]["lat"]), float(me.iloc[0]["lon"])
            use_nearby = True
            poly_level = "AD06"
    elif parent_district:
        poly_level = "AD08"
        poly_parent_col = "PARENT_AD06"
        poly_parent_id = parent_district
elif place_type == "AD06":
    parent_info = data.get_parent(session, place_id)
    parent_state = parent_info.get("ad04") if parent_info else None
    if parent_state:
        poly_level = "AD06"
        poly_parent_col = "PARENT_AD04"
        poly_parent_id = parent_state
elif place_type == "AD04":
    poly_level = "AD06"
    poly_parent_col = "PARENT_AD04"
    poly_parent_id = place_id
else:
    poly_level = "AD04"

map_features = []
sub_area_df = pd.DataFrame()
matched = pd.DataFrame()

if poly_level:
    with st.spinner("Loading map..."):
        if use_nearby and ref_lat is not None:
            polys = data.get_nearby_polygons(session, ref_lat, ref_lon, poly_level, limit=50)
        else:
            polys = data.get_polygons(session, poly_level, poly_parent_col, poly_parent_id)
        map_summary = data.get_market_summary(session, txn, poly_level)
        map_hybrid = map_summary[map_summary["ITEM_TYPE"] == "ITEM_TYPE.HYBRID"]

        merge_cols = list(set(["PLACE_ID", "PRICE_SQM", "VAR_1Y", growth_col]))
        poly_data = polys.merge(
            map_hybrid[merge_cols],
            left_on="place_id", right_on="PLACE_ID", how="inner"
        )

        if not poly_data.empty:
            is_price_map = map_metric == "Price €/m²"
            val_col = "PRICE_SQM" if is_price_map else "VAR_1Y"
            poly_data = poly_data.dropna(subset=[val_col, "geojson"])

            if not poly_data.empty:
                vals = poly_data[val_col]
                lo, hi = vals.quantile(0.05), vals.quantile(0.95)

                def map_color(v):
                    if pd.isna(v):
                        return [200, 200, 200, 100]
                    if is_price_map:
                        t = np.clip((v - lo) / (hi - lo + 1e-9), 0, 1)
                        return [255, int(220 - 180 * t), int(50 * (1 - t)), 180]
                    m = max(abs(lo), abs(hi), 1e-9)
                    t = np.clip(v / m, -1, 1)
                    if t >= 0:
                        return [int(255 * (1 - t)), int(180 + 75 * t), 0, 180]
                    return [255, int(180 * (1 + t)), 0, 180]

                all_lats, all_lons = [], []
                for _, row in poly_data.iterrows():
                    try:
                        geom = json.loads(row["geojson"])
                    except (ValueError, TypeError):
                        continue
                    v = row[val_col]
                    color = map_color(v)
                    tip = fmt_eur(v) if is_price_map else f"{v*100:+.1f}%"
                    map_features.append({
                        "type": "Feature", "geometry": geom,
                        "properties": {"name": row["name"], "value": tip, "fill": color},
                    })

                # Collect centroids for auto-zoom
                geo_for_zoom = data.get_geo(session, poly_level)
                matched = geo_for_zoom[geo_for_zoom["place_id"].isin(poly_data["place_id"])]
                if not matched.empty:
                    all_lats = matched["lat"].dropna().tolist()
                    all_lons = matched["lon"].dropna().tolist()

                sub_area_df = poly_data[["name", "PLACE_ID", "PRICE_SQM", growth_col]].copy()
                sub_area_df = sub_area_df.dropna(subset=["PRICE_SQM"]).sort_values("PRICE_SQM", ascending=False).head(20)

# Render 50/50 side by side
col_map, col_sub = st.columns(2)

with col_map:
    if map_features:
        center_lat = np.mean(all_lats) if all_lats else 51.1
        center_lon = np.mean(all_lons) if all_lons else 10.4
        zoom = auto_zoom(all_lats, all_lons)

        # Main choropleth layer
        layer = pdk.Layer(
            "GeoJsonLayer",
            {"type": "FeatureCollection", "features": map_features},
            stroked=True, filled=True,
            get_fill_color="properties.fill",
            get_line_color=[80, 80, 80, 160],
            line_width_min_pixels=0.5,
            pickable=True,
        )

        layers = [layer]

        # Highlight selected place with yellow border
        sel_features = [f for f in map_features if f["properties"]["name"] == place_name]
        if sel_features:
            highlight_layer = pdk.Layer(
                "GeoJsonLayer",
                {"type": "FeatureCollection", "features": sel_features},
                stroked=True, filled=False,
                get_line_color=[255, 204, 0, 255],
                line_width_min_pixels=3,
                pickable=False,
            )
            layers.append(highlight_layer)

        # Label only the selected place
        if not matched.empty:
            sel_label = matched[matched["name"] == place_name].head(1)
            if not sel_label.empty:
                text_layer = pdk.Layer(
                    "TextLayer",
                    sel_label[["name", "lat", "lon"]].rename(columns={"lat": "latitude", "lon": "longitude", "name": "text"}),
                    get_position=["longitude", "latitude"],
                    get_text="text",
                    get_size=14,
                    get_color=[30, 30, 30, 255],
                    get_alignment_baseline="'center'",
                    font_family="'Arial'",
                    font_weight="'bold'",
                    pickable=False,
                )
                layers.append(text_layer)

        st.pydeck_chart(pdk.Deck(
            layers=layers,
            initial_view_state=pdk.ViewState(
                latitude=center_lat, longitude=center_lon, zoom=zoom, pitch=0
            ),
            tooltip={"text": "{name}\n{value}"},
            map_style="mapbox://styles/mapbox/light-v10",
        ), use_container_width=True, height=500)

        report.add_choropleth(
            f"Regional Map — {map_metric}", map_features,
            selected_name=place_name, metric_label=map_metric,
        )
    else:
        st.info("No map data available.")

with col_sub:
    st.markdown(f"**Price Comparison** ({growth_label})")
    if not sub_area_df.empty:
        max_price = sub_area_df["PRICE_SQM"].max()
        html_items = []
        for _, row in sub_area_df.iterrows():
            pct = row["PRICE_SQM"] / max_price * 100
            var = row.get(growth_col)
            var_str = ""
            if pd.notna(var):
                cls = "var-positive" if var >= 0 else "var-negative"
                var_str = f'<span class="{cls}">{var*100:+.1f}%</span>'
            is_sel = row["name"] == place_name
            hl = "border-left:3px solid #ffcc00; font-weight:700;" if is_sel else ""
            html_items.append(f"""
            <div style="padding:4px 8px; margin-bottom:3px; {hl}">
                <div style="display:flex; justify-content:space-between; font-size:0.82rem;">
                    <span>{row['name']}</span>
                    <span>{fmt_eur(row['PRICE_SQM'])} {var_str}</span>
                </div>
                <div style="background:#eee; border-radius:3px; height:5px; margin-top:2px;">
                    <div style="background:#ffcc00; width:{pct:.0f}%; height:5px; border-radius:3px;"></div>
                </div>
            </div>""")
        st.markdown(f'<div class="price-list">{"".join(html_items)}</div>', unsafe_allow_html=True)
        report.add_ranking(f"Price Comparison ({growth_label})", "".join(html_items))
    else:
        st.info("No comparison data available.")

# ---------------------------------------------------------------------------
# Socio-Economic Summary
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Socio-Economic Summary")
report.heading("Socio-Economic Summary")

with st.spinner("Loading socio-economic data..."):
    income_df = data.get_income(session, place_id)
    income_nat = data.get_income_national(session)
    # For demographics, use parent district for cities
    demo_parent = None
    if place_type == "AD08":
        p_info = data.get_parent(session, place_id)
        demo_parent = p_info.get("ad06") if p_info else None
    elif place_type == "AD02":
        demo_parent = "AD06DE225"  # fallback national — skip demographics
    demo = data.get_demographics(session, place_id, parent_ad06=demo_parent) if place_type != "AD02" else {}

# Row 1: Salary + Demographics KPI cards
sal_val = "n/a"
sal_yoy = None
sal_sub = ""
if not income_df.empty:
    headline = income_df[income_df["CATEGORY"] == "Insgesamt"]
    if not headline.empty:
        h = headline.iloc[0]
        sal_val = fmt_salary(h["MEDIAN_2025"])
        sal_yoy = (h["MEDIAN_2025"] - h["MEDIAN_2024"]) / h["MEDIAN_2024"] if h["MEDIAN_2024"] else None
        nat_row = income_nat[income_nat["CATEGORY"] == "Insgesamt"]
        if not nat_row.empty:
            nat_sal = int(nat_row.iloc[0]["MEDIAN_2025"])
            diff = int(h["MEDIAN_2025"] - nat_sal)
            sal_sub = f"{'+' if diff >= 0 else ''}{diff:,}€ vs. national".replace(",", ".")

sc1, sc2, sc3 = st.columns(3)
with sc1:
    st.markdown(kpi_card("Median Salary", sal_val, sal_sub if sal_sub else "Monthly gross, all workers", sal_yoy), unsafe_allow_html=True)
with sc2:
    avg_age = demo.get("avg_age")
    st.markdown(kpi_card("Average Age", f"{avg_age:.1f}" if avg_age else "n/a", "District average"), unsafe_allow_html=True)
with sc3:
    pop = demo.get("population") or data.get_population(session, place_id)
    pop_str = f"{int(pop):,}".replace(",", ".") if pop else "n/a"
    st.markdown(kpi_card("Population", pop_str, "Registered residents"), unsafe_allow_html=True)

sc4, sc5, sc6 = st.columns(3)
with sc4:
    hh = demo.get("households")
    pop2 = demo.get("population")
    if hh and pop2 and hh > 0:
        hh_size = pop2 / hh
        st.markdown(kpi_card("Household Size", f"{hh_size:.2f}", "Avg. persons per household"), unsafe_allow_html=True)
    else:
        st.markdown(kpi_card("Household Size", "n/a"), unsafe_allow_html=True)
with sc5:
    density = demo.get("density")
    st.markdown(kpi_card("Pop. Density", f"{int(density):,}/km²".replace(",", ".") if density else "n/a", "Inhabitants per km²"), unsafe_allow_html=True)
with sc6:
    pct_single = demo.get("pct_hh_single")
    st.markdown(kpi_card("Single Households", f"{pct_single:.1f}%" if pct_single else "n/a", "1-person households"), unsafe_allow_html=True)

# Collapsible income breakdown
if not income_df.empty:
    with st.expander("Income Breakdown (by category)"):
        cat_labels = {
            "Insgesamt": "All Workers",
            "Männer": "Men",
            "Frauen": "Women",
            "unter 25 Jahre": "Under 25",
            "25 bis unter 55 Jahre": "25–54",
            "55 Jahre und älter": "55+",
            "Deutsche": "German",
            "Ausländer": "Foreign",
            "ohne Berufsabschluss": "No Qualification",
            "anerkannter Berufsabschluss": "Vocational",
            "akademischer Berufsabschluss": "Academic",
            "Helfer": "Helper",
            "Fachkraft": "Skilled Worker",
            "Spezialist": "Specialist",
            "Experte": "Expert",
        }
        tbl = income_df.copy()
        tbl["Category"] = tbl["CATEGORY"].map(cat_labels).fillna(tbl["CATEGORY"])
        tbl["2021"] = tbl["MEDIAN_2021"].apply(fmt_salary)
        tbl["2022"] = tbl["MEDIAN_2022"].apply(fmt_salary)
        tbl["2023"] = tbl["MEDIAN_2023"].apply(fmt_salary)
        tbl["2024"] = tbl["MEDIAN_2024"].apply(fmt_salary)
        tbl["2025"] = tbl["MEDIAN_2025"].apply(fmt_salary)
        tbl["YoY"] = ((tbl["MEDIAN_2025"] - tbl["MEDIAN_2024"]) / tbl["MEDIAN_2024"] * 100).round(1)
        tbl["YoY"] = tbl["YoY"].apply(lambda v: f"+{v:.1f}%" if v >= 0 else f"{v:.1f}%")
        display_tbl = tbl[["Category", "2021", "2022", "2023", "2024", "2025", "YoY"]].set_index("Category")
        st.dataframe(display_tbl, use_container_width=True)
        # (Income breakdown table intentionally excluded from the HTML export — highlight KPIs only.)

# ---------------------------------------------------------------------------
# Purchasing Power
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader(f"Purchasing Power ({household_type}{'  incl. realtor' if include_realtor else ''})")
report.heading(f"Purchasing Power ({household_type}{' incl. realtor' if include_realtor else ''})")

pp_col = f"PP_SQM_{household_type.upper()}_{'W_REALTOR' if include_realtor else 'NO_REALTOR'}"

if txn == "TRANSACTION_TYPE.SELL":
    pp_df = data.get_purchasing_power(session, place_id)
    if not pp_df.empty:
        pp1, pp2, pp3 = st.columns(3)
        for col, item_key in zip([pp1, pp2, pp3], ["ITEM_TYPE.APARTMENT", "ITEM_TYPE.HOUSE", "ITEM_TYPE.HYBRID"]):
            row = pp_df[pp_df["ITEM_TYPE"] == item_key]
            with col:
                if not row.empty and pd.notna(row.iloc[0].get(pp_col)):
                    sqm = row.iloc[0][pp_col]
                    st.markdown(kpi_card(
                        item_map[item_key],
                        f"{sqm:.0f} m²",
                        f"at {fmt_eur(row.iloc[0]['PRICE_SQM'])}",
                    ), unsafe_allow_html=True)
                else:
                    st.markdown(kpi_card(item_map[item_key], "n/a"), unsafe_allow_html=True)

        # Purchasing power evolution table
        pp_hist = data.get_purchasing_power_history(session, place_id)
        if not pp_hist.empty:
            pp_hist["INDEX_DATE"] = pd.to_datetime(pp_hist["INDEX_DATE"])
            pp_hist["m²"] = pp_hist[pp_col]
            pp_hist = pp_hist.dropna(subset=["m²"])
            # Filter outliers
            med = pp_hist["m²"].median()
            pp_hist = pp_hist[pp_hist["m²"] <= med * 5]

            if not pp_hist.empty:
                latest_date = pp_hist["INDEX_DATE"].max()
                windows = {"1m": 1, "3m": 3, "6m": 6, "1y": 12}
                pp_var_rows = []
                for item_key, label in item_map.items():
                    item_data = pp_hist[pp_hist["ITEM_TYPE"] == item_key].set_index("INDEX_DATE").sort_index()
                    if item_data.empty:
                        continue
                    latest_val = item_data.iloc[-1]["m²"]
                    row = {"Property": label}
                    for wl, months in windows.items():
                        target = latest_date - pd.DateOffset(months=months)
                        past = item_data[item_data.index <= target]
                        if not past.empty:
                            past_val = past.iloc[-1]["m²"]
                            row[wl] = round((latest_val - past_val) / past_val * 100, 1) if past_val > 0 else None
                        else:
                            row[wl] = None
                    pp_var_rows.append(row)

                if pp_var_rows:
                    pp_var_df = pd.DataFrame(pp_var_rows).set_index("Property")
                    pp_var_styler = (
                        pp_var_df.style.format("{:+.1f}%", na_rep="\u2014")
                        .map(lambda v: "color:#1db954" if (pd.notna(v) and v >= 0)
                             else ("color:#e74c3c" if pd.notna(v) else ""))
                    )
                    st.dataframe(pp_var_styler, use_container_width=True)
                    report.add_table("Purchasing Power \u2014 change in m\u00b2 affordable", pp_var_styler.to_html())

            # Historical chart
            with st.expander("Purchasing Power History (m²)", expanded=False):
                pp_chart_data = pp_hist.copy()
                pp_chart_data["Type"] = pp_chart_data["ITEM_TYPE"].map(item_map)
                # Default to 3 years, expandable
                three_yr_ago = pp_chart_data["INDEX_DATE"].max() - pd.DateOffset(years=3)
                show_full_pp = st.checkbox("Show full history", value=False, key="full_pp")
                if not show_full_pp:
                    pp_chart_data = pp_chart_data[pp_chart_data["INDEX_DATE"] >= three_yr_ago]
                if not pp_chart_data.empty:
                    pp_chart = alt.Chart(pp_chart_data).mark_line(strokeWidth=2, point=False).encode(
                        x=alt.X("INDEX_DATE:T", title=""),
                        y=alt.Y("m²:Q", title="m² affordable", scale=alt.Scale(zero=False)),
                        color=alt.Color("Type:N", scale=alt.Scale(
                            range=["#ffcc00", "#333333", "#999999"]
                        )),
                        tooltip=[
                            alt.Tooltip("INDEX_DATE:T", title="Date", format="%b %Y"),
                            alt.Tooltip("Type:N"),
                            alt.Tooltip("m²:Q", format=".1f"),
                        ],
                    ).properties(height=250)
                    st.altair_chart(pp_chart, use_container_width=True)

                    pp_wide = pp_chart_data.pivot_table(index="INDEX_DATE", columns="Type", values="m²").reset_index()
                    _pp_pal = {"Apartment": "#ffcc00", "House": "#333333", "Hybrid": "#999999"}
                    report.add_time_series(
                        "Purchasing Power History (m\u00b2 affordable)", pp_wide, "INDEX_DATE",
                        primary=[(t, t, _pp_pal.get(t, "#999999")) for t in pp_wide.columns if t != "INDEX_DATE"],
                        primary_label="m\u00b2 affordable", fmt="{:,.1f}",
                    )
    else:
        st.info("No purchasing power data available for this area.")
else:
    st.info("Purchasing power is only calculated for the Buy market.")

# ---------------------------------------------------------------------------
# Interest Rates
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Interest Rates (10yr Fixed, 90% LTV)")
report.heading("Interest Rates (10yr Fixed, 90% LTV)")

rates_df = data.get_interest_rates(session)
if not rates_df.empty:
    rates_df["RATE_DATE"] = pd.to_datetime(rates_df["RATE_DATE"])
    latest = rates_df.iloc[-1]
    rate_val = latest["NOMINAL_RATE"]

    # Calculate rate change based on selected growth period
    growth_months = {"VAR_1M": 1, "VAR_3M": 3, "VAR_6M": 6, "VAR_YTD": None,
                     "VAR_1Y": 12, "VAR_2Y": 24, "VAR_3Y": 36, "VAR_5Y": 60, "VAR_ALL": None}
    months_back = growth_months.get(growth_col)
    if months_back:
        target_date = latest["RATE_DATE"] - pd.DateOffset(months=months_back)
        past_rows = rates_df[rates_df["RATE_DATE"] <= target_date]
        if not past_rows.empty:
            past_rate = past_rows.iloc[-1]["NOMINAL_RATE"]
            rate_chg_pct = (rate_val - past_rate) / past_rate if past_rate else None
        else:
            rate_chg_pct = None
    else:
        # YTD or ALL: compare to first of year or first available
        if growth_col == "VAR_YTD":
            yr_start = rates_df[rates_df["RATE_DATE"].dt.year == latest["RATE_DATE"].year].iloc[0]
            rate_chg_pct = (rate_val - yr_start["NOMINAL_RATE"]) / yr_start["NOMINAL_RATE"] if yr_start["NOMINAL_RATE"] else None
        else:
            first_rate = rates_df.iloc[0]["NOMINAL_RATE"]
            rate_chg_pct = (rate_val - first_rate) / first_rate if first_rate else None

    ir1, ir2 = st.columns([1, 3])
    with ir1:
        st.markdown(kpi_card("Current Rate", f"{rate_val:.2f}%",
                             growth_label,
                             rate_chg_pct, invert_color=True), unsafe_allow_html=True)
    with ir2:
        # Default to last 3 years, expandable
        three_years_ago = rates_df["RATE_DATE"].max() - pd.DateOffset(years=3)
        rates_3y = rates_df[rates_df["RATE_DATE"] >= three_years_ago]
        show_full_rates = st.checkbox("Show full history", value=False, key="full_rates")
        chart_data = rates_df if show_full_rates else rates_3y

        rate_chart = alt.Chart(chart_data).mark_line(strokeWidth=2.5, color="#ffcc00").encode(
            x=alt.X("RATE_DATE:T", title=""),
            y=alt.Y("NOMINAL_RATE:Q", title="Rate %", scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("RATE_DATE:T", title="Date", format="%b %Y"),
                alt.Tooltip("NOMINAL_RATE:Q", title="Rate", format=".2f"),
            ],
        ).properties(height=200)
        st.altair_chart(rate_chart, use_container_width=True)
        report.add_time_series(
            "Interest Rate History", chart_data, "RATE_DATE",
            primary=[("NOMINAL_RATE", "Rate %", "#ffcc00")],
            primary_label="Rate %", fmt="{:.2f}",
        )
else:
    st.info("No interest rate data available.")

# ---------------------------------------------------------------------------
# Demand (Clicks & Leads)
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Demand (Clicks & Leads)")
report.heading("Demand (Clicks & Leads)")

# For AD08 cities, use parent district name for demand lookup
demand_name = place_name
demand_type = place_type
if place_type == "AD08":
    p_info = data.get_parent(session, place_id)
    if p_info and p_info.get("ad06"):
        # Get district name
        parent_geo = data.get_geo(session, "AD06")
        parent_match = parent_geo[parent_geo["place_id"] == p_info["ad06"]]
        if not parent_match.empty:
            demand_name = parent_match.iloc[0]["name"]
    demand_type = "AD06"

demand_df = data.get_demand(session, demand_name, demand_type)
if not demand_df.empty and len(demand_df) > 1:
    demand_df["MONTH_DATE"] = pd.to_datetime(demand_df["MONTH_DATE"])
    # Exclude current incomplete month
    demand_df = demand_df[demand_df["MONTH_DATE"] < demand_df["MONTH_DATE"].max()]

    if not demand_df.empty:
        latest = demand_df.iloc[-1]
        prev = demand_df.iloc[-2] if len(demand_df) > 1 else None

        is_buy = "Kaufen" in txn_label
        clicks_col = "CLICKS_BUY" if is_buy else "CLICKS_RENT"
        leads_col = "LEADS_BUY" if is_buy else "LEADS_RENT"

        clicks_now = int(latest[clicks_col])
        leads_now = int(latest[leads_col])
        clicks_mom = (clicks_now - int(prev[clicks_col])) / int(prev[clicks_col]) if prev is not None and int(prev[clicks_col]) > 0 else None
        leads_mom = (leads_now - int(prev[leads_col])) / int(prev[leads_col]) if prev is not None and int(prev[leads_col]) > 0 else None

        d1, d2 = st.columns(2)
        with d1:
            clicks_str = f"{clicks_now:,.0f}".replace(",", ".")
            st.markdown(kpi_card("Detail Views", clicks_str, f"{latest['MONTH_DATE'].strftime('%b %Y')}", clicks_mom), unsafe_allow_html=True)
        with d2:
            leads_str = f"{leads_now:,.0f}".replace(",", ".")
            st.markdown(kpi_card("Leads", leads_str, f"{latest['MONTH_DATE'].strftime('%b %Y')}", leads_mom), unsafe_allow_html=True)

        # Chart: clicks + leads on separate y-axes
        chart_df = demand_df[["MONTH_DATE", clicks_col, leads_col]].copy()
        chart_df = chart_df.rename(columns={clicks_col: "Detail Views", leads_col: "Leads"})

        base = alt.Chart(chart_df).encode(x=alt.X("MONTH_DATE:T", title=""))

        views_line = base.mark_line(strokeWidth=2.5, color="#ffcc00").encode(
            y=alt.Y("Detail Views:Q", title="Detail Views", axis=alt.Axis(titleColor="#b8960a")),
            tooltip=[
                alt.Tooltip("MONTH_DATE:T", title="Month", format="%b %Y"),
                alt.Tooltip("Detail Views:Q", format=",.0f"),
            ],
        )
        leads_line = base.mark_line(strokeWidth=2.5, color="#333333").encode(
            y=alt.Y("Leads:Q", title="Leads", axis=alt.Axis(titleColor="#333333")),
            tooltip=[
                alt.Tooltip("MONTH_DATE:T", title="Month", format="%b %Y"),
                alt.Tooltip("Leads:Q", format=",.0f"),
            ],
        )

        demand_chart = alt.layer(views_line, leads_line).resolve_scale(y="independent").properties(height=250)
        st.altair_chart(demand_chart, use_container_width=True)
        report.add_time_series(
            "Demand \u2014 Detail Views & Leads", chart_df, "MONTH_DATE",
            primary=[("Detail Views", "Detail Views", "#ffcc00")],
            secondary=[("Leads", "Leads", "#333333")],
            primary_label="Detail Views", secondary_label="Leads", fmt="{:,.0f}",
        )
else:
    st.info("No demand data available for this area.")

# ---------------------------------------------------------------------------
# Listings Insights (Supply)
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Listings Supply")
report.heading("Listings Supply")

# For AD08 cities, use parent district name
listings_name = demand_name  # reuse same geo logic from demand section
listings_type = demand_type

listings_df = data.get_listings_insights(session, listings_name, listings_type, txn)
if not listings_df.empty:
    listings_df["MONTH_DATE"] = pd.to_datetime(listings_df["MONTH_DATE"])
    # Exclude incomplete current month
    listings_df = listings_df[listings_df["MONTH_DATE"] < listings_df["MONTH_DATE"].max()]

    if not listings_df.empty:
        latest_month = listings_df["MONTH_DATE"].max()
        prev_month = latest_month - pd.DateOffset(months=1)

        # Combined totals
        latest_all = listings_df[listings_df["MONTH_DATE"] == latest_month]
        prev_all = listings_df[listings_df["MONTH_DATE"] == prev_month]
        total_active = int(latest_all["ACTIVE_LISTINGS"].sum())
        total_new = int(latest_all["NEW_LISTINGS"].sum())
        prev_active_total = int(prev_all["ACTIVE_LISTINGS"].sum()) if not prev_all.empty else None
        active_mom_total = (total_active - prev_active_total) / prev_active_total if prev_active_total and prev_active_total > 0 else None

        st.markdown(f"**Combined** — {latest_month.strftime('%b %Y')}")
        report.subheading(f"Combined \u2014 {latest_month.strftime('%b %Y')}")
        tc1, tc2, tc3 = st.columns(3)
        with tc1:
            st.markdown(kpi_card("Active Listings", f"{total_active:,.0f}".replace(",", "."), "Apartment + House", active_mom_total), unsafe_allow_html=True)
        with tc2:
            prev_new_total = int(prev_all["NEW_LISTINGS"].sum()) if not prev_all.empty else None
            new_mom_total = (total_new - prev_new_total) / prev_new_total if prev_new_total and prev_new_total > 0 else None
            st.markdown(kpi_card("New Listings", f"{total_new:,.0f}".replace(",", "."), "This month", new_mom_total), unsafe_allow_html=True)
        with tc3:
            # Absorption rate: new / active
            absorption = total_new / total_active * 100 if total_active > 0 else None
            st.markdown(kpi_card("Turnover Rate", f"{absorption:.1f}%" if absorption else "n/a", "New / Active (monthly)"), unsafe_allow_html=True)

        # Per estate type
        for estate in ["APARTMENT", "HOUSE"]:
            estate_label = "Apartment" if estate == "APARTMENT" else "House"
            edf = listings_df[listings_df["ESTATE_TYPE"] == estate]
            if edf.empty:
                continue

            latest_e = edf[edf["MONTH_DATE"] == latest_month]
            prev_e = edf[edf["MONTH_DATE"] == prev_month]
            if latest_e.empty:
                continue

            le = latest_e.iloc[0]
            active = int(le["ACTIVE_LISTINGS"])
            new = int(le["NEW_LISTINGS"])
            size = le["AVG_SIZE_SQM"]
            rooms = le["AVG_ROOMS"]
            price_sqm = le["AVG_PRICE_SQM"]
            # Estimate absolute price
            est_price = price_sqm * size if pd.notna(price_sqm) and pd.notna(size) and price_sqm > 0 else None

            prev_active_e = int(prev_e.iloc[0]["ACTIVE_LISTINGS"]) if not prev_e.empty else None
            active_mom_e = (active - prev_active_e) / prev_active_e if prev_active_e and prev_active_e > 0 else None

            st.markdown(f"**{estate_label}**")
            report.subheading(estate_label)
            lc1, lc2, lc3, lc4 = st.columns(4)
            with lc1:
                st.markdown(kpi_card("Active", f"{active:,.0f}".replace(",", "."), "", active_mom_e), unsafe_allow_html=True)
            with lc2:
                st.markdown(kpi_card("New", f"{new:,.0f}".replace(",", "."), ""), unsafe_allow_html=True)
            with lc3:
                size_str = f"{size:.0f} m²" if pd.notna(size) else "n/a"
                st.markdown(kpi_card("Avg. Size", size_str, f"{rooms:.1f} rooms" if pd.notna(rooms) else ""), unsafe_allow_html=True)
            with lc4:
                if est_price:
                    price_str = f"€{int(est_price):,.0f}".replace(",", ".")
                    st.markdown(kpi_card("Avg. Price", price_str, "Listed total"), unsafe_allow_html=True)
                else:
                    st.markdown(kpi_card("Avg. Price", "n/a"), unsafe_allow_html=True)

        # Supply trend chart
        with st.expander("Supply Trend", expanded=False):
            trend_df = listings_df.copy()
            trend_df["Type"] = trend_df["ESTATE_TYPE"].map({"APARTMENT": "Apartment", "HOUSE": "House"})

            active_chart = alt.Chart(trend_df).mark_line(strokeWidth=2).encode(
                x=alt.X("MONTH_DATE:T", title=""),
                y=alt.Y("ACTIVE_LISTINGS:Q", title="Active Listings"),
                color=alt.Color("Type:N", scale=alt.Scale(
                    domain=["Apartment", "House"],
                    range=["#ffcc00", "#333333"]
                )),
                tooltip=[
                    alt.Tooltip("MONTH_DATE:T", format="%b %Y"),
                    alt.Tooltip("Type:N"),
                    alt.Tooltip("ACTIVE_LISTINGS:Q", title="Active", format=",.0f"),
                    alt.Tooltip("NEW_LISTINGS:Q", title="New", format=",.0f"),
                ],
            ).properties(height=220)
            st.altair_chart(active_chart, use_container_width=True)
            _active_wide = trend_df.pivot_table(index="MONTH_DATE", columns="Type", values="ACTIVE_LISTINGS").reset_index()
            report.add_time_series(
                "Supply Trend \u2014 Active Listings", _active_wide, "MONTH_DATE",
                primary=[("Apartment", "Apartment", "#ffcc00"), ("House", "House", "#333333")],
                primary_label="Active Listings", fmt="{:,.0f}",
            )

        # Avg Size trend
        with st.expander("Avg. Size Development", expanded=False):
            trend_df = listings_df.copy()
            trend_df["Type"] = trend_df["ESTATE_TYPE"].map({"APARTMENT": "Apartment", "HOUSE": "House"})
            trend_df = trend_df.dropna(subset=["AVG_SIZE_SQM"])

            apt_size = trend_df[trend_df["Type"] == "Apartment"]
            house_size = trend_df[trend_df["Type"] == "House"]

            base_s = alt.Chart(apt_size).encode(x=alt.X("MONTH_DATE:T", title=""))
            apt_line = base_s.mark_line(strokeWidth=2.5, color="#ffcc00").encode(
                y=alt.Y("AVG_SIZE_SQM:Q", title="Apartment (m²)", scale=alt.Scale(zero=False), axis=alt.Axis(titleColor="#b8960a")),
                tooltip=[alt.Tooltip("MONTH_DATE:T", format="%b %Y"), alt.Tooltip("AVG_SIZE_SQM:Q", title="Apt m²", format=".1f")],
            )
            base_h = alt.Chart(house_size).encode(x=alt.X("MONTH_DATE:T", title=""))
            house_line = base_h.mark_line(strokeWidth=2.5, color="#333333").encode(
                y=alt.Y("AVG_SIZE_SQM:Q", title="House (m²)", scale=alt.Scale(zero=False), axis=alt.Axis(titleColor="#333333")),
                tooltip=[alt.Tooltip("MONTH_DATE:T", format="%b %Y"), alt.Tooltip("AVG_SIZE_SQM:Q", title="House m²", format=".1f")],
            )
            size_chart = alt.layer(apt_line, house_line).resolve_scale(y="independent").properties(height=220)
            st.altair_chart(size_chart, use_container_width=True)
            _size_wide = trend_df.pivot_table(index="MONTH_DATE", columns="Type", values="AVG_SIZE_SQM").reset_index()
            report.add_time_series(
                "Avg. Size Development", _size_wide, "MONTH_DATE",
                primary=[("Apartment", "Apartment (m\u00b2)", "#ffcc00")],
                secondary=[("House", "House (m\u00b2)", "#333333")],
                primary_label="Apartment (m\u00b2)", secondary_label="House (m\u00b2)", fmt="{:,.1f}",
            )

        # Avg Price trend
        with st.expander("Avg. Listed Price Development", expanded=False):
            trend_df = listings_df.copy()
            trend_df["Type"] = trend_df["ESTATE_TYPE"].map({"APARTMENT": "Apartment", "HOUSE": "House"})
            trend_df["EST_PRICE"] = trend_df["AVG_PRICE_SQM"] * trend_df["AVG_SIZE_SQM"]
            trend_df = trend_df.dropna(subset=["EST_PRICE"])
            trend_df = trend_df[trend_df["EST_PRICE"] > 0]

            apt_price = trend_df[trend_df["Type"] == "Apartment"]
            house_price = trend_df[trend_df["Type"] == "House"]

            base_ap = alt.Chart(apt_price).encode(x=alt.X("MONTH_DATE:T", title=""))
            apt_p_line = base_ap.mark_line(strokeWidth=2.5, color="#ffcc00").encode(
                y=alt.Y("EST_PRICE:Q", title="Apartment (€)", scale=alt.Scale(zero=False), axis=alt.Axis(titleColor="#b8960a", format=",.0f")),
                tooltip=[alt.Tooltip("MONTH_DATE:T", format="%b %Y"), alt.Tooltip("EST_PRICE:Q", title="Apt €", format=",.0f")],
            )
            base_hp = alt.Chart(house_price).encode(x=alt.X("MONTH_DATE:T", title=""))
            house_p_line = base_hp.mark_line(strokeWidth=2.5, color="#333333").encode(
                y=alt.Y("EST_PRICE:Q", title="House (€)", scale=alt.Scale(zero=False), axis=alt.Axis(titleColor="#333333", format=",.0f")),
                tooltip=[alt.Tooltip("MONTH_DATE:T", format="%b %Y"), alt.Tooltip("EST_PRICE:Q", title="House €", format=",.0f")],
            )
            price_chart = alt.layer(apt_p_line, house_p_line).resolve_scale(y="independent").properties(height=220)
            st.altair_chart(price_chart, use_container_width=True)
            _eprice_wide = trend_df.pivot_table(index="MONTH_DATE", columns="Type", values="EST_PRICE").reset_index()
            report.add_time_series(
                "Avg. Listed Price Development", _eprice_wide, "MONTH_DATE",
                primary=[("Apartment", "Apartment (\u20ac)", "#ffcc00")],
                secondary=[("House", "House (\u20ac)", "#333333")],
                primary_label="Apartment (\u20ac)", secondary_label="House (\u20ac)", fmt="{:,.0f}",
            )
else:
    st.info("No listings data available for this area.")

# ---------------------------------------------------------------------------
# Placeholder Cards
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("More Insights")

p1, p2, p3 = st.columns(3)
with p1:
    st.markdown(placeholder_card("Rental Yield"), unsafe_allow_html=True)
with p2:
    st.markdown(placeholder_card("Trends & Features"), unsafe_allow_html=True)
with p3:
    st.markdown(placeholder_card("Vacancy Rate"), unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# HTML Export (shareable, fully offline — no Snowflake / no internet needed)
# ---------------------------------------------------------------------------
try:
    _report_html = report.to_html()
    _safe_place = "".join(c if c.isalnum() else "_" for c in place_name).strip("_") or "report"
    _market = "Buy" if "Kaufen" in txn_label else "Rent"
    _fname = f"Immowelt_{_safe_place}_{_market}_{pd.Timestamp.now():%Y%m%d}.html"
    export_slot.download_button(
        "⬇ Export as HTML",
        data=_report_html.encode("utf-8"),
        file_name=_fname,
        mime="text/html",
        use_container_width=True,
        help="Download a self-contained HTML snapshot of this view. Opens in any browser — no Snowflake access needed.",
    )
except Exception as _exc:  # never let export break the dashboard
    export_slot.caption(f"Export unavailable: {_exc}")


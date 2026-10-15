"""
Visualization and PDF generation for housing market analysis.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.backends.backend_pdf import PdfPages


def plot_purchasing_power(apartment_series, house_series, name, place_id, level, ax=None):
    """Plot purchasing power for a single location."""
    if ax is None:
        fig, ax = plt.subplots(figsize=(10, 6))

    ax.plot(apartment_series.index, apartment_series.values, label='Wohnung', color='#1f77b4', linewidth=1.5)
    ax.plot(house_series.index, house_series.values, label='Haus', color='#ff7f0e', linewidth=1.5)

    ax.set_title(f"Leistbarkeit fuer {name} ({level.title()})", fontsize=12, fontweight='bold')
    ax.set_xlabel('Datum')
    ax.set_ylabel('Leistbarkeit in m2')

    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=6))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
    plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    ax.legend(loc='upper right')
    ax.grid(True, alpha=0.3)

    return ax


def extract_series(df, id_col, name_col, place_id, n_months=None):
    """Extract time series from purchasing power dataframe."""
    row = df[df[id_col] == place_id]
    name = row[name_col].iloc[0] if name_col in row.columns else place_id

    drop_cols = [c for c in [id_col, name_col] if c in row.columns]
    series = row.drop(columns=drop_cols).T
    series.index = pd.to_datetime(series.index)
    series = series.iloc[:, 0]

    if n_months:
        series = series.tail(n_months)

    return series, name


def generate_purchasing_power_pdf(purchasing_power_summary, filename='purchasing_power_trends.pdf', n_months=None):
    """Generate PDF with purchasing power charts for all locations."""
    geo_config = {
        'country': {'id_col': 'country_id', 'name_col': 'country'},
        'state': {'id_col': 'state_id', 'name_col': 'state'},
        'district': {'id_col': 'district_id', 'name_col': 'district'},
        'city': {'id_col': 'city_id', 'name_col': 'city'},
    }

    with PdfPages(filename) as pdf:
        for level in purchasing_power_summary['apartment'].keys():
            apt_df = purchasing_power_summary['apartment'].get(level)
            house_df = purchasing_power_summary['house'].get(level)

            if apt_df is None or house_df is None:
                continue

            config = geo_config.get(level, {'id_col': f'{level}_id', 'name_col': level})
            id_col = config['id_col']
            name_col = config['name_col']

            if id_col not in apt_df.columns:
                continue

            for place_id in apt_df[id_col].unique():
                try:
                    apt_series, name = extract_series(apt_df, id_col, name_col, place_id, n_months)
                    house_series, _ = extract_series(house_df, id_col, name_col, place_id, n_months)

                    fig, ax = plt.subplots(figsize=(10, 6))
                    plot_purchasing_power(apt_series, house_series, name, place_id, level, ax=ax)
                    plt.tight_layout()
                    pdf.savefig(fig)
                    plt.close(fig)
                except Exception:
                    continue

    print(f"PDF saved to {filename}")


def insight_card(title, value, breakdown=None):
    """
    Yellow-header insight card for Streamlit dashboards.
    breakdown = list of (label, pct_value) tuples.
    """
    import streamlit as st

    st.markdown(
        f"""<div style="background:#FFC72C;border-radius:14px;padding:12px 18px;
        margin-bottom:8px;color:#1a1a2e;font-weight:600;font-size:1.05rem;">
        {title}: <span style="font-weight:800;">{value}</span></div>""",
        unsafe_allow_html=True,
    )
    if breakdown:
        for label, pct in breakdown:
            if pct is None or (isinstance(pct, float) and np.isnan(pct)):
                st.markdown(f"&nbsp;&nbsp;{label}: n/a", unsafe_allow_html=True)
            else:
                up = pct >= 0
                arrow = "\u2191" if up else "\u2193"
                color = "#1Db954" if up else "#e74c3c"
                st.markdown(
                    f"""<div style="padding:2px 10px;color:{color};font-weight:600;">
                    {arrow} {label}: {pct * 100:+.1f}%</div>""",
                    unsafe_allow_html=True,
                )

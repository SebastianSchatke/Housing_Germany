"""
Streamlit UI helpers. Kept separate from charts.py so dashboards don't need matplotlib.
"""
from __future__ import annotations

import numpy as np


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

# Builds a fully self-contained, offline HTML export of the housing dashboard (no CDN, no JS, inline SVG charts).
# Co-authored with CoCo
"""Standalone HTML report builder for the Immowelt Market Insights dashboard.

Design goals:
- The produced .html opens in any browser with ZERO internet and ZERO Snowflake access.
- No external scripts, stylesheets, fonts, or CDNs. Charts are rendered as inline <svg>
  in pure Python (stdlib + pandas only). KPI cards / tables / rankings reuse the app's
  own HTML so they look identical.

Public API (see ReportBuilder):
    r = ReportBuilder(place_name, market_label)
    r.set_meta(as_of=..., growth=..., household=..., realtor=...)
    r.heading("Prices (\u20ac/m\u00b2)")
    r.subheading("Apartment")
    r.card(html)                      # usually called automatically from kpi_card()
    r.add_table(title, html)
    r.add_ranking(title, html)
    r.add_time_series(title, df, x_col, primary=[(col,label,color)], secondary=[...])
    html = r.to_html()
"""

from __future__ import annotations

import datetime as _dt
import html as _html

import pandas as pd

# Palette (matches the dashboard)
YELLOW = "#ffcc00"
DARK = "#333333"
GREY = "#999999"


def esc(s) -> str:
    return _html.escape(str(s), quote=True)


def _fmt_de(text: str) -> str:
    """Swap thousands comma for a dot (German style)."""
    return text.replace(",", ".")


def _nice_ticks(lo: float, hi: float, n: int = 5):
    """Return ~n evenly spaced 'nice' tick values covering [lo, hi]."""
    if lo == hi:
        # Degenerate range: pad a little
        if lo == 0:
            lo, hi = -1.0, 1.0
        else:
            pad = abs(lo) * 0.1
            lo, hi = lo - pad, hi + pad
    span = hi - lo
    raw = span / max(n - 1, 1)
    mag = 10 ** (len(str(int(abs(raw)))) - 1) if abs(raw) >= 1 else 10 ** -3
    # Choose a nice step
    for mult in (1, 2, 2.5, 5, 10, 20, 25, 50, 100):
        step = mult * mag
        if step >= raw:
            break
    else:
        step = raw
    start = (int(lo / step)) * step
    ticks = []
    v = start
    while v <= hi + step * 0.5:
        if v >= lo - step * 0.5:
            ticks.append(round(v, 6))
        v += step
    if not ticks:
        ticks = [lo, hi]
    return ticks[0], ticks[-1], ticks


def _render_line_svg(xs, left_series, right_series, l_label, r_label, lfmt, rfmt):
    """Render a responsive inline SVG line chart.

    xs: list[pd.Timestamp] (full x domain, sorted)
    *_series: list of {"label", "color", "points": [(Timestamp, value), ...]}
    """
    W, H = 820, 340
    ml, mr, mt, mb = 66, (66 if right_series else 22), 42, 46
    plot_w = W - ml - mr
    plot_h = H - mt - mb

    x0 = min(xs).value
    x1 = max(xs).value
    xspan = (x1 - x0) or 1

    def px(ts):
        return ml + (ts.value - x0) / xspan * plot_w

    def collect(series):
        vals = [v for s in series for (_, v) in s["points"]]
        return vals

    lvals = collect(left_series)
    rvals = collect(right_series)
    l_lo, l_hi, l_ticks = _nice_ticks(min(lvals), max(lvals)) if lvals else (0, 1, [0, 1])
    r_lo, r_hi, r_ticks = _nice_ticks(min(rvals), max(rvals)) if rvals else (0, 1, [0, 1])

    def py_left(v):
        return mt + plot_h - (v - l_lo) / ((l_hi - l_lo) or 1) * plot_h

    def py_right(v):
        return mt + plot_h - (v - r_lo) / ((r_hi - r_lo) or 1) * plot_h

    parts = [
        f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="xMidYMid meet" '
        f'style="width:100%;height:auto;font-family:Arial,Helvetica,sans-serif;">'
    ]
    # Plot background
    parts.append(
        f'<rect x="{ml}" y="{mt}" width="{plot_w}" height="{plot_h}" fill="#ffffff" stroke="#eee"/>'
    )

    # Left gridlines + tick labels
    def lbl(v, fmt):
        try:
            return _fmt_de(fmt.format(v))
        except Exception:
            return str(v)

    for t in l_ticks:
        if t < l_lo - 1e-9 or t > l_hi + 1e-9:
            continue
        y = py_left(t)
        parts.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + plot_w}" y2="{y:.1f}" stroke="#f0f0f0"/>')
        parts.append(
            f'<text x="{ml - 8}" y="{y + 3:.1f}" text-anchor="end" font-size="11" fill="#888">{esc(lbl(t, lfmt))}</text>'
        )
    # Right axis tick labels
    if right_series:
        for t in r_ticks:
            if t < r_lo - 1e-9 or t > r_hi + 1e-9:
                continue
            y = py_right(t)
            parts.append(
                f'<text x="{ml + plot_w + 8}" y="{y + 3:.1f}" text-anchor="start" font-size="11" fill="#888">{esc(lbl(t, rfmt))}</text>'
            )

    # X tick labels (~6 evenly spaced across the domain)
    n_ticks = min(6, len(xs))
    if n_ticks >= 1:
        idxs = [round(i * (len(xs) - 1) / max(n_ticks - 1, 1)) for i in range(n_ticks)]
        seen = set()
        for i in idxs:
            if i in seen:
                continue
            seen.add(i)
            ts = xs[i]
            x = px(ts)
            parts.append(f'<line x1="{x:.1f}" y1="{mt + plot_h}" x2="{x:.1f}" y2="{mt + plot_h + 4}" stroke="#ccc"/>')
            parts.append(
                f'<text x="{x:.1f}" y="{mt + plot_h + 18}" text-anchor="middle" font-size="10" fill="#888">'
                f'{esc(ts.strftime("%b %y"))}</text>'
            )

    # Axis titles
    parts.append(
        f'<text x="14" y="{mt + plot_h / 2:.1f}" text-anchor="middle" font-size="11" fill="#666" '
        f'transform="rotate(-90 14 {mt + plot_h / 2:.1f})">{esc(l_label)}</text>'
    )
    if right_series and r_label:
        parts.append(
            f'<text x="{W - 12}" y="{mt + plot_h / 2:.1f}" text-anchor="middle" font-size="11" fill="#666" '
            f'transform="rotate(90 {W - 12} {mt + plot_h / 2:.1f})">{esc(r_label)}</text>'
        )

    # Series polylines + end labels
    def draw(series, pyf, fmt):
        for s in series:
            pts = s["points"]
            coords = " ".join(f"{px(ts):.1f},{pyf(v):.1f}" for ts, v in pts)
            if len(pts) == 1:
                ts, v = pts[0]
                parts.append(f'<circle cx="{px(ts):.1f}" cy="{pyf(v):.1f}" r="3" fill="{s["color"]}"/>')
            else:
                parts.append(
                    f'<polyline points="{coords}" fill="none" stroke="{s["color"]}" '
                    f'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
                )
            # End-point value label (compensates for lack of tooltips)
            ts, v = pts[-1]
            parts.append(
                f'<text x="{px(ts) - 4:.1f}" y="{pyf(v) - 6:.1f}" text-anchor="end" font-size="10" '
                f'font-weight="700" fill="{s["color"]}">{esc(_fmt_de(fmt.format(v)))}</text>'
            )

    draw(left_series, py_left, lfmt)
    draw(right_series, py_right, rfmt)

    # Legend
    lx, ly = ml, 24
    for s in (left_series + right_series):
        parts.append(f'<rect x="{lx}" y="{ly - 9}" width="14" height="4" rx="2" fill="{s["color"]}"/>')
        parts.append(f'<text x="{lx + 20}" y="{ly - 3}" font-size="11" fill="#555">{esc(s["label"])}</text>')
        lx += 26 + len(str(s["label"])) * 7

    parts.append("</svg>")
    return "".join(parts)


def _rings_from_geometry(geom):
    """Yield lists of [lon, lat] rings from a GeoJSON Polygon/MultiPolygon geometry."""
    if not geom:
        return
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return
    if gtype == "Polygon":
        for ring in coords:
            yield ring
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                yield ring


def _polygons_from_geometry(geom):
    """Yield polygons (each = list of rings; ring[0] outer, rest holes)."""
    if not geom:
        return
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return
    if gtype == "Polygon":
        yield coords
    elif gtype == "MultiPolygon":
        for poly in coords:
            yield poly


def _rgba(color):
    """[r,g,b,a(0-255)] -> css rgba() string."""
    try:
        r, g, b = int(color[0]), int(color[1]), int(color[2])
        a = (color[3] / 255.0) if len(color) > 3 else 1.0
        return f"rgba({r},{g},{b},{a:.2f})"
    except Exception:
        return "rgba(200,200,200,0.6)"


def _render_choropleth_svg(features, selected_name, metric_label, legend_lo=None, legend_hi=None):
    """Render a static inline-SVG choropleth from GeoJSON-like features.

    features: list of {"geometry": <geojson>, "properties": {"name","value","fill":[r,g,b,a]}}
    Equirectangular projection with cos(lat) aspect correction. No basemap tiles.
    """
    import math

    # Bounding box across all coordinates
    lons, lats = [], []
    for f in features:
        for ring in _rings_from_geometry(f.get("geometry")):
            for pt in ring:
                lons.append(pt[0])
                lats.append(pt[1])
    if not lons or not lats:
        return ""

    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)
    lat_mid = (lat_min + lat_max) / 2.0
    kx = math.cos(math.radians(lat_mid)) or 1e-6  # longitude compression

    W = 820
    pad = 12
    geo_w = (lon_max - lon_min) * kx or 1e-6
    geo_h = (lat_max - lat_min) or 1e-6
    plot_w = W - 2 * pad
    plot_h = plot_w * (geo_h / geo_w)
    H = plot_h + 2 * pad
    # Clamp height to something reasonable
    if H > 900:
        scale = 900 / H
        H, plot_h, plot_w = 900, plot_h * scale, plot_w * scale

    def px(lon):
        return pad + (lon - lon_min) * kx / geo_w * plot_w

    def py(lat):
        return pad + (lat_max - lat) / geo_h * plot_h  # invert y

    parts = [
        f'<svg viewBox="0 0 {W} {H:.0f}" preserveAspectRatio="xMidYMid meet" '
        f'style="width:100%;height:auto;font-family:Arial,Helvetica,sans-serif;background:#f7f9fb;">'
    ]

    sel_centroid = None

    def path_for_polygon(polygon):
        d = []
        for ring in polygon:  # ring[0] outer, rest holes (evenodd handles holes)
            if not ring:
                continue
            seg = [f"M{px(ring[0][0]):.1f},{py(ring[0][1]):.1f}"]
            for pt in ring[1:]:
                seg.append(f"L{px(pt[0]):.1f},{py(pt[1]):.1f}")
            seg.append("Z")
            d.append(" ".join(seg))
        return " ".join(d)

    # Draw non-selected first, selected last (so highlight sits on top)
    ordered = sorted(features, key=lambda f: f.get("properties", {}).get("name") == selected_name)
    for f in ordered:
        props = f.get("properties", {})
        name = props.get("name", "")
        fill = _rgba(props.get("fill"))
        is_sel = name == selected_name
        for polygon in _polygons_from_geometry(f.get("geometry")):
            d = path_for_polygon(polygon)
            if not d:
                continue
            stroke = "#ffcc00" if is_sel else "#7a7a7a"
            sw = 2.6 if is_sel else 0.5
            parts.append(
                f'<path d="{d}" fill="{fill}" fill-rule="evenodd" '
                f'stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"/>'
            )
        if is_sel:
            # centroid of outer ring points for label placement
            xs = [px(p[0]) for r in _rings_from_geometry(f.get("geometry")) for p in r]
            ys = [py(p[1]) for r in _rings_from_geometry(f.get("geometry")) for p in r]
            if xs and ys:
                sel_centroid = (sum(xs) / len(xs), sum(ys) / len(ys), name)

    # Selected label
    if sel_centroid:
        cx, cy, name = sel_centroid
        tw = 7 * len(name) + 12
        parts.append(
            f'<rect x="{cx - tw / 2:.1f}" y="{cy - 10:.1f}" width="{tw:.1f}" height="17" rx="3" '
            f'fill="#ffffff" stroke="#ffcc00" stroke-width="1.2" opacity="0.9"/>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="{cy + 2:.1f}" text-anchor="middle" font-size="11" '
            f'font-weight="700" fill="#333">{esc(name)}</text>'
        )

    # Legend (gradient bar) if numeric bounds supplied
    if legend_lo is not None and legend_hi is not None:
        parts.append(
            f'<text x="{pad}" y="{H - 8:.0f}" font-size="10" fill="#666">{esc(metric_label)}:'
            f' {esc(legend_lo)} \u2192 {esc(legend_hi)}</text>'
        )

    parts.append("</svg>")
    return "".join(parts)


_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { font-family: Arial, Helvetica, sans-serif; color:#333; background:#f5f5f5;
       margin:0; padding:24px; }
.report { max-width: 1100px; margin: 0 auto; background:#fff; border-radius:12px;
          padding: 28px 32px 40px; box-shadow: 0 2px 12px rgba(0,0,0,0.06); }
.rep-header { border-bottom: 3px solid #ffcc00; padding-bottom: 14px; margin-bottom: 6px; }
.rep-header h1 { margin: 0; font-size: 1.9rem; }
.rep-meta { color:#666; font-size: 0.86rem; margin-top: 6px; }
.rep-meta span { margin-right: 14px; }
h2.sec { font-size: 1.15rem; margin: 30px 0 10px; padding-top: 6px; border-top: 1px solid #eee; }
h3.subsec { font-size: 0.95rem; margin: 16px 0 6px; color:#555; }
.kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin: 8px 0; }
.kpi-card { background:#fff; border-radius:10px; padding:16px; border-left:4px solid #ffcc00;
            box-shadow:0 1px 3px rgba(0,0,0,0.06); }
.kpi-card .kpi-value { font-size:1.5rem; font-weight:800; color:#333; }
.kpi-card .kpi-label { font-size:0.82rem; color:#666; margin-top:3px; }
.kpi-card .kpi-sub { font-size:0.75rem; color:#999; }
.var-positive { color:#1db954; font-weight:600; }
.var-negative { color:#e74c3c; font-weight:600; }
.chart-wrap { border:1px solid #eee; border-radius:10px; padding:10px 12px; margin:10px 0; background:#fff; }
.chart-title { font-size:0.9rem; font-weight:600; color:#555; margin-bottom:4px; }
.price-list { max-width: 520px; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
table th, table td { border-bottom: 1px solid #eee; padding: 6px 10px; text-align: right; }
table th:first-child, table td:first-child { text-align: left; }
table thead th { background:#fafafa; color:#666; font-weight:600; }
.rep-footer { margin-top: 34px; padding-top: 14px; border-top: 1px solid #eee;
              color:#999; font-size: 0.75rem; line-height: 1.5; }
"""


class ReportBuilder:
    def __init__(self, place_name: str, market_label: str):
        self.place_name = place_name
        self.market_label = market_label
        self.meta = {}
        self._blocks = []          # list of tuples describing ordered content
        self._pending_cards = []   # KPI cards awaiting flush into a grid

    # -- meta ---------------------------------------------------------------
    def set_meta(self, **kwargs):
        self.meta.update({k: v for k, v in kwargs.items() if v is not None})

    # -- card capture -------------------------------------------------------
    def card(self, html: str):
        """Record a KPI card's HTML. Called from the app's kpi_card()."""
        if html:
            self._pending_cards.append(html)

    def _flush(self):
        if self._pending_cards:
            self._blocks.append(("cards", tuple(self._pending_cards)))
            self._pending_cards = []

    # -- structural blocks --------------------------------------------------
    def heading(self, title: str):
        self._flush()
        self._blocks.append(("h2", title))

    def subheading(self, title: str):
        self._flush()
        self._blocks.append(("h3", title))

    def add_table(self, title: str, html: str):
        self._flush()
        self._blocks.append(("table", title, html or ""))

    def add_ranking(self, title: str, html: str):
        self._flush()
        self._blocks.append(("ranking", title, html or ""))

    def add_choropleth(self, title, features, selected_name=None, metric_label="",
                       legend_lo=None, legend_hi=None):
        """Add a static SVG choropleth map from GeoJSON-like features."""
        if not features:
            return
        try:
            svg = _render_choropleth_svg(features, selected_name, metric_label, legend_lo, legend_hi)
        except Exception:
            return
        if not svg:
            return
        self._flush()
        self._blocks.append(("chart", title, svg))

    def add_time_series(self, title, df, x_col, primary, secondary=None,
                        primary_label="", secondary_label="", fmt="{:,.0f}", fmt2=None):
        """Add a line chart. primary/secondary: list of (col, label, color)."""
        if df is None or len(df) == 0:
            return
        d = df.copy()
        d[x_col] = pd.to_datetime(d[x_col])
        d = d.dropna(subset=[x_col]).sort_values(x_col)
        if d.empty:
            return
        xs = list(d[x_col])

        def build(series_defs):
            out = []
            for col, label, color in series_defs:
                if col not in d.columns:
                    continue
                pts = [(ts, float(v)) for ts, v in zip(d[x_col], d[col]) if pd.notna(v)]
                if pts:
                    out.append({"label": label, "color": color, "points": pts})
            return out

        left = build(primary)
        right = build(secondary) if secondary else []
        if not left and not right:
            return
        svg = _render_line_svg(xs, left, right, primary_label, secondary_label, fmt, fmt2 or fmt)
        self._flush()
        self._blocks.append(("chart", title, svg))

    # -- render -------------------------------------------------------------
    def to_html(self) -> str:
        self._flush()
        body = [
            '<div class="report">',
            '<div class="rep-header">',
            f"<h1>{esc(self.place_name)}</h1>",
        ]
        meta_bits = [f"<span><b>Market:</b> {esc(self.market_label)}</span>"]
        if self.meta.get("as_of"):
            meta_bits.append(f"<span><b>Data as of:</b> {esc(self.meta['as_of'])}</span>")
        if self.meta.get("growth"):
            meta_bits.append(f"<span><b>Growth period:</b> {esc(self.meta['growth'])}</span>")
        if self.meta.get("household"):
            realtor = " incl. realtor" if self.meta.get("realtor") else ""
            meta_bits.append(f"<span><b>Household:</b> {esc(self.meta['household'])}{realtor}</span>")
        gen = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        meta_bits.append(f"<span><b>Generated:</b> {gen}</span>")
        body.append(f'<div class="rep-meta">{"".join(meta_bits)}</div>')
        body.append("</div>")  # header

        for block in self._blocks:
            kind = block[0]
            if kind == "h2":
                body.append(f'<h2 class="sec">{esc(block[1])}</h2>')
            elif kind == "h3":
                body.append(f'<h3 class="subsec">{esc(block[1])}</h3>')
            elif kind == "cards":
                body.append('<div class="kpi-grid">' + "".join(block[1]) + "</div>")
            elif kind == "table":
                body.append(f'<div class="chart-title">{esc(block[1])}</div>')
                body.append(block[2])
            elif kind == "ranking":
                body.append(f'<div class="chart-title">{esc(block[1])}</div>')
                body.append(f'<div class="price-list">{block[2]}</div>')
            elif kind == "chart":
                body.append('<div class="chart-wrap">')
                body.append(f'<div class="chart-title">{esc(block[1])}</div>')
                body.append(block[2])
                body.append("</div>")

        body.append(
            '<div class="rep-footer">'
            "This report was exported from the Immowelt Market Insights dashboard. "
            "It is fully self-contained \u2014 no internet connection or Snowflake access is required to view it. "
            "Charts are static snapshots of the data at export time. "
            "Figures reflect platform demand/supply signals and estimated prices, not verified transactions."
            "</div>"
        )
        body.append("</div>")  # report

        return (
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{esc(self.place_name)} \u2014 Market Insights</title>"
            f"<style>{_CSS}</style></head><body>"
            + "".join(body)
            + "</body></html>"
        )

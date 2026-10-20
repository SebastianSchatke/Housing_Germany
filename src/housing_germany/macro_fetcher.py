"""
Macro-level data fetchers for the German housing market.

- ECB: Monthly house-purchase loan volumes (SDMX REST API)
- GENESIS/Destatis: Monthly building permits (REST POST API)
- Bundesagentur fuer Arbeit: Median income by region (Excel scrape)
"""
from __future__ import annotations

import datetime as _dt
import io
import logging
import os
import re
import zipfile
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ECB – Loan volumes for house purchase (Germany, monthly)
# ---------------------------------------------------------------------------
_ECB_API = "https://data-api.ecb.europa.eu/service/data"
_LOAN_SERIES_KEY = "M.DE.B.A2C.A.B.A.2250.EUR.P"


def fetch_loan_volumes(
    start_period: str = "2017-01",
    end_period: str | None = None,
    series_key: str = _LOAN_SERIES_KEY,
) -> pd.DataFrame:
    """
    Fetch monthly house-purchase loan volumes for Germany from the ECB.

    Uses the ECB SDMX 2.1 REST API (no authentication required).
    Series MIR.M.DE.B.A2C.A.B.A.2250.EUR.P = pure new loans for house
    purchase, deposit-taking corporations, euro, business volume.

    Args:
        start_period: First month to retrieve (YYYY-MM).
        end_period:   Last month to retrieve (YYYY-MM), or None for latest.
        series_key:   SDMX series key within the MIR dataflow.

    Returns:
        DataFrame with columns ``period`` (datetime) and
        ``loan_volume_mio_eur`` (float).
    """
    url = f"{_ECB_API}/MIR/{series_key}"
    params: dict[str, str] = {
        "startPeriod": start_period,
        "format": "csvdata",
    }
    if end_period:
        params["endPeriod"] = end_period

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    df = pd.read_csv(io.StringIO(response.text))
    df = df[["TIME_PERIOD", "OBS_VALUE"]].rename(columns={
        "TIME_PERIOD": "period",
        "OBS_VALUE": "loan_volume_mio_eur",
    })
    df["period"] = pd.to_datetime(df["period"], format="%Y-%m")
    df["loan_volume_mio_eur"] = pd.to_numeric(
        df["loan_volume_mio_eur"], errors="coerce",
    )
    logger.info("ECB: fetched %d rows (%s to %s)", len(df),
                df["period"].min(), df["period"].max())
    return df


# ---------------------------------------------------------------------------
# GENESIS / Destatis – Building permits (Germany, monthly)
# ---------------------------------------------------------------------------
_GENESIS_API = "https://genesis.destatis.de/genesisWS/rest/2020"


def fetch_building_permits(
    table: str = "31111-0020",
    start_year: int = 2017,
    end_year: int | None = None,
    token: str | None = None,
) -> pd.DataFrame:
    """
    Fetch monthly building permit data from GENESIS/Destatis.

    Uses the GENESIS REST API (POST, requires a personal API token).
    Table 31111-0020 = Baugenehmigungen im Hochbau: Deutschland, Monate,
    Bautätigkeiten, Gebäudeart.

    The table is large, so data is fetched one year at a time and
    concatenated (per Destatis recommendation for large tables).

    Args:
        table:      GENESIS table code.
        start_year: First year to retrieve.
        end_year:   Last year to retrieve (None = current year).
        token:      GENESIS personal API token (defaults to env GENESIS_TOKEN).

    Returns:
        DataFrame with columns ``period`` (datetime) and
        ``wohnungen`` (int, total residential dwelling units approved).
        Filtered to new construction only.
    """
    import datetime

    token = token or os.environ["GENESIS_TOKEN"]
    if end_year is None:
        end_year = datetime.date.today().year

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "username": token,
        "password": "",
    }

    frames = []
    for year in range(start_year, end_year + 1):
        logger.info("GENESIS: fetching year %d ...", year)
        response = requests.post(
            f"{_GENESIS_API}/data/tablefile",
            headers=headers,
            data={
                "name": table,
                "startyear": str(year),
                "endyear": str(year),
                "compress": "true",
                "format": "ffcsv",
                "language": "de",
            },
            timeout=60,
        )
        if response.status_code == 401:
            raise PermissionError(
                "GENESIS API returned 401 (Unauthorized). Verify your "
                "API token at genesis.destatis.de (Webservice-Schnittstelle)."
            )
        response.raise_for_status()

        # Check for JSON error response (table too large, etc.)
        ct = response.headers.get("Content-Type", "")
        if "json" in ct:
            import json
            body = json.loads(response.text)
            code = body.get("Status", {}).get("Code")
            if code == 98:
                logger.warning("Year %d too large even as single year, skipping", year)
                continue
            msg = body.get("Status", {}).get("Content", "Unknown error")
            raise RuntimeError(f"GENESIS API error (code {code}): {msg}")

        content = _unzip_genesis_response(response)
        chunk = _parse_genesis_ffcsv(content)
        frames.append(chunk)

    if not frames:
        return pd.DataFrame(columns=["period", "activity_type", "building_type", "value"])

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values("period").reset_index(drop=True)

    # Filter to apartment/house permit counts only
    df = _filter_residential_permits(df)

    logger.info("GENESIS: fetched %d rows total (%s to %s)", len(df),
                df["period"].min(), df["period"].max())
    return df


def _filter_residential_permits(df: pd.DataFrame) -> pd.DataFrame:
    """Keep total new-construction dwelling-unit permits (Wohnungen) per month.

    Filters to: activity = new construction, building_type = Wohngebäude
    (total residential), metric = Wohnungen (dwelling units).
    """
    mask = pd.Series(True, index=df.index)
    if "activity_type" in df.columns:
        mask &= df["activity_type"] == "Errichtung neuer Gebäude"
    if "building_type" in df.columns:
        mask &= df["building_type"] == "Wohngebäude"
    if "metric" in df.columns:
        mask &= df["metric"] == "Wohnungen"

    result = df.loc[mask, ["period", "value"]].copy()
    result = result.rename(columns={"value": "wohnungen"})
    return result.reset_index(drop=True)


def _unzip_genesis_response(response: requests.Response) -> str:
    """Extract the single CSV file from a zipped GENESIS response."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(response.content))
        csv_file = zf.open(zf.namelist()[0])
        return csv_file.read().decode("utf-8")
    except zipfile.BadZipFile:
        # Response was not zipped (small tables or error JSON)
        return response.text


def _parse_genesis_ffcsv(text: str) -> pd.DataFrame:
    """Parse a GENESIS flat-file CSV response into a tidy DataFrame.

    The ffcsv format (table 31111-0020) has columns like:
        time (year), 1_variable_attribute_label (month),
        4_variable_attribute_label (activity), 5_variable_attribute_label
        (building type), value, value_variable_label (metric name).
    """
    df = pd.read_csv(
        io.StringIO(text),
        sep=";",
        decimal=",",
        na_values=["...", ".", "-", "/", "x"],
    )
    df.columns = [c.strip() for c in df.columns]

    # Build period from year ("time") + month name ("1_variable_attribute_label")
    month_col = _find_column(df, ["1_variable_attribute_label"])
    year_col = _find_column(df, ["time"])

    if year_col and month_col:
        df["period"] = (
            df[year_col].astype(str) + " " + df[month_col].astype(str)
        ).apply(_parse_genesis_period)
    else:
        # Fallback: try "time" or "Zeit" column directly
        time_col = _find_column(df, ["time", "Zeit"])
        if time_col is None:
            raise ValueError(
                f"Cannot find time column. Columns: {list(df.columns)}"
            )
        df["period"] = df[time_col].apply(_parse_genesis_period)

    df = df.dropna(subset=["period"])

    # Map numbered variable labels to friendly names
    # For table 31111-0020:
    #   4_variable_attribute_label = activity type (Neubau, etc.)
    #   5_variable_attribute_label = building type (Wohngebäude, etc.)
    col_map = {
        "4_variable_attribute_label": "activity_type",
        "5_variable_attribute_label": "building_type",
    }
    keep_cols = ["period"]
    rename = {}
    for src, dst in col_map.items():
        found = _find_column(df, [src])
        if found:
            keep_cols.append(found)
            rename[found] = dst

    # Value and its unit/label
    value_col = _find_column(df, ["value"])
    value_label_col = _find_column(df, ["value_variable_label"])
    if value_col:
        keep_cols.append(value_col)
    if value_label_col:
        keep_cols.append(value_label_col)
        rename[value_label_col] = "metric"

    result = df[keep_cols].copy().rename(columns=rename)
    return result.reset_index(drop=True)


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first column name from *candidates* that exists in *df*.

    Tries exact match first, then falls back to substring matching.
    """
    # Exact match (case-insensitive)
    for c in candidates:
        for col in df.columns:
            if c.lower() == col.lower():
                return col
    # Substring match
    for c in candidates:
        for col in df.columns:
            if c.lower() in col.lower():
                return col
    return None


def _is_numeric_column(series: pd.Series, threshold: float = 0.5) -> bool:
    """Heuristic: does more than *threshold* of non-empty values look numeric?"""
    non_empty = series.str.strip().replace("", pd.NA).dropna()
    if len(non_empty) == 0:
        return False
    numeric = pd.to_numeric(
        non_empty.str.replace(",", "."), errors="coerce",
    ).notna().sum()
    return (numeric / len(non_empty)) > threshold


def _parse_genesis_period(value) -> pd.Timestamp | None:
    """Convert GENESIS period strings to a Timestamp.

    Handles: '2017M01', '2017 Januar', '2024 Dezember', etc.
    """
    if pd.isna(value):
        return None
    value = str(value).strip()

    # "2017M01" format
    if "M" in value.upper():
        parts = value.upper().split("M")
        if len(parts) == 2:
            try:
                return pd.Timestamp(year=int(parts[0]), month=int(parts[1]), day=1)
            except (ValueError, TypeError):
                pass

    # "2017 Januar" / "2020 Februar" format (German month names)
    _GERMAN_MONTHS = {
        "januar": 1, "februar": 2, "märz": 3, "april": 4,
        "mai": 5, "juni": 6, "juli": 7, "august": 8,
        "september": 9, "oktober": 10, "november": 11, "dezember": 12,
    }
    parts = value.split()
    if len(parts) == 2:
        year_str, month_str = parts
        month_num = _GERMAN_MONTHS.get(month_str.lower())
        if month_num and year_str.isdigit():
            return pd.Timestamp(year=int(year_str), month=month_num, day=1)

    # Fallback to pandas parser
    try:
        return pd.Timestamp(value)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Bundesagentur fuer Arbeit – Median income by region
# ---------------------------------------------------------------------------
_BA_SEARCH_URL = (
    "https://statistik.arbeitsagentur.de/SiteGlobals/Forms/Suche/"
    "Einzelheftsuche_Formular.html?nn=1523076&topic_f=beschaeftigung-entgelt-entgelt"
)
_BA_BASE = "https://statistik.arbeitsagentur.de"


def fetch_income() -> tuple[pd.DataFrame, str]:
    """Fetch median net income by region from the Bundesagentur fuer Arbeit.

    Scrapes the BA publication index for the latest Excel file, downloads it
    into memory (no temp file), and parses sheet "8.2" (Medianentgelt by
    Kreise).  Year columns are detected dynamically so the latest year is
    never truncated.

    Returns:
        Tuple of (DataFrame with columns cog, place, sort, and one column
        per reporting year; latest_year string e.g. "2025").
    """
    # 1. Find the latest Excel download URL
    resp = requests.get(_BA_SEARCH_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.content, "html.parser")
    excel_span = soup.find("span", class_="doctypeLabel", title="Format: Excel")
    if excel_span is None:
        raise RuntimeError("No Excel link found on the BA publications page")
    href = excel_span.find_parent("a")["href"]
    excel_url = f"{_BA_BASE}{href}"
    logger.info("BA income: downloading %s", excel_url)

    # 2. Download Excel into memory
    resp = requests.get(excel_url, timeout=60)
    resp.raise_for_status()
    excel_bytes = io.BytesIO(resp.content)

    # 3. Parse sheet 8.2
    df = pd.read_excel(excel_bytes, sheet_name="8.2", skiprows=8)

    # Identify columns: first 3 are meta (cog, place, sort), then datetime
    # year columns, then junk (Filteroptionen etc.)
    meta_cols = df.columns[:3].tolist()
    year_cols = [c for c in df.columns if isinstance(c, _dt.datetime)]
    if not year_cols:
        raise ValueError("No year columns found in sheet 8.2")

    df = df[meta_cols + year_cols]
    df = df.rename(columns={
        meta_cols[0]: "cog",
        meta_cols[1]: "place",
        meta_cols[2]: "sort",
    })

    # 4. Filter rows: only "Insgesamt", drop West/Ost aggregates
    df = df[df["sort"] == "Insgesamt"]
    df = df[~df["cog"].isin(["W", "O"])]

    # 5. Normalize cog codes
    df["cog"] = df["cog"].astype(str).str.strip()
    df.loc[df["cog"] == "D", "cog"] = "de"
    df["cog"] = df["cog"].apply(_normalize_cog)

    # 6. Rename datetime columns to reporting year strings
    #    Excel header 2024-12-31 represents reference date -> report year 2024+1 = 2025
    col_rename = {c: str(c.year + 1) for c in year_cols}
    df = df.rename(columns=col_rename)

    # 7. Derive the publication year from the URL (e.g. /Detail/202512/ -> "2025")
    m = re.search(r"/Detail/(\d{4})", href)
    latest_year = m.group(1) if m else str(max(c.year + 1 for c in year_cols))

    logger.info("BA income: %d regions, years %s, latest=%s",
                len(df), [col_rename[c] for c in year_cols], latest_year)
    return df, latest_year


def _normalize_cog(val: str) -> str:
    """Zero-pad admin codes: states to 2 digits, districts to 5."""
    if val == "de":
        return val
    if val.isdigit():
        return val.zfill(2) if len(val) <= 2 else val.zfill(5)
    return val


# Backward-compatible aliases
income_link_DE_task = None  # removed – URL discovery is internal to fetch_income
income_raw_DE_task = fetch_income


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def save_to_parquet(df: pd.DataFrame, path: str | Path) -> Path:
    """Save a DataFrame to a Parquet file, creating parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    logger.info("Saved %d rows to %s", len(df), path)
    return path


def save_to_csv(df: pd.DataFrame, path: str | Path) -> Path:
    """Save a DataFrame to a CSV file, creating parent directories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    logger.info("Saved %d rows to %s", len(df), path)
    return path


_SF_SCHEMA = "HOUSING_DEV.MARKET_INSIGHTS_STREAMLIT_APPS"


def load_loan_volumes_to_snowflake(
    session,
    start_period: str = "2017-01",
    table: str = f"{_SF_SCHEMA}.ECB_LOAN_VOLUMES",
) -> int:
    """Fetch ECB loan volumes and load into a Snowflake table.

    Returns the number of rows loaded.
    """
    df = fetch_loan_volumes(start_period=start_period)
    df["period"] = df["period"].dt.strftime("%Y-%m-%d")

    session.sql(f"DELETE FROM {table}").collect()

    rows = []
    for _, r in df.iterrows():
        rows.append(f"('{r['period']}', {r['loan_volume_mio_eur']})")

    for i in range(0, len(rows), 50):
        batch = rows[i : i + 50]
        sql = f"""INSERT INTO {table}
            (PERIOD, LOAN_VOLUME_MIO_EUR) VALUES {','.join(batch)}"""
        session.sql(sql).collect()

    print(f"Loaded {len(rows)} rows into {table}")
    return len(rows)


def load_building_permits_to_snowflake(
    session,
    start_year: str = "2017",
    table: str = f"{_SF_SCHEMA}.BUILDING_PERMITS",
) -> int:
    """Fetch GENESIS building permits and load into a Snowflake table.

    Returns the number of rows loaded.
    """
    df = fetch_building_permits(start_year=start_year)
    df["period"] = df["period"].dt.strftime("%Y-%m-%d")

    session.sql(f"DELETE FROM {table}").collect()

    # Build value columns dynamically from the DataFrame
    value_cols = [c for c in df.columns
                  if c != "period" and df[c].dtype in ("float64", "int64")]
    dim_cols = [c for c in df.columns
                if c not in ("period",) and c not in value_cols]
    all_cols = ["period"] + dim_cols + value_cols
    sf_cols = [c.upper() for c in all_cols]

    rows = []
    for _, r in df.iterrows():
        vals = []
        for c in all_cols:
            v = r[c]
            if pd.isna(v):
                vals.append("NULL")
            elif isinstance(v, (int, float)):
                vals.append(str(v))
            else:
                escaped = str(v).replace("'", "''")
                vals.append(f"'{escaped}'")
        rows.append(f"({','.join(vals)})")

    for i in range(0, len(rows), 50):
        batch = rows[i : i + 50]
        sql = f"""INSERT INTO {table}
            ({','.join(sf_cols)}) VALUES {','.join(batch)}"""
        session.sql(sql).collect()

    print(f"Loaded {len(rows)} rows into {table}")
    return len(rows)

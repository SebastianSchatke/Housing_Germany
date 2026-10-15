"""
German income data scraping and processing.

Fetches median net income data from the Bundesagentur fuer Arbeit website
and matches it to geo IDs via administrative codes.
"""
from __future__ import annotations

import re
import pandas as pd
import requests
from bs4 import BeautifulSoup


def income_link_DE_task() -> str | None:
    """Scrape the Bundesagentur website for the latest income Excel download URL."""
    url = "https://statistik.arbeitsagentur.de/SiteGlobals/Forms/Suche/Einzelheftsuche_Formular.html?nn=1523076&topic_f=beschaeftigung-entgelt-entgelt"
    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'html.parser')
    excel_links = soup.find_all('span', class_='doctypeLabel', title='Format: Excel')
    if excel_links:
        first_excel_url = excel_links[0].find_parent('a')['href']
        print("URL of the first Excel file:", first_excel_url)
        return first_excel_url
    print("No Excel files found on the webpage.")
    return None


def income_raw_DE_task() -> tuple[pd.DataFrame, str]:
    """Download and parse the income Excel file into a cleaned DataFrame."""
    first_excel_url = income_link_DE_task()
    excel_url = f"https://statistik.arbeitsagentur.de{first_excel_url}"

    response = requests.get(excel_url)
    with open("data.xlsx", "wb") as f:
        f.write(response.content)

    income_raw = (
        pd.read_excel("data.xlsx", sheet_name="8.2", skiprows=8)
        .iloc[:, :7]
        .rename(columns={"Unnamed: 0": "cog", "Unnamed: 1": "place", "Unnamed: 2": "sort"})
    )

    # Keep only Insgesamt rows, drop West/Ost aggregates
    income_raw = income_raw[income_raw["sort"] == "Insgesamt"]
    income_raw = income_raw[~income_raw["cog"].isin(["W", "O"])]

    # Normalize cog: Deutschland -> 'de', everything else zero-pad
    income_raw["cog"] = income_raw["cog"].astype(str).str.strip()
    income_raw.loc[income_raw["cog"] == "D", "cog"] = "de"

    def normalize_cog(val):
        if val == "de":
            return val
        if val.isdigit():
            return val.zfill(2) if len(val) <= 2 else val.zfill(5)
        return val

    income_raw["cog"] = income_raw["cog"].apply(normalize_cog)

    # Rename year columns: Excel serial dates -> actual year + 1
    income_raw.columns = [str(col).split()[0] for col in income_raw.columns]
    year_cols = {
        col: str(int(col[:4]) + 1)
        for col in income_raw.columns
        if col not in ["cog", "place", "sort"] and col[:4].isdigit()
    }
    income_raw = income_raw.rename(columns=year_cols)

    pattern = r'/Detail/(\d{4})'
    year = re.search(pattern, first_excel_url).group(1)

    return income_raw, year


def get_income_DE_common(conn, geotype=None) -> tuple[pd.DataFrame, str]:
    """
    Get income data matched to geo IDs.

    Args:
        conn: database connection (psycopg2)
        geotype: 'AD02DE' (country), 'AD04DE' (Bundeslaender),
                 'AD06DE' (Landkreise/Stadtkreise), or None (all levels)

    Returns:
        Tuple of (income DataFrame with geo IDs, year string)
    """
    income_raw, year = income_raw_DE_task()

    key_map = {
        'AD02DE': ("'AD02DE'", None),
        'AD04DE': ("'AD04DE'", 2),
        'AD06DE': ("'AD06DE'", 5),
        None:     ("'AD02DE', 'AD04DE', 'AD06DE'", None),
    }
    key_filter_str, _ = key_map[geotype]

    sql = f"""
        SELECT id, key, administrative_code, type, display_name
        FROM miracle.feature
        WHERE key IN ({key_filter_str})
        AND active = True
    """
    geo = pd.read_sql(sql, conn)

    # Normalize administrative_code to match cog format
    geo["administrative_code"] = geo["administrative_code"].astype(str).str.strip()
    geo.loc[geo["key"] == "AD04DE", "administrative_code"] = (
        geo.loc[geo["key"] == "AD04DE", "administrative_code"].str.zfill(2)
    )
    geo.loc[geo["key"] == "AD06DE", "administrative_code"] = (
        geo.loc[geo["key"] == "AD06DE", "administrative_code"].str.zfill(5)
    )

    # Primary join: administrative_code = cog
    merged = geo.merge(
        income_raw, left_on="administrative_code", right_on="cog",
        how="left", indicator=True
    )
    matched = merged[merged["_merge"] == "both"].drop(columns=["_merge"])

    # Fallback join for unmatched: display_name = place
    unmatched_geo = (
        merged[merged["_merge"] == "left_only"]
        .drop(columns=["_merge"] + [c for c in income_raw.columns if c in merged.columns and c != "display_name"])
    )

    if not unmatched_geo.empty:
        unmatched_geo["display_name_clean"] = (
            unmatched_geo["display_name"].str.strip().str.rstrip(",").str.strip()
        )
        income_raw["place_clean"] = income_raw["place"].str.strip()
        fallback = unmatched_geo.merge(
            income_raw, left_on="display_name_clean", right_on="place_clean",
            how="inner"
        )
        if not fallback.empty:
            fallback = fallback.drop(columns=["display_name_clean", "place_clean"], errors="ignore")
            matched = pd.concat([matched, fallback], ignore_index=True)

    return matched, year

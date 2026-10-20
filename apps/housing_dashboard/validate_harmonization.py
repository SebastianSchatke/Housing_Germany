"""
Validate market_insights.py against the original iwt_barometer math.

We cannot reach the Postgres backend the originals query (miracle.feature,
clustered_indice.indice_long, price.sandbox_price). So this harness proves
harmonization accuracy the rigorous way instead: it defines the ORIGINAL
pure-math primitives verbatim (copied from indice_functions.py /
indices_aggregation.py) and asserts the ported market_insights functions
produce identical results on identical inputs -- including the real
AD02DE1 house+apartment SELL series pulled from Snowflake.

Run: python3 validate_harmonization.py
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import market_insights as mi

# ---------------------------------------------------------------------------
# ORIGINAL primitives, copied verbatim from the uploaded iwt_barometer files.
# ---------------------------------------------------------------------------

def calcul_evol_from_series(series):                      # indice_functions.py:108
    return series.pct_change().fillna(0)


def calcul_rebase(series):                                # indice_functions.py:121
    list_rebase = [100]
    for i in range(1, len(series)):
        rebase = list_rebase[i - 1] * (1 + series.iloc[i])
        list_rebase.append(rebase)
    return list_rebase


def find_first_date_rebase(list_of_list_of_dates):        # indice_functions.py:112
    if len(list_of_list_of_dates) == 0 or all(not l for l in list_of_list_of_dates):
        return None
    valid_dates = [lst[0] for lst in list_of_list_of_dates if lst]
    if not valid_dates:
        return None
    return max(valid_dates).strftime('%Y-%m-%d')


def aggregate_rebase_indices_original(df):
    """Core of aggregate_rebase_indices (indices_aggregation.py:147) for one geo."""
    unique_dates = pd.to_datetime(df['rebased_dates'].explode().unique()).sort_values()
    hyb = pd.Series(0, index=unique_dates)
    nb_log = 0
    for _, row in df.iterrows():
        if not row.rebased_indices:
            continue
        nb_log += row.log_count
        hyb = hyb.add(
            row.log_count * pd.Series(row.rebased_indices, index=pd.to_datetime(row.rebased_dates)),
            fill_value=0,
        )
    if nb_log > 0:
        hyb = hyb / nb_log
    hyb.iloc[0] = 100
    return hyb


def rebase_row_original(values, dates, cut_date):
    """Single-row logic from rebase_indices_common (indices_aggregation.py:62)."""
    indice = pd.Series(values, index=pd.to_datetime(dates)).to_frame().rename(columns={0: "indice"})
    cut = pd.to_datetime(cut_date)
    if cut < indice.index.min():
        cut = indice.index.min()
    indice = indice[indice.index >= cut]
    evol = calcul_evol_from_series(indice['indice'])
    return pd.Series(calcul_rebase(evol), index=indice.index)


# ---------------------------------------------------------------------------
# Real fixture: AD02DE1 SELL, pulled from SILVER_PROD.MKKN_INDICE.INDICE_DE.
# ---------------------------------------------------------------------------

DATES = [d.strftime('%Y-%m-%d') for d in pd.date_range('2017-01-01', periods=114, freq='MS')]

HOUSE = [100,100.5216302027262,101.1392690290352,101.6618477798699,102.3871480258425,103.132566873613,103.8951733631884,104.5527657821267,105.170933185631,105.7221947274563,106.2092542029551,106.7310166695509,107.3759276209152,108.1212745668227,108.922591417296,109.6130546354145,110.3880399217975,111.1160722281766,111.7520466969427,112.3376182187262,112.9677666363078,113.7075903311753,114.4752167068291,115.2582194011216,116.0872012791068,116.9564979987536,117.9151976749606,118.7729053078066,119.7211214867317,120.6242519777638,121.4695847154239,122.2903454486317,123.0475326171923,123.8707944511384,124.7114946471487,125.7389402628427,126.7932621343054,127.9086941187066,128.9832383107312,130.0391098940366,131.2086591816047,132.4503706318328,133.643092556905,135.0298616966957,136.452511981478,137.9569029380463,139.4425682650019,140.9729617580629,142.5843891098144,144.2871296331334,146.1018557697463,147.8686161240752,149.8635326346327,151.9009414057929,153.8413344864028,155.6063642952523,157.2413704808621,158.8536368300799,160.4341361405221,161.9410702381456,163.3480691342734,164.520695898929,165.4835620473313,166.0380667812844,166.2672258539206,166.1954936987815,165.8292622074916,164.6919316610635,163.3643149758996,161.9451279260622,160.6422304725967,159.6050538549103,158.8495907873165,158.0741030609106,157.2728251150555,156.2891262492387,155.3193739427408,154.2892173599917,153.2361718703998,152.3987194249764,151.6531220620771,150.8754672433477,150.3129710639576,150.2144564818595,150.3577262393383,150.6643829854673,151.1791834313997,151.7207605945055,152.2740024169796,152.7408923156093,153.0922727467886,153.2358290530832,153.3293736072257,153.461820605716,153.669197272847,153.9346446935376,154.2341331390731,154.4402868585542,154.5906994993844,154.7787642974036,155.1718903381051,155.6673410511634,156.0673540750276,156.3065594566564,156.4885809989618,156.7356390510803,157.1180318417861,157.5484269479939,158.0252077650157,158.4381092181203,158.9124348854562,159.4970322801598,160.0710662006365,160.524520332011]

APARTMENT = [100,100.4057028312153,101.0062995339333,101.6614878606359,102.529268207043,103.4046579150031,104.1612295465374,104.8215727039345,105.4143326478712,106.0322109739632,106.6534875033944,107.3550164687707,108.1279930099066,108.9521392464928,109.7686330948064,110.4518101821278,111.0743932113351,111.6196359855041,112.2356041984955,113.0069360295751,113.9561712761978,114.9114388684593,115.6038134728878,116.3326682582316,117.1517211539923,118.042537972232,119.0455382972523,120.0496546397754,121.1477999471559,122.1801167288163,123.0817915068578,123.9106983581551,124.802527712466,125.8123913348927,126.8799533505307,127.9657950572888,129.0962793726898,130.2980118444369,131.5410455180049,132.6886217676017,133.7616685752656,134.7134518495334,135.5781036128648,136.858005991269,138.2041682832056,139.59368081667,140.9946971349285,142.4552207855028,144.0937345753046,145.8774028923519,147.7312995227797,149.4257255876331,151.1453097514494,152.8642170251439,154.5385952713421,156.1184159620738,157.5684559356396,159.0398705464353,160.4643513277883,161.8710885865619,163.1338319081291,164.2131552677161,165.1273308277345,165.7467117158198,165.7865751683531,165.4432381734012,164.8164562542961,163.3733144004775,161.798873940562,160.1917869067708,158.6932268214431,157.3054556447444,156.1041332743532,155.0724416359265,153.9618938102219,152.9772379522977,152.1792204100369,151.17110940963,149.9368554342127,148.6308560033621,147.969573598884,147.6476901374734,147.5181377561456,147.6927998921958,147.9833223962274,148.6998295624675,149.0554702864803,149.4984543375182,149.8676358989572,150.2209463365992,150.5265055581381,150.6915884159664,150.9895370162465,151.326876017817,151.648097267422,152.0667545514239,152.6001405504351,153.1466302227519,153.6199226068366,153.9988287848885,154.446771512129,154.8909543549925,155.3395305087179,155.6293300031809,155.8011259046267,156.1038599253632,156.4591234776512,157.0097989115702,157.6012438874412,158.1669295819154,158.6999048737595,159.2657876832085,159.7273631843263,160.263459204286]

VOL_HOUSE = 40223
VOL_APARTMENT = 24776


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _check(name, a, b, tol=1e-9):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.shape != b.shape:
        print(f"FAIL {name}: shape {a.shape} vs {b.shape}")
        return False
    max_diff = np.nanmax(np.abs(a - b)) if a.size else 0.0
    ok = max_diff <= tol
    print(f"{'PASS' if ok else 'FAIL'} {name}: max abs diff = {max_diff:.3e}")
    return ok


def main():
    results = []
    house = pd.Series(HOUSE, index=pd.to_datetime(DATES))
    apt = pd.Series(APARTMENT, index=pd.to_datetime(DATES))

    # 1. Evolution
    results.append(_check("pct_evolution vs calcul_evol_from_series (house)",
                          mi.pct_evolution(house).values, calcul_evol_from_series(house).values))

    # 2. Rebase to 100
    evol = calcul_evol_from_series(house)
    results.append(_check("rebase_to_100 vs calcul_rebase (house)",
                          mi.rebase_to_100(evol), calcul_rebase(evol)))

    # 3. Common start date
    dates_obj = [pd.to_datetime(d).date() for d in DATES]
    orig_start = find_first_date_rebase([dates_obj, dates_obj[6:]])
    port_start = mi.common_start_date([DATES, DATES[6:]])
    match3 = pd.to_datetime(orig_start) == pd.to_datetime(port_start)
    print(f"{'PASS' if match3 else 'FAIL'} common_start_date vs find_first_date_rebase: "
          f"{port_start} vs {orig_start}")
    results.append(match3)

    # 4. Single-row rebase (custom ref date mid-series)
    ref = "2020-01-01"
    orig_row = rebase_row_original(HOUSE, DATES, ref)
    port_row = mi.rebase_series(DATES, HOUSE, ref_date=ref)
    results.append(_check(f"rebase_series vs rebase_indices_common (ref={ref})",
                          port_row.values, orig_row.values))

    # 5. Volume-weighted hybrid: original aggregate vs ported volume_weighted_index
    #    Rebase both at common start (2017-01-01), weight by volume.
    df_orig = pd.DataFrame([
        {"rebased_dates": [pd.to_datetime(d).date() for d in DATES],
         "rebased_indices": list(rebase_row_original(HOUSE, DATES, DATES[0]).values),
         "log_count": VOL_HOUSE},
        {"rebased_dates": [pd.to_datetime(d).date() for d in DATES],
         "rebased_indices": list(rebase_row_original(APARTMENT, DATES, DATES[0]).values),
         "log_count": VOL_APARTMENT},
    ])
    orig_hybrid = aggregate_rebase_indices_original(df_orig)

    port_hybrid = mi.volume_weighted_index(
        [mi.rebase_series(DATES, HOUSE, DATES[0]), mi.rebase_series(DATES, APARTMENT, DATES[0])],
        [VOL_HOUSE, VOL_APARTMENT],
    )
    results.append(_check("volume_weighted_index vs aggregate_rebase_indices (computed hybrid)",
                          port_hybrid.values, orig_hybrid.values))

    # 6. Implied past price: current / (1 + evolution)
    current_price = 3500.0
    cum_evol = (house.iloc[-1] / house.iloc[0]) - 1
    results.append(_check("implied_price_at vs current/(1+evol)",
                          [mi.implied_price_at(current_price, cum_evol)],
                          [current_price / (1 + cum_evol)]))

    # 7. build_index_series end-to-end uses the same primitives (sanity, no DB)
    indices_df = pd.DataFrame([
        {"item": "house", "PLACE_ID": "AD02DE1", "DATES": DATES, "VALUES": HOUSE, "last_volume": VOL_HOUSE},
        {"item": "apartment", "PLACE_ID": "AD02DE1", "DATES": DATES, "VALUES": APARTMENT, "last_volume": VOL_APARTMENT},
    ])
    computed_house = mi.build_index_series(indices_df, "house", ["AD02DE1"], ref_date=None)
    results.append(_check("build_index_series(house) preserves already-rebased Snowflake values",
                          computed_house.values, HOUSE, tol=1e-6))

    # 8. Computed-hybrid fallback (SELL has no pre-built HYBRID) must equal the
    #    original aggregate_rebase_indices over house+apartment.
    computed_hybrid = mi.build_index_series(indices_df, "hybrid", ["AD02DE1"], ref_date=None)
    results.append(_check("build_index_series(hybrid) fallback vs aggregate_rebase_indices",
                          computed_hybrid.values, orig_hybrid.values))

    print("\n" + ("ALL PASSED" if all(results) else "SOME FAILED")
          + f"  ({sum(results)}/{len(results)})")
    return all(results)


if __name__ == "__main__":
    raise SystemExit(0 if main() else 1)

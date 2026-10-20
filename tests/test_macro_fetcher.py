"""Tests for housing_germany.macro_fetcher."""
from __future__ import annotations

import pandas as pd
import pytest

from housing_germany.macro_fetcher import (
    _filter_residential_permits,
    _parse_genesis_ffcsv,
    _parse_genesis_period,
)

# ---------------------------------------------------------------------------
# ECB CSV parsing
# ---------------------------------------------------------------------------

ECB_CSV_SAMPLE = """\
KEY,FREQ,REF_AREA,BS_ITEM,TIME_PERIOD,OBS_VALUE,OBS_STATUS
MIR.M.DE.B.A2C.A.B.A.2250.EUR.P,M,DE,A2C,2017-01,14500,A
MIR.M.DE.B.A2C.A.B.A.2250.EUR.P,M,DE,A2C,2017-02,13200,A
MIR.M.DE.B.A2C.A.B.A.2250.EUR.P,M,DE,A2C,2017-03,15800,A
"""


def test_ecb_csv_parsing():
    """Verify that the ECB CSV can be parsed and filtered correctly."""
    import io
    df = pd.read_csv(io.StringIO(ECB_CSV_SAMPLE))
    df = df[["TIME_PERIOD", "OBS_VALUE"]].rename(columns={
        "TIME_PERIOD": "period",
        "OBS_VALUE": "loan_volume_mio_eur",
    })
    df["period"] = pd.to_datetime(df["period"], format="%Y-%m")

    assert len(df) == 3
    assert df["period"].iloc[0] == pd.Timestamp("2017-01-01")
    assert df["loan_volume_mio_eur"].iloc[0] == 14500


# ---------------------------------------------------------------------------
# GENESIS period parsing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("2017M01", pd.Timestamp("2017-01-01")),
    ("2024M12", pd.Timestamp("2024-12-01")),
    ("2020M06", pd.Timestamp("2020-06-01")),
    ("2017 Januar", pd.Timestamp("2017-01-01")),
    ("2024 Dezember", pd.Timestamp("2024-12-01")),
    ("2020 März", pd.Timestamp("2020-03-01")),
])
def test_parse_genesis_period(raw, expected):
    assert _parse_genesis_period(raw) == expected


def test_parse_genesis_period_bad_input():
    assert _parse_genesis_period("garbage") is None


# ---------------------------------------------------------------------------
# GENESIS ffcsv parsing (official format)
# ---------------------------------------------------------------------------

GENESIS_FFCSV_OFFICIAL = """\
statistics_code;statistics_label;time_code;time_label;time;1_variable_code;1_variable_label;1_variable_attribute_code;1_variable_attribute_label;4_variable_code;4_variable_label;4_variable_attribute_code;4_variable_attribute_label;5_variable_code;5_variable_label;5_variable_attribute_code;5_variable_attribute_label;value;value_unit;value_variable_code;value_variable_label
31111;Baugenehmigungen;JAHR;Jahr;2017;MONAT;Monate;MONAT01;Januar;BAUTK1;Bautätigkeiten;BTKNB;Errichtung neuer Gebäude;GEBTYP;Gebäudeart;GT010;Wohngebäude;4500;Anzahl;BAU003;Gebäude
31111;Baugenehmigungen;JAHR;Jahr;2017;MONAT;Monate;MONAT02;Februar;BAUTK1;Bautätigkeiten;BTKNB;Errichtung neuer Gebäude;GEBTYP;Gebäudeart;GT010;Wohngebäude;4800;Anzahl;BAU003;Gebäude
31111;Baugenehmigungen;JAHR;Jahr;2017;MONAT;Monate;MONAT01;Januar;BAUTK1;Bautätigkeiten;BTKNB;Errichtung neuer Gebäude;GEBTYP;Gebäudeart;GT020;Wohngebäude mit 1 Wohnung;3200;Anzahl;BAU003;Gebäude
"""


def test_parse_genesis_ffcsv_official():
    df = _parse_genesis_ffcsv(GENESIS_FFCSV_OFFICIAL)

    assert "period" in df.columns
    assert "activity_type" in df.columns
    assert "building_type" in df.columns
    assert "value" in df.columns
    assert len(df) == 3
    assert df["period"].iloc[0] == pd.Timestamp("2017-01-01")
    assert df["activity_type"].iloc[0] == "Errichtung neuer Gebäude"
    assert df["building_type"].iloc[0] == "Wohngebäude"
    assert df["value"].iloc[0] == 4500


def test_filter_residential_permits():
    raw = pd.DataFrame({
        "period": pd.to_datetime(["2017-01-01"] * 4),
        "activity_type": [
            "Errichtung neuer Gebäude",
            "Errichtung neuer Gebäude",
            "Errichtung neuer Gebäude",
            "Baumaßnahmen an einem bestehenden Gebäude",
        ],
        "building_type": ["Wohngebäude", "Wohngebäude", "Nichtwohngebäude", "Wohngebäude"],
        "metric": ["Wohnungen", "Gebäude", "Wohnungen", "Wohnungen"],
        "value": [15103, 4713, 200, 2700],
    })
    result = _filter_residential_permits(raw)
    assert len(result) == 1
    assert list(result.columns) == ["period", "wohnungen"]
    assert result["wohnungen"].iloc[0] == 15103

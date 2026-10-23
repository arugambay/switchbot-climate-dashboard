"""Tests für das CSV-Einlesen (nur synthetische Daten)."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from climate_dashboard.domain.models import ABS_HUMIDITY, DEW_POINT, HUMIDITY, METRICS, TEMPERATURE, TS, VPD
from climate_dashboard.ingest.columns import map_columns, split_header
from climate_dashboard.ingest.csv_reader import parse_csv
from climate_dashboard.ingest.validation import Severity
from tests.synthetic import make_measurements, to_switchbot_csv

FIXTURES = Path(__file__).parent / "fixtures"


def codes(parsed, severity=None):
    return {i.code for i in parsed.issues if severity is None or i.severity == severity}


def utc(*values: str) -> list[pd.Timestamp]:
    return [pd.Timestamp(v, tz="UTC") for v in values]


def assert_invariant(parsed):
    assert parsed.rows_read == len(parsed.data) + len(parsed.invalid_rows)


# ---------------------------------------------------------------------------
# Trennzeichen, Dezimalzeichen, Kodierung
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("delimiter", "decimal", "delim_name", "decimal_name"),
    [
        (",", ",", "Komma", "Komma"),  # echtes SwitchBot-Format: "19,2" in Anführungszeichen
        (";", ",", "Semikolon", "Komma"),
        ("\t", ".", "Tabulator", "Punkt"),
        (";", ".", "Semikolon", "Punkt"),
        ("|", ",", "senkrechter Strich", "Komma"),
    ],
)
def test_delimiter_and_decimal_roundtrip(delimiter, decimal, delim_name, decimal_name):
    df = make_measurements("2026-01-05", "2026-01-07", interval_min=30)
    parsed = parse_csv(to_switchbot_csv(df, delimiter=delimiter, decimal=decimal), "x.csv")
    assert parsed.detection["delimiter_name"] == delim_name
    assert parsed.detection["decimal"] == decimal_name
    assert parsed.detection["decimal_by_column"]["humidity(%)"] == "ganzzahlig"
    assert not parsed.has_errors
    assert parsed.rows_read == len(df) == 96
    out = parsed.data.reset_index(drop=True)
    assert out[TS].tolist() == df[TS].tolist()
    for m in METRICS:
        np.testing.assert_allclose(
            out[m].to_numpy(), df[m].round(2 if m in (VPD, ABS_HUMIDITY) else 1).to_numpy()
        )
    assert out["raw_timestamp"].iloc[0] == "2026-01-05 00:00:00"
    assert parsed.data.index[0] == 2  # Zeilennummer in der Datei (Kopfzeile = 1)


def test_quoted_values_with_semicolon():
    content = (
        '"timestamp";"temperature(℃)";"humidity(%)"\n'
        '"2026-01-10 08:00:00";"19,5";"55"\n'
        '"2026-01-10 08:30:00";"-2,25";"56"\n'
    ).encode()
    parsed = parse_csv(content, "q.csv")
    assert parsed.detection["delimiter"] == ";"
    assert parsed.data[TEMPERATURE].tolist() == [19.5, -2.25]
    assert parsed.data[HUMIDITY].tolist() == [55.0, 56.0]


def test_semicolon_fixture_with_empty_line():
    parsed = parse_csv((FIXTURES / "semikolon_dezimalkomma.csv").read_bytes(), "semikolon_dezimalkomma.csv")
    assert parsed.detection["delimiter"] == ";"
    assert parsed.detection["encoding"] == "UTF-8"
    assert parsed.rows_read == 4
    assert parsed.empty_lines == 1
    assert "empty_lines" in codes(parsed, Severity.INFO)
    assert parsed.data.index.tolist() == [2, 3, 5, 6]
    assert parsed.data[TEMPERATURE].tolist() == [19.5, 19.7, 20.1, 20.4]
    assert parsed.data[VPD].tolist() == [1.02, 1.05, 1.10, 1.15]
    assert parsed.data[TS].tolist() == utc(
        "2026-01-10 07:00", "2026-01-10 07:30", "2026-01-10 08:00", "2026-01-10 08:30"
    )
    assert parsed.detection["missing_metrics"] == []


@pytest.mark.parametrize(
    ("encoding", "expected"),
    [("utf-8-sig", "UTF-8 mit BOM"), ("utf-16", "UTF-16"), ("cp1252", "Windows-1252"), ("utf-8", "UTF-8")],
)
def test_encodings(encoding, expected):
    text = "Zeitstempel;Temperatur (°C);Luftfeuchtigkeit (%)\n01.02.2026 10:00;21,5;48\n01.02.2026 10:15;21,4;49\n"
    parsed = parse_csv(text.encode(encoding), "enc.csv")
    assert parsed.detection["encoding"] == expected
    assert parsed.detection["column_mapping"] == {
        "Zeitstempel": "timestamp",
        "Temperatur (°C)": TEMPERATURE,
        "Luftfeuchtigkeit (%)": HUMIDITY,
    }
    assert parsed.data[TEMPERATURE].tolist() == [21.5, 21.4]
    assert parsed.data[TS].tolist() == utc("2026-02-01 09:00", "2026-02-01 09:15")


def test_cp1252_fixture_separate_date_time_columns_any_order():
    parsed = parse_csv((FIXTURES / "deutsch_cp1252.csv").read_bytes(), "deutsch_cp1252.csv")
    assert parsed.detection["encoding"] == "Windows-1252"
    assert parsed.detection["unknown_columns"] == ["Bemerkung"]
    assert "unknown_columns" in codes(parsed, Severity.INFO)
    assert parsed.data[HUMIDITY].tolist() == [61.0, 63.0, 64.0]
    assert parsed.data[TEMPERATURE].tolist() == [18.0, 18.2, 18.3]
    assert parsed.data[TS].tolist() == utc("2026-02-15 06:00", "2026-02-15 06:15", "2026-02-15 06:30")
    assert parsed.data["raw_timestamp"].iloc[0] == "15.02.2026 07:00"


def test_fahrenheit_fixture_with_bom_and_us_dates():
    parsed = parse_csv((FIXTURES / "fahrenheit_us.csv").read_bytes(), "fahrenheit_us.csv")
    assert parsed.detection["encoding"] == "UTF-8 mit BOM"
    assert parsed.detection["fahrenheit_columns"] == ["Temperature_Fahrenheit(°F)"]
    assert "fahrenheit_converted" in codes(parsed, Severity.INFO)
    np.testing.assert_allclose(parsed.data[TEMPERATURE], [20.0, 21.0, 10.0])
    assert parsed.detection["timestamp_format"].startswith("MM/TT/JJJJ")
    # 02/13 → 13. Februar (Monat zuerst, weil 13 kein Monat sein kann)
    assert parsed.data[TS].tolist() == utc("2026-02-13 09:00", "2026-02-13 09:15", "2026-02-14 09:00")


def test_celsius_preferred_over_fahrenheit_duplicate():
    content = b"timestamp,Temperature_Fahrenheit(\xc2\xb0F),Temperature_Celsius(\xc2\xb0C),humidity\n2026-01-01 10:00,68,20,50\n"
    parsed = parse_csv(content, "x.csv")
    assert parsed.data[TEMPERATURE].tolist() == [20.0]
    assert parsed.detection["ignored_columns"] == ["Temperature_Fahrenheit(°F)"]
    assert parsed.detection["fahrenheit_columns"] == []


@pytest.mark.parametrize(
    ("header", "role"),
    [
        ("Timestamp", "timestamp"),
        ("  TIMESTAMP ", "timestamp"),
        ("Date", "date"),
        ("Datum", "date"),
        ("Time", "time"),
        ("Uhrzeit", "time"),
        ("temperature(℃)", TEMPERATURE),
        ("Temperature_Celsius(°C)", TEMPERATURE),
        ("Temperature_Fahrenheit(°F)", TEMPERATURE),
        ("Temperatur", TEMPERATURE),
        ("humidity(%)", HUMIDITY),
        ("Relative_Humidity(%)", HUMIDITY),
        ("Luftfeuchtigkeit", HUMIDITY),
        ("dpt(℃)", DEW_POINT),
        ("DewPoint", DEW_POINT),
        ("Taupunkt", DEW_POINT),
        ("vpd(kPa)", VPD),
        ("absoluteHumidity(g/m³)", ABS_HUMIDITY),
        ("Absolute Humidity (g/m3)", ABS_HUMIDITY),
    ],
)
def test_header_aliases(header, role):
    assert map_columns([header]).roles == {role: header}


def test_split_header_units():
    assert split_header("temperature(℃)") == ("temperature", "°c")
    assert split_header("Temperature_Fahrenheit(°F)") == ("temperaturefahrenheit", "°f")


# ---------------------------------------------------------------------------
# Fehlende Spalten
# ---------------------------------------------------------------------------
def test_only_temperature_and_humidity():
    parsed = parse_csv(b"timestamp,temperature,humidity\n2026-01-01 10:00,20.5,50\n", "x.csv")
    assert not parsed.has_errors
    assert parsed.detection["missing_metrics"] == [DEW_POINT, VPD, ABS_HUMIDITY]
    assert "optional_metrics_missing" in codes(parsed, Severity.INFO)
    assert parsed.data[DEW_POINT].isna().all()
    assert parsed.data[DEW_POINT].dtype == "float64"


def test_missing_humidity_is_warning_only():
    parsed = parse_csv(b"timestamp,temperature\n2026-01-01 10:00,20.5\n", "x.csv")
    assert not parsed.has_errors
    assert "no_humidity_column" in codes(parsed, Severity.WARNING)


def test_missing_temperature_column_is_error_without_exception():
    parsed = parse_csv(b"timestamp,humidity\n2026-01-01 10:00,50\n", "x.csv")
    assert parsed.has_errors
    assert "no_temperature_column" in codes(parsed, Severity.ERROR)
    assert len(parsed.data) == 0


def test_missing_timestamp_column_is_error():
    parsed = parse_csv(b"foo,temperature\n1,20\n", "x.csv")
    assert "no_timestamp_column" in codes(parsed, Severity.ERROR)


@pytest.mark.parametrize("content", [b"", b"   \n\n", b"\x00\x01\x02\x03\x04\x05\x06\x07" * 50])
def test_unreadable_content_is_error(content):
    parsed = parse_csv(content, "kaputt.csv")
    assert parsed.has_errors
    assert len(parsed.data) == 0


def test_header_only_is_error():
    parsed = parse_csv(b"timestamp,temperature,humidity\n", "x.csv")
    assert "no_data_rows" in codes(parsed, Severity.ERROR)


# ---------------------------------------------------------------------------
# Zeitstempel
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected_utc"),
    [
        ("2026-01-15 10:00:00", "2026-01-15 09:00"),
        ("2026-01-15 10:00", "2026-01-15 09:00"),
        ("2026-01-15T10:00:00", "2026-01-15 09:00"),
        ("2026-07-15T10:00", "2026-07-15 08:00"),
        ("2026-01-15T10:00:00Z", "2026-01-15 10:00"),
        ("2026-01-15T10:00:00+02:00", "2026-01-15 08:00"),
        ("15.01.2026 10:00", "2026-01-15 09:00"),
        ("15.01.2026 10:00:30", "2026-01-15 09:00:30"),
        ("1768471200", "2026-01-15 10:00"),
        ("1768471200000", "2026-01-15 10:00"),
    ],
)
def test_timestamp_formats(raw, expected_utc):
    parsed = parse_csv(f"timestamp;temperature\n{raw};20\n".encode(), "x.csv")
    assert not parsed.has_errors, parsed.issues
    assert parsed.data[TS].tolist() == utc(expected_utc)
    assert parsed.data["raw_timestamp"].tolist() == [raw]


def test_day_first_slash_dates():
    content = b"Date,Temp,RH\n13/02/2026 10:00,20,50\n14/02/2026 10:00,20,50\n"
    parsed = parse_csv(content, "x.csv")
    assert parsed.detection["timestamp_format"].startswith("TT/MM/JJJJ")
    assert parsed.data[TS].tolist() == utc("2026-02-13 09:00", "2026-02-14 09:00")


def test_ambiguous_slash_dates_resolved_by_order():
    # Tag zuerst ergibt eine plausible Reihe (03.04., 04.04., 05.04.), Monat zuerst nicht
    content = b"Date,Temp,RH\n03/04/2026 10:00,20,50\n04/04/2026 10:00,20,50\n05/04/2026 10:00,20,50\n"
    parsed = parse_csv(content, "x.csv")
    assert parsed.data[TS].dt.day.tolist() == [3, 4, 5]
    assert parsed.data[TS].dt.month.tolist() == [4, 4, 4]


def test_undecidable_slash_dates_warn_and_assume_day_first():
    parsed = parse_csv(b"Date,Temp\n03/04/2026 10:00,20\n", "x.csv")
    assert "date_order_ambiguous" in codes(parsed, Severity.WARNING)
    assert parsed.data[TS].tolist() == utc("2026-04-03 08:00")


def test_invalid_and_missing_timestamps():
    content = (
        b"timestamp,temperature,humidity\n"
        b"2026-01-01 10:00,20,50\n"
        b"kein datum,21,50\n"
        b",22,50\n"
        b"2026-13-45 10:00,23,50\n"
        b"2026-01-01 10:30,24,50\n"
    )
    parsed = parse_csv(content, "x.csv")
    assert parsed.rows_read == 5
    assert parsed.data[TEMPERATURE].tolist() == [20.0, 24.0]
    inv = parsed.invalid_rows
    assert inv["line"].tolist() == [3, 4, 5]
    assert inv["reason"].tolist() == [
        "Zeitstempel nicht lesbar",
        "Zeitstempel fehlt",
        "Zeitstempel nicht lesbar",
    ]
    assert inv["raw"].iloc[0] == "kein datum,21,50"
    assert "timestamp_unreadable" in codes(parsed, Severity.WARNING)
    assert_invariant(parsed)


def test_unsorted_file_is_sorted_with_info():
    content = b"timestamp,temperature\n2026-01-01 10:30,3\n2026-01-01 10:00,1\n2026-01-01 10:15,2\n"
    parsed = parse_csv(content, "x.csv")
    assert parsed.data[TEMPERATURE].tolist() == [1.0, 2.0, 3.0]
    assert parsed.data.index.tolist() == [3, 4, 2]
    assert "unsorted" in codes(parsed, Severity.INFO)


def test_sorted_file_has_no_unsorted_info():
    parsed = parse_csv(b"timestamp,temperature\n2026-01-01 10:00,1\n2026-01-01 10:15,2\n", "x.csv")
    assert "unsorted" not in codes(parsed)


def test_dst_spring_nonexistent_times_are_invalid_not_shifted():
    content = (
        b"timestamp;temperature;humidity\n"
        b"2026-03-29 01:30;5,0;50\n"
        b"2026-03-29 02:00;5,1;50\n"
        b"2026-03-29 02:30;5,2;50\n"
        b"2026-03-29 03:00;5,3;50\n"
    )
    parsed = parse_csv(content, "x.csv")
    assert parsed.data[TS].tolist() == utc("2026-03-29 00:30", "2026-03-29 01:00")
    assert parsed.invalid_rows["line"].tolist() == [3, 4]
    assert parsed.invalid_rows["reason"].str.contains("Sommerzeit").all()
    issue = next(i for i in parsed.issues if i.code == "dst_nonexistent")
    assert issue.severity == Severity.WARNING and issue.count == 2
    assert_invariant(parsed)


def test_dst_autumn_ambiguous_resolved_by_order():
    content = (
        b"timestamp,temperature,humidity\n"
        b"2026-10-25 01:30,5.0,50\n"
        b"2026-10-25 02:00,5.1,50\n"
        b"2026-10-25 02:30,5.2,50\n"
        b"2026-10-25 02:00,5.3,50\n"
        b"2026-10-25 02:30,5.4,50\n"
        b"2026-10-25 03:00,5.5,50\n"
    )
    parsed = parse_csv(content, "x.csv")
    assert parsed.data[TS].tolist() == utc(
        "2026-10-24 23:30",
        "2026-10-25 00:00",
        "2026-10-25 00:30",
        "2026-10-25 01:00",
        "2026-10-25 01:30",
        "2026-10-25 02:00",
    )
    assert parsed.data[TEMPERATURE].tolist() == [5.0, 5.1, 5.2, 5.3, 5.4, 5.5]
    assert "dst_ambiguous_resolved" in codes(parsed, Severity.INFO)
    assert "unsorted" not in codes(parsed)


def test_dst_autumn_hourly_repeat_resolved():
    content = b"timestamp,temperature\n2026-10-25 01:00,1\n2026-10-25 02:00,2\n2026-10-25 02:00,3\n2026-10-25 03:00,4\n"
    parsed = parse_csv(content, "x.csv")
    assert parsed.data[TS].tolist() == utc(
        "2026-10-24 23:00", "2026-10-25 00:00", "2026-10-25 01:00", "2026-10-25 02:00"
    )


def test_dst_autumn_unresolvable_assumes_summer_time():
    content = b"timestamp,temperature\n2026-10-25 02:15,1\n2026-10-25 02:45,2\n"
    parsed = parse_csv(content, "x.csv")
    assert "dst_ambiguous_unresolved" in codes(parsed, Severity.WARNING)
    assert parsed.data[TS].tolist() == utc("2026-10-25 00:15", "2026-10-25 00:45")


@pytest.mark.parametrize(("start", "end"), [("2026-03-27", "2026-04-01"), ("2026-10-23", "2026-10-28")])
def test_synthetic_roundtrip_across_dst(start, end):
    # Synthetischer Sensor misst in UTC regelmäßig: Frühjahr ohne 02:xx, Herbst mit doppelter Stunde
    df = make_measurements(start, end, interval_min=30)
    parsed = parse_csv(to_switchbot_csv(df), "x.csv")
    assert not ({"dst_nonexistent", "dst_ambiguous_unresolved"} & codes(parsed))
    assert parsed.data[TS].tolist() == df[TS].tolist()
    assert parsed.data[TS].is_unique


# ---------------------------------------------------------------------------
# Zahlen und Plausibilität
# ---------------------------------------------------------------------------
def test_mixed_decimal_separators_detected():
    content = b"timestamp;temperature;humidity\n2026-01-01 10:00;20,5;50\n2026-01-01 10:15;20.7;51\n"
    parsed = parse_csv(content, "x.csv")
    assert parsed.data[TEMPERATURE].tolist() == [20.5, 20.7]
    assert parsed.detection["decimal_by_column"]["temperature"] == "gemischt"
    assert "decimal_mixed" in codes(parsed, Severity.WARNING)


def test_unreadable_values_and_units():
    content = (
        b"timestamp,temperature,humidity\n2026-01-01 10:00,20.5 \xc2\xb0C,55 %\n2026-01-01 10:15,abc,50\n"
    )
    parsed = parse_csv(content, "x.csv")
    assert parsed.data[TEMPERATURE].tolist()[0] == 20.5
    assert parsed.data[HUMIDITY].tolist()[0] == 55.0
    assert np.isnan(parsed.data[TEMPERATURE].tolist()[1])
    assert "value_unreadable" in codes(parsed, Severity.WARNING)


def test_plausibility_rules():
    content = (
        b"timestamp,temperature,humidity,dpt\n"
        b"2026-01-01 10:00,120,50,5\n"  # Temperatur außerhalb Sensorbereich → NaN, Zeile bleibt (rF)
        b"2026-01-01 10:15,55,50,5\n"  # Raum-unplausibel → Warnung, Wert bleibt
        b"2026-01-01 10:30,20,105,5\n"  # rF > 100 → NaN
        b"2026-01-01 10:45,20,100,25\n"  # rF genau 100 → Warnung; Taupunkt > T → NaN
        b"2026-01-01 11:00,200,-3,5\n"  # weder T noch rF gültig → Zeile ungültig
    )
    parsed = parse_csv(content, "x.csv")
    d = parsed.data
    assert d.index.tolist() == [2, 3, 4, 5]
    assert np.isnan(d.loc[2, TEMPERATURE]) and d.loc[2, HUMIDITY] == 50
    assert d.loc[3, TEMPERATURE] == 55
    assert np.isnan(d.loc[4, HUMIDITY]) and d.loc[4, TEMPERATURE] == 20
    assert d.loc[5, HUMIDITY] == 100 and np.isnan(d.loc[5, DEW_POINT])
    assert parsed.invalid_rows["line"].tolist() == [6]
    warn = codes(parsed, Severity.WARNING)
    assert {
        "temperature_out_of_range",
        "temperature_implausible_room",
        "humidity_out_of_range",
        "humidity_extreme",
        "dew_point_above_temperature",
        "row_without_values",
    } <= warn
    t_issue = next(i for i in parsed.issues if i.code == "temperature_out_of_range")
    assert t_issue.count == 2  # Zeilen 2 und 6
    assert t_issue.examples[0] == "Zeile 2: 120"
    assert_invariant(parsed)


def test_negative_vpd_and_abs_humidity_discarded():
    content = b"timestamp,temperature,humidity,vpd,absoluteHumidity\n2026-01-01 10:00,20,50,-0.5,-1\n"
    parsed = parse_csv(content, "x.csv")
    assert parsed.data[VPD].isna().all() and parsed.data[ABS_HUMIDITY].isna().all()
    assert {"vpd_negative", "abs_humidity_out_of_range"} <= codes(parsed, Severity.WARNING)


def test_too_many_fields_marks_row_invalid():
    content = b"timestamp,temperature,humidity\n2026-01-01 10:00,20,50\n2026-01-01 10:15,21,50,99,98\n"
    parsed = parse_csv(content, "x.csv")
    assert parsed.data[TEMPERATURE].tolist() == [20.0]
    assert parsed.invalid_rows["reason"].tolist() == ["Zeile hat mehr Felder als die Kopfzeile"]


def test_excel_sep_hint():
    parsed = parse_csv(b"sep=,\ntimestamp,temperature\n2026-01-01 10:00,20\n", "x.csv")
    assert parsed.detection["delimiter"] == ","
    assert parsed.data.index.tolist() == [3]


def test_sha256_and_to_dict_serializable():
    content = b"timestamp,temperature\n2026-01-01 10:00,20\nxx,1\n"
    parsed = parse_csv(content, "x.csv")
    assert parsed.sha256 == hashlib.sha256(content).hexdigest()
    d = parsed.to_dict()
    json.dumps(d)
    assert d["rows_read"] == 2 and d["rows_invalid"] == 1 and d["invalid_rows"][0]["line"] == 3


# ---------------------------------------------------------------------------
# Große Datei
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_large_file_performance():
    df = make_measurements("2026-06-01", "2026-12-31", interval_min=1)  # ~305.000 Zeilen inkl. Zeitumstellung
    assert len(df) > 300_000
    content = to_switchbot_csv(df)
    t0 = time.perf_counter()
    parsed = parse_csv(content, "gross.csv")
    elapsed = time.perf_counter() - t0
    assert elapsed < 15, elapsed
    assert parsed.rows_read == len(df)
    assert len(parsed.data) + len(parsed.invalid_rows) == len(df)
    assert not parsed.has_errors
    np.testing.assert_allclose(parsed.data[TEMPERATURE].to_numpy(), df[TEMPERATURE].to_numpy())
    assert parsed.data[TS].tolist() == df[TS].tolist()

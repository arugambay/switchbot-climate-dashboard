"""Tests für die deutsche Formatierung (climate_dashboard.ui.formatting)."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from climate_dashboard.domain.models import ABS_HUMIDITY, DEW_POINT, HUMIDITY, TEMPERATURE, VPD
from climate_dashboard.ui import formatting as f

NN = " "  # schmales geschütztes Leerzeichen
SP = " "  # geschütztes Leerzeichen
M = "−"  # Minus


@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [
        (1234.5, 1, "1.234,5"),
        (1234567.891, 2, "1.234.567,89"),
        (0, 0, "0"),
        (0.04, 1, "0,0"),
        (-0.04, 1, "0,0"),  # kein "−0,0"
        (-12.345, 2, f"{M}12,35" if round(-12.345, 2) == -12.35 else f"{M}12,34"),
        (999.95, 1, "1.000,0"),
        (np.float64(21.44), 1, "21,4"),
        (np.int64(1500), 0, "1.500"),
        (7, 2, "7,00"),
    ],
)
def test_fmt_number(value, decimals, expected):
    assert f.fmt_number(value, decimals) == expected


@pytest.mark.parametrize("missing", [None, float("nan"), np.nan, pd.NA, pd.NaT, "abc", math.inf])
def test_missing_values_render_dash(missing):
    assert f.fmt_number(missing, 1) == "–"
    assert f.fmt_metric(missing, TEMPERATURE) == "–"
    assert f.fmt_delta(missing, TEMPERATURE) == "–"
    assert f.fmt_percent(missing) == "–"
    assert f.fmt_duration(missing) == "–"


def test_fmt_number_sign_and_grouping():
    assert f.fmt_number(1.25, 1, sign=True) in ("+1,2", "+1,3")
    assert f.fmt_number(-3, 0, sign=True) == f"{M}3"
    assert f.fmt_number(0, 1, sign=True) == "±0,0"
    assert f.fmt_number(12345, 0, grouping=False) == "12345"


def test_fmt_metric_units_and_decimals():
    assert f.fmt_metric(21.44, TEMPERATURE) == f"21,4{NN}°C"
    assert f.fmt_metric(57.4, HUMIDITY) == f"57{NN}%"
    assert f.fmt_metric(11.86, DEW_POINT) == f"11,9{NN}°C"
    assert f.fmt_metric(0.734, VPD) == f"0,73{NN}kPa"
    assert f.fmt_metric(9.26, ABS_HUMIDITY) == f"9,3{NN}g/m³"
    assert f.fmt_metric(-3.2, TEMPERATURE) == f"{M}3,2{NN}°C"
    assert f.fmt_metric(21.44, TEMPERATURE, decimals=2) == f"21,44{NN}°C"
    # unbekannte Messgröße: keine Einheit
    assert f.fmt_metric(3.14159, "foo") == "3,1"


def test_fmt_delta():
    assert f.fmt_delta(1.23, TEMPERATURE) == f"+1,2{NN}K"
    assert f.fmt_delta(-0.44, DEW_POINT) == f"{M}0,4{NN}K"
    assert f.fmt_delta(0.0, TEMPERATURE) == f"±0,0{NN}K"
    assert f.fmt_delta(3.2, HUMIDITY) == f"+3{SP}Prozentpunkte"
    assert f.fmt_delta(-1.0, HUMIDITY) == f"{M}1{SP}Prozentpunkt"
    assert f.fmt_delta(3.2, HUMIDITY, short=True) == f"+3{NN}%-Pkt."
    assert f.fmt_delta(0.05, VPD) == f"+0,05{NN}kPa"


def test_fmt_percent():
    assert f.fmt_percent(57) == f"57{NN}%"
    assert f.fmt_percent(0.573, 1, ratio=True) == f"57,3{NN}%"
    assert f.fmt_percent(12.5, 1, sign=True) == f"+12,5{NN}%"
    assert f.fmt_percent(100) == f"100{NN}%"


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, f"0{SP}Min."),
        (30, f"<{SP}1{SP}Min."),
        (60, f"1{SP}Min."),
        (20 * 60, f"20{SP}Min."),
        (3 * 3600 + 20 * 60, f"3{SP}Std. 20{SP}Min."),
        (3 * 3600, f"3{SP}Std."),
        (3599.9, f"1{SP}Std."),
        (2 * 86400 + 4 * 3600 + 10 * 60, f"2{SP}Tage 4{SP}Std."),
        (86400, f"1{SP}Tag"),
        (86400 + 3600 * 23.6, f"2{SP}Tage"),
        (timedelta(hours=1, minutes=5), f"1{SP}Std. 5{SP}Min."),
        (pd.Timedelta(minutes=90), f"1{SP}Std. 30{SP}Min."),
        (-3600, f"{M}1{SP}Std."),
    ],
)
def test_fmt_duration(seconds, expected):
    assert f.fmt_duration(seconds) == expected


def test_fmt_duration_max_units():
    assert f.fmt_duration(2 * 86400 + 4 * 3600 + 10 * 60, max_units=3) == f"2{SP}Tage 4{SP}Std. 10{SP}Min."
    assert f.fmt_duration(3 * 3600 + 40 * 60, max_units=1) == f"4{SP}Std."


def test_fmt_datetime_converts_utc_to_local():
    utc = datetime(2026, 10, 22, 19, 45, tzinfo=UTC)  # 21:45 MESZ
    assert f.fmt_datetime(utc) == "22.10.2026, 21:45"
    assert f.fmt_datetime(pd.Timestamp("2026-10-22T19:45:00Z")) == "22.10.2026, 21:45"
    assert f.fmt_datetime("2026-10-22T19:45:00+00:00") == "22.10.2026, 21:45"
    # naiv = UTC
    assert f.fmt_datetime(datetime(2026, 1, 5, 7, 3)) == "05.01.2026, 08:03"
    assert f.fmt_datetime(utc, "UTC", seconds=True) == "22.10.2026, 19:45:00"
    assert f.fmt_datetime(None) == "–"
    assert f.fmt_datetime(pd.NaT) == "–"
    assert f.fmt_datetime("kein Datum") == "–"
    assert f.fmt_time(utc) == "21:45"


def test_fmt_date_styles():
    ts = pd.Timestamp("2026-10-22T10:00:00Z")
    assert f.fmt_date(ts) == "22.10.2026"
    assert f.fmt_date(ts, style="long") == "22. Oktober 2026"
    assert f.fmt_date(ts, style="weekday") == "Do, 22.10.2026"
    assert f.fmt_date(date(2026, 3, 1)) == "01.03.2026"
    # Mitternacht-Grenze: 23:30 UTC ist in Berlin schon der nächste Tag
    assert f.fmt_date(pd.Timestamp("2026-03-01T23:30:00Z")) == "02.03.2026"


def test_fmt_date_range():
    s = pd.Timestamp("2026-03-01", tz="Europe/Berlin").tz_convert("UTC")
    e = pd.Timestamp("2026-10-22 18:00", tz="Europe/Berlin").tz_convert("UTC")
    assert f.fmt_date_range(s, e) == "01.03.–22.10.2026"
    assert f.fmt_date_range(date(2025, 12, 15), date(2026, 1, 10)) == "15.12.2025–10.01.2026"
    assert f.fmt_date_range(date(2026, 10, 22), date(2026, 10, 22)) == "22.10.2026"
    e_excl = pd.Timestamp("2026-04-01", tz="Europe/Berlin").tz_convert("UTC")
    assert f.fmt_date_range(s, e_excl, end_exclusive=True) == "01.03.–31.03.2026"
    assert f.fmt_date_range(None, None) == "–"
    assert f.fmt_date_range(s, None) == "ab 01.03.2026"


def test_month_weekday_season_names():
    assert f.month_name(3) == "März"
    assert f.month_name(3, short=True) == "Mär"
    assert f.month_name(13) == "–"
    assert f.fmt_month("2026-10") == "Oktober 2026"
    assert f.fmt_month(pd.Period("2026-03", freq="M"), short=True) == "Mär 2026"
    assert f.fmt_month(date(2026, 5, 1)) == "Mai 2026"
    assert f.weekday_name(0) == "Montag"
    assert f.weekday_name(6, short=True) == "So"
    assert len(f.MONTH_NAMES) == 12 and len(f.WEEKDAY_NAMES) == 7
    assert f.season_key(1) == "winter" and f.season_key(4) == "spring"
    assert f.season_key(7) == "summer" and f.season_key(10) == "autumn" and f.season_key(12) == "winter"
    assert f.season_label("summer") == "Sommer"
    assert f.season_label("Herbst") == "Herbst"
    assert f.season_label("DJF") == "Winter"
    assert f.season_label(7, 2026) == "Sommer 2026"
    assert f.season_label("winter", 2026) == "Winter 2025/26"
    assert f.season_label("unbekannt") == "unbekannt"


def test_fmt_value_generic():
    assert f.fmt_value(None) == "–"
    assert f.fmt_value(True) == "Ja"
    assert f.fmt_value(1234) == "1.234"
    assert f.fmt_value(2.345) == "2,3"
    assert f.fmt_value(21.44, TEMPERATURE) == f"21,4{NN}°C"
    assert f.fmt_value("Bad") == "Bad"

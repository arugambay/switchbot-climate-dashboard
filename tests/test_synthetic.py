"""Tests für den synthetischen Testdaten-Generator (sommerzeitrobust, vektorisierte CSV)."""

from __future__ import annotations

import pandas as pd

from climate_dashboard.domain.models import TS
from tests.synthetic import make_measurements, to_switchbot_csv


def test_one_minute_across_autumn_dst_has_no_error():
    df = make_measurements("2024-10-26 22:00", "2024-10-27 05:00", interval_min=1)
    assert len(df) == 8 * 60  # 7 h Ortszeit + doppelte Stunde
    assert df[TS].is_unique and df[TS].is_monotonic_increasing
    local = df[TS].dt.tz_convert("Europe/Berlin").dt.strftime("%H:%M")
    assert (local == "02:30").sum() == 2


def test_spring_dst_skips_nonexistent_hour():
    df = make_measurements("2026-03-29 00:00", "2026-03-29 05:00", interval_min=30)
    local = df[TS].dt.tz_convert("Europe/Berlin").dt.strftime("%H:%M").tolist()
    assert local == ["00:00", "00:30", "01:00", "01:30", "03:00", "03:30", "04:00", "04:30"]


def test_regular_utc_grid_and_gaps():
    df = make_measurements(
        "2026-01-05", "2026-01-06", interval_min=15, gaps=[("2026-01-05 10:00", "2026-01-05 12:00")]
    )
    assert len(df) == 96 - 8
    assert (df[TS].diff().dropna() >= pd.Timedelta("15min")).all()


def test_switchbot_csv_format():
    df = make_measurements("2026-01-05 10:00", "2026-01-05 10:30", interval_min=30)
    text = to_switchbot_csv(df).decode()
    header, row = text.splitlines()
    assert header == "timestamp,temperature(℃),humidity(%),dpt(℃),vpd(kPa),absoluteHumidity(g/m³)"
    t = df.iloc[0]
    expected = (
        (
            f'2026-01-05 10:00:00,"{t.temperature:.1f}",{int(t.humidity)},"{t.dew_point:.1f}",'
            f'"{t.vpd:.2f}","{t.abs_humidity:.2f}"'
        )
        .replace(".", ",")
        .replace("2026-01-05 10:00:00".replace(".", ","), "2026-01-05 10:00:00")
    )
    assert row == expected
    semi = to_switchbot_csv(df, delimiter=";", decimal=".").decode().splitlines()[1]
    assert (
        semi
        == f"2026-01-05 10:00:00;{t.temperature:.1f};{int(t.humidity)};{t.dew_point:.1f};{t.vpd:.2f};{t.abs_humidity:.2f}"
    )

import json
import time

import numpy as np
import pandas as pd
import pytest

from climate_dashboard.analysis import humidity
from climate_dashboard.domain.models import HUMIDITY, TEMPERATURE, TS, AnalysisSettings

TZ = "Europe/Berlin"


def series(start, end, freq, rh_fn, t=20.0, drop=None) -> pd.DataFrame:
    local = pd.date_range(start, end, freq=freq, inclusive="left")
    if drop:
        for a, b in drop:
            local = local[(local < a) | (local >= b)]
    rh = np.array([rh_fn(x) for x in local], dtype=float)
    return pd.DataFrame({TS: local.tz_localize(TZ).tz_convert("UTC"), TEMPERATURE: t, HUMIDITY: rh})


def between(a, b, value, base=50.0):
    a, b = pd.Timestamp(a), pd.Timestamp(b)
    return lambda x: value if a <= x < b else base


def test_band_shares_exact():
    values = [30] * 4 + [50] * 4 + [65] * 4 + [75] * 4
    local = pd.date_range("2026-03-02 00:00", periods=16, freq="30min")
    df = pd.DataFrame({TS: local.tz_localize(TZ).tz_convert("UTC"), TEMPERATURE: 20.0, HUMIDITY: values})
    b = humidity.band_shares(df, AnalysisSettings(), expected_s=1800)
    assert b.covered_hours == 8
    assert [band.hours for band in b.bands] == [2, 2, 2, 2]
    assert [band.pct for band in b.bands] == [25, 25, 25, 25]
    assert b.pct_at_or_above_elevated == 50
    assert b.hours_at_or_above_high == 2
    assert b.gap_hours == 0
    json.dumps(b.to_dict())


def test_band_shares_gap_time_separate():
    df = series("2026-03-02 00:00", "2026-03-02 12:00", "30min", lambda x: 65.0,
                drop=[("2026-03-02 04:00", "2026-03-02 08:00")])
    b = humidity.band_shares(df, expected_s=1800,
                             period_start=pd.Timestamp("2026-03-02 00:00", tz=TZ),
                             period_end=pd.Timestamp("2026-03-02 12:00", tz=TZ))
    assert b.covered_hours == 8
    assert b.gap_hours == 4
    assert b.pct_at_or_above_elevated == 100  # Anteil bezieht sich auf abgedeckte Zeit


def test_sustained_and_short_episodes():
    def rh(x):
        return 75.0 if pd.Timestamp("2026-03-02 02:00") <= x < pd.Timestamp("2026-03-02 04:00") else (
            72.0 if pd.Timestamp("2026-03-02 06:00") <= x < pd.Timestamp("2026-03-02 06:30") else 50.0)
    df = series("2026-03-02 00:00", "2026-03-02 12:00", "10min", rh)
    eps = humidity.find_episodes(df, 60, AnalysisSettings())
    assert len(eps) == 2
    long, short = eps
    assert long.start == pd.Timestamp("2026-03-02 02:00", tz=TZ)
    assert long.end == pd.Timestamp("2026-03-02 04:00", tz=TZ)
    assert long.duration_s == 7200
    assert long.is_sustained
    assert long.max_rh == 75 and long.mean_rh == 75 and long.mean_temp == 20
    assert long.min_dew_point_spread == pytest.approx(20 - 15.4, abs=0.1)
    assert short.duration_s == 1800 and not short.is_sustained
    summ = humidity.episode_summary(eps, AnalysisSettings(), covered_s=12 * 3600)
    assert summ.count_sustained == 1 and summ.count_short == 1
    assert summ.longest_duration_s == 7200
    assert summ.days_with_sustained == 1
    assert summ.sustained_per_week == pytest.approx(1 / (12 / 168))
    json.dumps(summ.to_dict())


def test_short_interruptions_are_merged():
    def rh(x):
        return 50.0 if pd.Timestamp("2026-03-02 03:00") <= x < pd.Timestamp("2026-03-02 03:20") else (
            75.0 if pd.Timestamp("2026-03-02 02:00") <= x < pd.Timestamp("2026-03-02 04:00") else 50.0)
    df = series("2026-03-02 00:00", "2026-03-02 08:00", "10min", rh)
    eps = humidity.find_episodes(df, 60)
    assert len(eps) == 1
    assert eps[0].duration_s == 7200
    assert eps[0].above_s == 6000
    assert eps[0].is_sustained


def test_long_interruptions_split():
    def rh(x):
        return 50.0 if pd.Timestamp("2026-03-02 03:00") <= x < pd.Timestamp("2026-03-02 03:40") else (
            75.0 if pd.Timestamp("2026-03-02 02:00") <= x < pd.Timestamp("2026-03-02 04:00") else 50.0)
    df = series("2026-03-02 00:00", "2026-03-02 08:00", "10min", rh)
    eps = humidity.find_episodes(df, 60)
    assert [e.duration_s for e in eps] == [3600, 1200]
    assert not any(e.is_sustained for e in eps)


def test_episode_ends_at_data_gap():
    df = series("2026-03-02 00:00", "2026-03-02 10:00", "10min",
                lambda x: 75.0 if pd.Timestamp("2026-03-02 02:00") <= x < pd.Timestamp("2026-03-02 07:00") else 50.0,
                drop=[("2026-03-02 03:00", "2026-03-02 06:00")])
    eps = humidity.find_episodes(df, 60)
    assert len(eps) == 2
    assert eps[0].interrupted_by_gap and not eps[1].interrupted_by_gap
    assert eps[0].end == pd.Timestamp("2026-03-02 03:00", tz=TZ)  # 02:50 + 10 min, nicht über die Lücke
    assert eps[1].starts_after_gap
    assert eps[0].duration_s == 3600


def test_episode_at_data_edges_and_empty():
    df = series("2026-03-02 00:00", "2026-03-02 03:00", "10min", lambda x: 80.0)
    eps = humidity.find_episodes(df, 60)
    assert len(eps) == 1 and eps[0].at_period_start and eps[0].at_period_end
    empty = df.iloc[0:0]
    assert humidity.find_episodes(empty, 60) == []
    assert humidity.band_shares(empty).covered_hours == 0
    assert humidity.episode_summary([], AnalysisSettings()).count_total == 0
    json.dumps(humidity.analyze_humidity(empty).to_dict())
    json.dumps(humidity.analyze_humidity(df.iloc[:1]).to_dict())


def test_day_night_shares():
    # 2 volle Tage, nachts (22–6) 70 %, tagsüber 50 %
    df = series("2026-03-02 00:00", "2026-03-04 00:00", "30min",
                lambda x: 70.0 if (x.hour >= 22 or x.hour < 6) else 50.0)
    dn = humidity.day_night_shares(df, AnalysisSettings())
    assert dn.night.covered_hours == 16
    assert dn.day.covered_hours == 32
    assert dn.night.pct_elevated == 100 and dn.day.pct_elevated == 0
    assert dn.night.mean_rh == 70


def test_episode_performance():
    n = 1_000_000
    ts = pd.date_range("2020-01-01", periods=n, freq="min", tz="UTC")
    rh = 55 + 8 * np.sin(np.arange(n) / 600.0)
    df = pd.DataFrame({TS: ts, TEMPERATURE: 20.0, HUMIDITY: rh})
    t0 = time.perf_counter()
    res = humidity.analyze_humidity(df)
    assert res.elevated_summary.count_total > 100
    assert time.perf_counter() - t0 < 20


def test_max_days_in_window():
    days = pd.to_datetime(["2026-03-01", "2026-03-05", "2026-03-07", "2026-03-20"]).date
    assert humidity.max_days_in_window(list(days), 7) == 3
    assert humidity.max_days_in_window(list(days), 5) == 2
    assert humidity.max_days_in_window([], 7) == 0

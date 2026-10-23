"""Tests für Intervallerkennung, Lücken und Abdeckung (exakt nachrechenbare Zahlen)."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from climate_dashboard.analysis.quality import assess_quality
from climate_dashboard.domain.models import TS, AnalysisSettings
from climate_dashboard.ingest.intervals import (
    INTERVAL_PRESETS,
    analyze_intervals,
    detect_interval,
    format_duration_de,
    snap_interval,
)
from tests.synthetic import make_measurements


def series(start: str, periods: int, freq: str) -> pd.Series:
    return pd.Series(pd.date_range(start, periods=periods, freq=freq, tz="UTC"))


def test_presets_order_and_values():
    assert list(INTERVAL_PRESETS.items()) == [
        ("Automatisch erkennen", None),
        ("1 Minute", 60),
        ("15 Minuten", 900),
        ("30 Minuten", 1800),
        ("60 Minuten", 3600),
        ("12 Stunden", 43200),
        ("24 Stunden", 86400),
    ]


@pytest.mark.parametrize(
    ("interval_min", "start", "end", "expected"),
    [
        (1, "2026-01-05", "2026-01-06", 60),
        (15, "2026-01-05", "2026-01-12", 900),
        (30, "2026-01-05", "2026-01-12", 1800),
        (60, "2026-01-05", "2026-01-19", 3600),
        (720, "2026-01-01", "2026-03-01", 43200),
        (1440, "2026-01-01", "2026-03-01", 86400),
    ],
)
def test_detect_regular_intervals(interval_min, start, end, expected):
    df = make_measurements(start, end, interval_min=interval_min)
    assert detect_interval(df[TS]) == expected
    report = analyze_intervals(df[TS])
    assert report.expected_s == expected
    assert report.expected_source == "auto"
    assert report.completeness_pct == pytest.approx(100.0)
    assert report.time_coverage_pct == pytest.approx(100.0)
    assert report.gaps == []
    assert report.share_irregular_pct == 0.0


def test_detect_irregular_with_dropouts():
    df = make_measurements("2026-01-05", "2026-01-19", interval_min=30, drop_prob=0.25, seed=7)
    assert detect_interval(df[TS]) == 1800
    report = analyze_intervals(df[TS])
    assert 70 < report.completeness_pct < 80  # ~75 % der Messungen vorhanden
    assert report.share_irregular_pct > 20


def test_detect_with_jitter_snaps_to_typical():
    base = series("2026-01-01", 500, "10min")
    rng = np.random.default_rng(3)
    jitter = pd.to_timedelta(rng.integers(-20, 21, len(base)), unit="s")
    assert detect_interval(base + jitter) == 600


def test_detect_unsorted_and_duplicate_input():
    ts = series("2026-01-01", 10, "15min")
    shuffled = pd.concat([ts.iloc[::-1], ts.iloc[:3]])
    assert detect_interval(shuffled) == 900
    assert analyze_intervals(shuffled).n == 10


def test_detect_too_few_points():
    assert detect_interval(series("2026-01-01", 1, "15min")) is None
    assert detect_interval(pd.Series([], dtype="datetime64[ns, UTC]")) is None
    report = analyze_intervals(pd.Series([], dtype="datetime64[ns, UTC]"))
    assert report.n == 0 and report.expected_s is None and report.completeness_pct is None


def test_snap_interval():
    assert snap_interval(610) == 600
    assert snap_interval(1700) == 1800
    assert snap_interval(5400) == 5400  # 90 min ist kein typischer Wert → volle Minuten
    assert snap_interval(45) == 45


def _gap_series() -> pd.Series:
    """10-Minuten-Reihe 00:00–10:00 UTC (61 Punkte) mit
    * Lücke 02:00 → 05:00 (17 Punkte fehlen, 3 h) und
    * kurzem Ausfall 07:00 → 07:50 (4 Punkte fehlen, 50 min < Lückengrenze 60 min)."""
    ts = series("2026-01-01 00:00", 61, "10min")
    drop = (
        (ts > pd.Timestamp("2026-01-01 02:00", tz="UTC")) & (ts < pd.Timestamp("2026-01-01 05:00", tz="UTC"))
    ) | (
        (ts > pd.Timestamp("2026-01-01 07:00", tz="UTC")) & (ts < pd.Timestamp("2026-01-01 07:50", tz="UTC"))
    )
    return ts[~drop].reset_index(drop=True)


SETTINGS_60 = AnalysisSettings(min_gap_minutes=60)


def test_gaps_and_coverage_exact():
    ts = _gap_series()
    assert len(ts) == 40
    r = analyze_intervals(ts, settings=SETTINGS_60)  # erkannt: 10 min, Grenze max(3 × 10 min, 60 min)
    assert r.detected_s == 600 and r.expected_s == 600
    assert r.gap_threshold_s == 3600
    assert r.start == pd.Timestamp("2026-01-01 00:00", tz="UTC")
    assert r.end == pd.Timestamp("2026-01-01 10:00", tz="UTC")
    assert len(r.gaps) == 1
    gap = r.gaps[0]
    assert gap.start == pd.Timestamp("2026-01-01 02:00", tz="UTC")
    assert gap.end == pd.Timestamp("2026-01-01 05:00", tz="UTC")
    assert gap.duration_s == 10800
    assert r.longest_gap == gap
    # Zeitraum = 10 h + 1 Intervall = 36600 s; abgedeckt = 25200 s reguläre Abstände
    # + 600 s (Messung vor der Lücke, nominal) + 600 s (letzte Messung) = 26400 s
    assert r.time_coverage_pct == pytest.approx(26400 / 36600 * 100)
    assert r.expected_count == 61  # 36600 / 600
    assert r.completeness_pct == pytest.approx(40 / 61 * 100)
    assert r.share_irregular_pct == pytest.approx(2 / 39 * 100)  # 3 h und 50 min von 39 Abständen
    assert r.observed_median_s == 600
    assert r.max_spacing_s == 10800


def test_default_settings_use_180_minute_floor():
    r = analyze_intervals(_gap_series())  # Grenze max(30 min, 180 min) = 3 h → 3 h ist keine Lücke
    assert r.gap_threshold_s == 10800
    assert r.gaps == []
    assert r.time_coverage_pct == pytest.approx(100.0)


def test_gap_factor_and_min_gap_parameters():
    ts = _gap_series()
    r = analyze_intervals(ts, settings=AnalysisSettings(gap_factor=3.0, min_gap_minutes=30))
    assert [g.duration_s for g in r.gaps] == [10800, 3000]  # Grenze 30 min → auch 50 min ist Lücke
    assert r.time_coverage_pct == pytest.approx((36000 - 13800 + 2 * 600 + 600) / 36600 * 100)


def test_user_interval_overrides_and_deviation():
    ts = _gap_series()
    r = analyze_intervals(ts, expected_s=3600, settings=SETTINGS_60)
    assert r.expected_s == 3600 and r.expected_source == "user" and r.detected_s == 600
    assert r.gap_threshold_s == 10800
    assert r.gaps == []  # 3 h ist nicht > 3 h
    assert r.expected_count == 11  # (36000 + 3600) / 3600
    assert r.completeness_pct == 100.0  # gedeckelt
    assert r.deviates_from_expected is True
    assert "1 Stunde" in r.deviation_text and "10 Minuten" in r.deviation_text


@pytest.mark.parametrize("expected_s", [None, 600, 900, 3600])
@pytest.mark.parametrize(
    "settings", [AnalysisSettings(), SETTINGS_60, AnalysisSettings(gap_factor=2, min_gap_minutes=20)]
)
def test_same_numbers_as_analysis_quality(expected_s, settings):
    """Import und Analyse zeigen für dieselbe Reihe identische Abdeckung (single source of truth)."""
    for ts in (
        _gap_series(),
        make_measurements("2026-01-05", "2026-01-19", interval_min=30, drop_prob=0.2)[TS],
    ):
        r = analyze_intervals(ts, expected_s=expected_s, settings=settings)
        q = assess_quality(pd.DataFrame({TS: ts}), expected_s=expected_s or r.detected_s, settings=settings)
        assert r.time_coverage_pct == pytest.approx(q.time_coverage_pct, abs=1e-9)
        assert r.completeness_pct == pytest.approx(q.completeness_pct, abs=1e-9)
        assert r.gap_threshold_s == q.gap_threshold_s
        assert len(r.gaps) == q.gap_count
        if expected_s is None:  # automatische Erkennung stimmt bei regelmäßigen Reihen überein
            assert q.expected_interval_s == r.detected_s
            q_auto = assess_quality(pd.DataFrame({TS: ts}), settings=settings)
            assert r.time_coverage_pct == pytest.approx(q_auto.time_coverage_pct, abs=1e-9)


def test_user_interval_matching_detected_is_not_deviation():
    r = analyze_intervals(series("2026-01-01", 100, "15min"), expected_s=900)
    assert r.deviates_from_expected is False and r.deviation_text is None


def test_interval_change_detected():
    first = series("2026-01-01", 3 * 7 * 144, "10min")  # 3 Wochen à 10 min
    second = pd.Series(
        pd.date_range(first.iloc[-1] + pd.Timedelta("30min"), periods=3 * 7 * 48, freq="30min")
    )
    r = analyze_intervals(pd.concat([first, second], ignore_index=True))
    assert [s.interval_s for s in r.interval_changes] == [600, 1800]
    assert r.interval_changes[0].start == first.iloc[0]
    assert r.interval_changes[-1].end == second.iloc[-1]
    switch = r.interval_changes[1].start
    assert abs((switch - second.iloc[0]).total_seconds()) <= 7 * 86400


def test_constant_interval_has_no_changes():
    r = analyze_intervals(series("2026-01-01", 6 * 7 * 48, "30min"))
    assert r.interval_changes == []


def test_timestamps_are_not_modified():
    ts = _gap_series()
    before = ts.copy()
    analyze_intervals(ts, expected_s=600)
    pd.testing.assert_series_equal(ts, before)


def test_to_dict_is_json_serializable():
    d = analyze_intervals(_gap_series(), settings=SETTINGS_60).to_dict()
    json.dumps(d)
    assert d["gap_count"] == 1 and d["gaps"][0]["duration_s"] == 10800.0
    assert d["start"] == "2026-01-01T00:00:00+00:00"


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (30, "30 Sekunden"),
        (60, "1 Minute"),
        (1800, "30 Minuten"),
        (3600, "1 Stunde"),
        (5400, "1 Stunde 30 Minuten"),
        (43200, "12 Stunden"),
        (86400, "1 Tag"),
        (97200, "1 Tag 3 Stunden"),
    ],
)
def test_format_duration_de(seconds, text):
    assert format_duration_de(seconds) == text


def test_single_deviating_week_is_not_a_change():
    a = series("2026-01-01", 3 * 7 * 48, "30min")
    b = pd.Series(pd.date_range(a.iloc[-1] + pd.Timedelta("10min"), periods=7 * 144, freq="10min"))
    c = pd.Series(pd.date_range(b.iloc[-1] + pd.Timedelta("30min"), periods=3 * 7 * 48, freq="30min"))
    r = analyze_intervals(pd.concat([a, b, c], ignore_index=True))
    assert r.interval_changes == []
    assert r.detected_s == 1800

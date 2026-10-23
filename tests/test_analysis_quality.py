import json

import numpy as np
import pandas as pd
import pytest

from climate_dashboard.analysis import quality
from climate_dashboard.domain.models import HUMIDITY, TEMPERATURE, TS, AnalysisSettings


def local_ts(times, tz="Europe/Berlin") -> pd.Series:
    return pd.Series(pd.DatetimeIndex(times).tz_localize(tz).tz_convert("UTC"))


def frame_from(times, rh=50.0, t=20.0) -> pd.DataFrame:
    ts = local_ts(times)
    return pd.DataFrame({TS: ts, TEMPERATURE: t, HUMIDITY: rh})


def test_gap_threshold():
    s = AnalysisSettings(min_gap_minutes=60)
    assert quality.gap_threshold_s(600, s) == 3600  # 3×10 min < 60 min
    assert quality.gap_threshold_s(1800, s) == 5400
    assert quality.gap_threshold_s(None, s) == 3600
    # Standard: mindestens 3 h, damit einzelne ausgelassene Messungen keine Lücke sind
    default = AnalysisSettings()
    assert quality.gap_threshold_s(1800, default) == 10800
    assert quality.gap_threshold_s(7200, default) == 21600


def test_durations_irregular_intervals():
    ts = local_ts(["2026-03-02 00:00", "2026-03-02 00:01", "2026-03-02 00:02",
                   "2026-03-02 00:30", "2026-03-02 01:00"])
    w = quality.durations(ts, 5400, nominal_s=1800)
    assert list(w) == [60, 60, 1680, 1800, 1800]


def test_durations_gap_counts_only_nominal():
    ts = local_ts(["2026-03-02 00:00", "2026-03-02 00:30", "2026-03-02 05:00"])
    w = quality.durations(ts, 5400)
    assert list(w) == [1800, 1800, 1800]  # Lücke 00:30→05:00: nur nominales Intervall
    assert list(quality.gap_flags(ts, 5400)) == [False, True, False]


def test_durations_clipped_to_period_end():
    ts = local_ts(["2026-03-02 00:00", "2026-03-02 00:30"])
    w = quality.durations(ts, 5400, nominal_s=1800, period_end=pd.Timestamp("2026-03-02 00:40", tz="Europe/Berlin"))
    assert list(w) == [1800, 600]


def test_resolve_expected_interval():
    times = list(pd.date_range("2026-03-02", periods=50, freq="10min"))
    times += [pd.Timestamp("2026-03-02 12:00"), pd.Timestamp("2026-03-02 12:01")]
    ts = local_ts(times)
    assert quality.resolve_expected_interval(ts) == 600
    assert quality.resolve_expected_interval(ts, 300) == 300
    assert quality.resolve_expected_interval(ts.iloc[:1]) is None


def _day_with_gap() -> pd.DataFrame:
    times = pd.date_range("2026-03-02 00:00", "2026-03-02 23:30", freq="30min")
    times = times[(times < "2026-03-02 08:00") | (times >= "2026-03-02 14:00")]
    return frame_from(times)


def test_quality_report_inside_gap():
    df = _day_with_gap()
    start = pd.Timestamp("2026-03-02", tz="Europe/Berlin")
    rep = quality.assess_quality(df, start, start + pd.Timedelta(days=1), 1800, AnalysisSettings())
    assert rep.n == 36
    assert rep.expected_interval_source == "vorgegeben"
    assert rep.covered_s == 36 * 1800
    assert rep.time_coverage_pct == pytest.approx(75.0)
    assert rep.completeness_pct == pytest.approx(75.0)
    assert rep.gap_count == 1
    gap = rep.gaps[0]
    assert gap.position == "inside"
    assert gap.duration_s == 6.5 * 3600
    assert gap.start == pd.Timestamp("2026-03-02 07:30", tz="Europe/Berlin")
    assert rep.longest_gap_s == 6.5 * 3600
    assert rep.rating == "eingeschränkt"
    d = rep.to_dict()
    json.dumps(d)
    assert d["period_start"] == "2026-03-02T00:00:00+01:00"


def test_quality_gaps_at_period_edges_count():
    times = pd.date_range("2026-03-02 06:00", "2026-03-02 17:30", freq="30min")
    df = frame_from(times)
    start = pd.Timestamp("2026-03-02", tz="Europe/Berlin")
    rep = quality.assess_quality(df, start, start + pd.Timedelta(days=1), 1800)
    positions = [g.position for g in rep.gaps]
    assert positions == ["start", "end"]
    assert rep.gaps[0].duration_s == 6 * 3600
    assert rep.gaps[1].duration_s == 6.5 * 3600
    assert rep.time_coverage_pct == pytest.approx(50.0)
    assert rep.rating == "unzureichend"


def test_quality_full_day_is_good():
    times = pd.date_range("2026-03-02 00:00", "2026-03-02 23:30", freq="30min")
    rep = quality.assess_quality(frame_from(times), expected_s=None)
    assert rep.expected_interval_source == "erkannt"
    assert rep.expected_interval_s == 1800
    assert rep.time_coverage_pct == pytest.approx(100.0)
    assert rep.rating == "gut"
    assert rep.gaps == []


def test_quality_empty_and_single():
    empty = pd.DataFrame({TS: pd.Series(pd.DatetimeIndex([], tz="UTC")), HUMIDITY: pd.Series(dtype=float)})
    rep = quality.assess_quality(empty)
    assert rep.n == 0 and rep.rating == "unzureichend"
    json.dumps(rep.to_dict())
    start = pd.Timestamp("2026-03-02", tz="Europe/Berlin")
    rep = quality.assess_quality(empty, start, start + pd.Timedelta(days=1))
    assert rep.gaps[0].position == "all" and rep.time_coverage_pct == 0
    rep = quality.assess_quality(frame_from(["2026-03-02 10:00"]))
    assert rep.n == 1 and rep.rating == "unzureichend"
    json.dumps(rep.to_dict())
    rep = quality.assess_quality(None)
    assert rep.n == 0


def test_jsonable_handles_nan_and_numpy():
    out = quality.jsonable({"a": np.float64("nan"), "b": np.int64(3), "c": pd.NaT,
                            "d": pd.Timestamp("2026-07-01 12:00", tz="Europe/Berlin")})
    assert out == {"a": None, "b": 3, "c": None, "d": "2026-07-01T12:00:00+02:00"}

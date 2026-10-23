import json
import time

import numpy as np
import pandas as pd
import pytest

from climate_dashboard.analysis import stats
from climate_dashboard.domain.models import DEW_POINT, HUMIDITY, METRICS, TEMPERATURE, TS


def frame(rows, tz="Europe/Berlin") -> pd.DataFrame:
    """rows: (lokale Zeit, Temperatur, rF)."""
    ts = pd.DatetimeIndex([r[0] for r in rows]).tz_localize(tz).tz_convert("UTC")
    return pd.DataFrame({TS: ts, TEMPERATURE: [float(r[1]) for r in rows], HUMIDITY: [float(r[2]) for r in rows]})


def test_time_weighting_differs_from_counting():
    # 2 × 30 min mit 40 %, dann 10 Messungen im Minutentakt mit 80 %, dann 3 × 30 min mit 40 %
    rows = [("2026-03-02 00:00", 20, 40), ("2026-03-02 00:30", 20, 40)]
    rows += [(f"2026-03-02 01:0{i}", 20, 80) for i in range(10)]
    rows += [("2026-03-02 01:10", 20, 40), ("2026-03-02 01:40", 20, 40), ("2026-03-02 02:10", 20, 40)]
    df = frame(rows)
    s = stats.summarize(df, (HUMIDITY,), expected_s=1800)[HUMIDITY]
    assert s.covered_s == 5 * 1800 + 600
    assert s.mean == pytest.approx((9000 * 40 + 600 * 80) / 9600)  # 42,5
    assert df[HUMIDITY].mean() == pytest.approx(66.67, abs=0.01)  # Zählung wäre stark verzerrt
    assert s.median == 40
    assert s.max == 80
    assert s.max_time == pd.Timestamp("2026-03-02 01:00", tz="Europe/Berlin")
    assert s.count == 15
    assert s.time_weighted


def test_gaps_do_not_count_as_time():
    rows = [("2026-03-02 00:00", 20, 40), ("2026-03-02 00:30", 20, 40), ("2026-03-02 01:00", 20, 40),
            ("2026-03-02 12:00", 20, 80), ("2026-03-02 12:30", 20, 80)]
    s = stats.summarize(frame(rows), (HUMIDITY,))[HUMIDITY]
    assert s.covered_s == 5 * 1800
    assert s.mean == pytest.approx(56.0)


def test_weighted_quantile():
    v = np.array([1.0, 2.0, 3.0, 4.0])
    assert stats.weighted_quantile(v, np.ones(4), 0.5) == 2.0
    assert stats.weighted_quantile(v, np.array([0, 0, 0, 10.0]), 0.05) == 4.0
    assert stats.weighted_quantile(v, np.array([10.0, 1, 1, 1]), 0.95) == 4.0
    assert stats.weighted_quantile(v, np.array([10.0, 1, 1, 1]), 0.5) == 1.0


def test_calculated_source_when_only_t_rh():
    rows = [("2026-03-02 00:00", 20, 50), ("2026-03-02 00:30", 20, 50)]
    s = stats.summarize(frame(rows))
    assert s[DEW_POINT].source == "calculated"
    assert s[DEW_POINT].calculated_pct == 100.0
    assert s[DEW_POINT].mean == pytest.approx(9.255, abs=1e-3)
    assert s[TEMPERATURE].source == "measured"
    json.dumps({k: v.to_dict() for k, v in s.items()})


def test_empty_and_single_measurement():
    empty = pd.DataFrame({TS: pd.Series(pd.DatetimeIndex([], tz="UTC"))})
    out = stats.summarize(empty)
    assert set(out) == set(METRICS)
    assert all(v.count == 0 and v.mean is None for v in out.values())
    json.dumps({k: v.to_dict() for k, v in out.items()})
    one = stats.summarize(frame([("2026-03-02 10:00", 21.5, 47)]))
    assert one[TEMPERATURE].mean == 21.5
    assert one[TEMPERATURE].min == one[TEMPERATURE].max == 21.5
    assert one[TEMPERATURE].count == 1
    json.dumps({k: v.to_dict() for k, v in one.items()})
    assert stats.summarize(None)[HUMIDITY].count == 0


def test_period_restriction_and_unsorted_input():
    rows = [("2026-03-02 01:00", 22, 60), ("2026-03-02 00:00", 20, 40), ("2026-03-02 00:30", 21, 50)]
    df = frame(rows)
    s = stats.summarize(df, (TEMPERATURE,), period_start=pd.Timestamp("2026-03-02 00:15", tz="Europe/Berlin"))
    assert s[TEMPERATURE].count == 2
    assert s[TEMPERATURE].min == 21


def test_performance_one_million_rows():
    n = 1_000_000
    ts = pd.date_range("2020-01-01", periods=n, freq="min", tz="UTC")
    rng = np.random.default_rng(0)
    df = pd.DataFrame({TS: ts, TEMPERATURE: 20 + rng.normal(0, 1, n), HUMIDITY: 50 + rng.normal(0, 3, n)})
    t0 = time.perf_counter()
    prep = stats.prepare(df)
    out = stats.summarize(prep)
    elapsed = time.perf_counter() - t0
    assert out[HUMIDITY].count == n
    assert elapsed < 15

import json

import numpy as np
import pandas as pd
import pytest

from climate_dashboard.analysis import periods
from climate_dashboard.domain.models import HUMIDITY, TEMPERATURE, TS, AnalysisSettings

TZ = "Europe/Berlin"


def utc_series(start_local, end_local, freq="30min", rh=50.0, t=20.0, drop=None) -> pd.DataFrame:
    """Regelmäßige Messungen in UTC zwischen zwei lokalen Zeitpunkten (DST-sicher)."""
    s = pd.Timestamp(start_local, tz=TZ).tz_convert("UTC")
    e = pd.Timestamp(end_local, tz=TZ).tz_convert("UTC")
    ts = pd.date_range(s, e, freq=freq, inclusive="left")
    if drop:
        for a, b in drop:
            ts = ts[(ts < pd.Timestamp(a, tz=TZ)) | (ts >= pd.Timestamp(b, tz=TZ))]
    rh_v = rh(ts) if callable(rh) else rh
    t_v = t(ts) if callable(t) else t
    return pd.DataFrame({TS: ts, TEMPERATURE: t_v, HUMIDITY: rh_v})


def test_daily_aggregate_dst_spring_and_autumn():
    spring = periods.aggregate(utc_series("2026-03-28", "2026-03-31"), "D", TZ)
    assert list(spring["hours"]) == [24, 23, 24]
    assert list(spring["n"]) == [48, 46, 48]
    assert np.allclose(spring["coverage_pct"], 100)
    autumn = periods.aggregate(utc_series("2026-10-24", "2026-10-27"), "D", TZ)
    assert list(autumn["hours"]) == [24, 25, 24]
    assert list(autumn["n"]) == [48, 50, 48]
    assert np.allclose(autumn["coverage_pct"], 100)
    assert str(autumn["start"].iloc[1]) == "2026-10-25 00:00:00+02:00"
    assert str(autumn["start"].iloc[2]) == "2026-10-26 00:00:00+01:00"


def test_hourly_aggregate_keeps_empty_windows_nan():
    df = utc_series("2026-03-02 00:00", "2026-03-02 06:00", drop=[("2026-03-02 02:00", "2026-03-02 04:00")])
    agg = periods.aggregate(df, "h", TZ)
    assert len(agg) == 6
    assert list(agg["n"]) == [2, 2, 0, 0, 2, 2]
    assert agg["humidity_mean"].isna().tolist() == [False, False, True, True, False, False]
    assert agg["coverage_pct"].iloc[2] == 0
    recs = periods.aggregate_records(agg)
    json.dumps(recs)
    assert recs[2]["humidity_mean"] is None


def test_aggregate_time_weighted_mean_per_window():
    df = pd.DataFrame({
        TS: pd.DatetimeIndex(["2026-03-02 00:00", "2026-03-02 00:50", "2026-03-02 00:55"]).tz_localize(TZ).tz_convert("UTC"),
        TEMPERATURE: [20.0, 30.0, 30.0],
        HUMIDITY: [50.0, 50.0, 50.0],
    })
    agg = periods.aggregate(df, "h", TZ, expected_s=300)
    # 20 °C für 50 min, 30 °C für 2 × 5 min
    assert agg["temperature_mean"].iloc[0] == pytest.approx((20 * 50 + 30 * 10) / 60)
    assert agg["temperature_max"].iloc[0] == 30


def test_weekly_and_monthly_windows():
    df = utc_series("2026-03-02", "2026-04-06")
    weeks = periods.aggregate(df, "W", TZ)
    assert str(weeks["start"].iloc[0]) == "2026-03-02 00:00:00+01:00"  # Montag
    assert (weeks["start"].dt.day_of_week == 0).all()
    months = periods.aggregate(df, "MS", TZ)
    assert [str(s)[:10] for s in months["start"]] == ["2026-03-01", "2026-04-01"]
    assert months["hours"].iloc[0] == 31 * 24 - 1


def test_monthly_overview_complete_vs_incomplete():
    df = utc_series("2026-03-01", "2026-05-01", rh=lambda ts: np.where(ts.hour % 2 == 0, 65.0, 50.0),
                    drop=[("2026-04-10", "2026-04-20")])
    months = periods.monthly_overview(df, AnalysisSettings(), 1800)
    assert [m.month for m in months] == ["2026-03", "2026-04"]
    mar, apr = months
    assert mar.complete and mar.time_coverage_pct == pytest.approx(100)
    assert not apr.complete
    assert apr.time_coverage_pct == pytest.approx(20 / 30 * 100, abs=0.2)
    assert any("Zeitabdeckung" in r for r in apr.incomplete_reasons)
    assert mar.pct_elevated == pytest.approx(50, abs=0.5)
    assert mar.label == "März 2026"
    json.dumps([m.to_dict() for m in months])


def test_monthly_overview_partial_month():
    months = periods.monthly_overview(utc_series("2026-03-15", "2026-04-01"), AnalysisSettings())
    assert len(months) == 1
    assert not months[0].complete
    assert any("Monatsanfang" in r for r in months[0].incomplete_reasons)


def test_seasonal_overview_insufficient_and_winter_label():
    spring = periods.seasonal_overview(utc_series("2026-03-01", "2026-04-15"), AnalysisSettings())
    assert len(spring) == 1
    assert spring[0].label == "Frühling 2026"
    assert not spring[0].sufficient and spring[0].partial
    assert spring[0].reasons
    winter = periods.seasonal_overview(utc_series("2025-12-01", "2026-03-01"), AnalysisSettings())
    assert [w.label for w in winter] == ["Winter 2025/26"]
    assert winter[0].sufficient and not winter[0].partial
    json.dumps([w.to_dict() for w in winter])


def test_day_night():
    df = utc_series("2026-03-02", "2026-03-04", t=lambda ts: np.where(
        (ts.tz_convert(TZ).hour >= 22) | (ts.tz_convert(TZ).hour < 6), 18.0, 21.0))
    dn = periods.day_night(df, AnalysisSettings())
    assert dn.night.means[TEMPERATURE] == 18 and dn.day.means[TEMPERATURE] == 21
    assert dn.night_minus_day[TEMPERATURE] == -3
    assert dn.night.covered_hours == 16
    json.dumps(dn.to_dict())


def test_compare_periods():
    a = utc_series("2026-03-02", "2026-03-09", rh=50.0, t=20.0)
    b = utc_series("2026-03-09", "2026-03-23", freq="60min", rh=55.0, t=21.0)
    res = periods.compare_periods(a, b, AnalysisSettings(), labels=("März A", "März B"))
    t = res.item("temperature_mean")
    assert t.delta == pytest.approx(1.0) and t.delta_unit == "K" and t.delta_pct is None
    h = res.item("humidity_mean")
    assert h.delta == pytest.approx(5.0) and h.delta_unit == "Prozentpunkte" and h.delta_pct is None
    ah = res.item("abs_humidity_mean")
    assert ah.delta_pct == pytest.approx((ah.b - ah.a) / ah.a * 100)
    assert any("unterschiedlich lang" in c for c in res.caveats)
    assert any("Messintervalle" in c for c in res.caveats)
    json.dumps(res.to_dict())


def test_compare_with_empty_period():
    a = utc_series("2026-03-02", "2026-03-09")
    res = periods.compare_periods(a, a.iloc[0:0])
    assert res.item("humidity_mean").delta is None
    assert any("zu wenige" in c for c in res.caveats)


def test_period_helpers():
    s, e = periods.period_bounds("week", "2026-10-22", TZ)
    assert (str(s), str(e)) == ("2026-10-19 00:00:00+02:00", "2026-10-26 00:00:00+01:00")
    s, e = periods.period_bounds("day", pd.Timestamp("2026-10-25 12:00", tz=TZ), TZ)
    assert (e - s) == pd.Timedelta(hours=25)
    s, e = periods.period_bounds("month", "2026-10-22", TZ)
    assert str(s) == "2026-10-01 00:00:00+02:00" and str(e) == "2026-11-01 00:00:00+01:00"
    s, e = periods.previous_period("month", "2026-03-15", TZ)
    assert str(s)[:10] == "2026-02-01" and str(e)[:10] == "2026-03-01"
    s, e = periods.period_bounds("year", "2026-05-05", TZ)
    assert str(s)[:10] == "2026-01-01" and str(e)[:10] == "2027-01-01"
    py = periods.same_period_previous_year(*periods.period_bounds("month", "2026-10-22", TZ), TZ)
    assert str(py[0]) == "2025-10-01 00:00:00+02:00"
    w = periods.equal_length_window(pd.Timestamp("2026-03-10", tz=TZ), pd.Timestamp("2026-03-17", tz=TZ), TZ)
    assert w[1] - w[0] == pd.Timedelta(days=7) and w[1] == pd.Timestamp("2026-03-10", tz=TZ)


def test_empty_inputs():
    empty = utc_series("2026-03-02", "2026-03-02")
    assert periods.monthly_overview(empty) == []
    assert periods.seasonal_overview(empty) == []
    assert len(periods.aggregate(empty, "D")) == 0
    json.dumps(periods.day_night(empty).to_dict())
    one = utc_series("2026-03-02 10:00", "2026-03-02 10:30")
    assert len(periods.monthly_overview(one)) == 1
    json.dumps([m.to_dict() for m in periods.monthly_overview(one)])

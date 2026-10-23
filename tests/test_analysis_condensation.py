import json

import numpy as np
import pandas as pd
import pytest

from climate_dashboard.analysis import condensation
from climate_dashboard.domain.models import HUMIDITY, TEMPERATURE, TS, AnalysisSettings

TZ = "Europe/Berlin"


def const(rh: float, t: float = 20.0, hours: int = 12) -> pd.DataFrame:
    local = pd.date_range("2026-03-02 00:00", periods=hours * 2, freq="30min")
    return pd.DataFrame({TS: local.tz_localize(TZ).tz_convert("UTC"), TEMPERATURE: t, HUMIDITY: rh})


def test_spread_statistics_without_surface():
    res = condensation.analyze_condensation(const(50.0), AnalysisSettings())
    assert res.spread_mean == pytest.approx(20 - 9.255, abs=1e-3)
    assert res.pct_below_warn == 0
    assert res.surface is None
    assert condensation.NO_SURFACE_NOTE in res.limitations
    json.dumps(res.to_dict())


def test_low_spread_shares_and_phases():
    res = condensation.analyze_condensation(const(85.0), AnalysisSettings())  # Td ≈ 17,4 → Abstand 2,6 K
    assert res.pct_below_warn == 100
    assert res.pct_below_1k == 0
    assert len(res.low_spread_phases) == 1
    assert res.low_spread_phases[0].duration_s == 12 * 3600
    assert res.surface is None  # keine Kondensationsbehauptung ohne Oberflächenwert


def test_assumed_surface_offset():
    df = const(60.0)  # Td ≈ 12,0 °C
    mild = condensation.analyze_condensation(df, surface_offset_k=5.0)  # Oberfläche 15 °C
    assert mild.surface.basis == "assumed"
    assert "ANNAHME" in mild.surface.assumption
    assert mild.surface.hours_at_or_below_dew_point == 0
    assert mild.surface.pct_surface_rh_critical == 100  # ≈ 82 % an der Oberfläche
    assert mild.surface.max_surface_rh == pytest.approx(82.2, abs=0.1)
    cold = condensation.analyze_condensation(df, AnalysisSettings(surface_offset_k=9.0))  # 11 °C
    assert cold.surface.pct_at_or_below_dew_point == 100
    assert cold.surface.max_surface_rh == 100
    assert cold.surface.min_surface_minus_dew_point == pytest.approx(-1.0, abs=0.01)
    json.dumps(cold.to_dict())


def test_measured_surface_series():
    df = const(60.0)
    surf = pd.Series(np.where(np.arange(len(df)) < 12, 11.0, 16.0), index=pd.DatetimeIndex(df[TS]))
    res = condensation.analyze_condensation(df, surface_temperature=surf, surface_offset_k=1.0)
    assert res.surface.basis == "measured"
    assert res.surface.offset_k is None
    assert res.surface.hours_at_or_below_dew_point == 6
    assert res.surface.pct_at_or_below_dew_point == 50


def test_empty():
    res = condensation.analyze_condensation(const(50.0).iloc[0:0], surface_offset_k=5.0)
    assert res.covered_hours == 0 and res.surface is None
    json.dumps(res.to_dict())

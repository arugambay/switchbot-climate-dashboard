import json
import math

import numpy as np
import pandas as pd
import pytest

from climate_dashboard.analysis import physics
from climate_dashboard.domain.models import ABS_HUMIDITY, DEW_POINT, HUMIDITY, TEMPERATURE, TS, VPD
from tests.synthetic import make_measurements


def test_reference_values_20c_50pct():
    assert physics.saturation_vapor_pressure_kpa(20.0) == pytest.approx(2.3326, abs=1e-3)
    assert physics.dew_point(20.0, 50.0) == pytest.approx(9.26, abs=0.02)
    assert round(physics.dew_point(20.0, 50.0), 1) == 9.3
    assert physics.absolute_humidity(20.0, 50.0) == pytest.approx(8.62, abs=0.02)
    assert physics.vpd(20.0, 50.0) == pytest.approx(1.166, abs=0.002)


def test_reference_dew_point_19_2_57():
    assert round(physics.dew_point(19.2, 57.0), 1) == 10.5


def test_saturation_at_100pct_dew_point_equals_temperature():
    t = np.array([-10.0, 0.0, 15.0, 30.0])
    np.testing.assert_allclose(physics.dew_point(t, 100.0), t, atol=1e-9)
    np.testing.assert_allclose(physics.vpd(t, 100.0), 0.0, atol=1e-12)


def test_relative_humidity_roundtrip():
    t = np.array([5.0, 18.3, 22.7, 35.0])
    rh = np.array([30.0, 55.0, 80.0, 95.0])
    np.testing.assert_allclose(physics.relative_humidity(t, physics.dew_point(t, rh)), rh, rtol=1e-9)


def test_relative_humidity_above_100_when_below_dew_point():
    td = physics.dew_point(20.0, 60.0)
    assert physics.relative_humidity(td - 1.0, td) > 100.0


def test_invalid_inputs_give_nan():
    assert math.isnan(physics.dew_point(20.0, 0.0))
    assert math.isnan(physics.dew_point(20.0, -5.0))
    assert math.isnan(physics.dew_point(20.0, 101.0))
    assert math.isnan(physics.saturation_vapor_pressure_kpa(70.0))
    assert math.isnan(physics.saturation_vapor_pressure_kpa(-50.0))
    assert math.isnan(physics.absolute_humidity(np.nan, 50.0))


def test_series_keep_index_and_scalars_are_float():
    t = pd.Series([20.0, 21.0], index=[5, 7])
    out = physics.dew_point(t, pd.Series([50.0, 60.0], index=[5, 7]))
    assert isinstance(out, pd.Series)
    assert list(out.index) == [5, 7]
    assert isinstance(physics.vpd(20.0, 50.0), float)
    arr = physics.vpd(np.array([20.0, 25.0]), 50.0)
    assert isinstance(arr, np.ndarray) and arr.shape == (2,)


def _only_t_rh() -> pd.DataFrame:
    ts = pd.date_range("2026-01-01", periods=3, freq="30min", tz="UTC")
    return pd.DataFrame({TS: ts, TEMPERATURE: [20.0, 21.0, np.nan], HUMIDITY: [50.0, 60.0, 55.0]})


def test_with_calculated_does_not_touch_originals():
    df = make_measurements("2026-03-01", "2026-03-02", 30)
    before = df.copy()
    out = physics.with_calculated(df)
    pd.testing.assert_frame_equal(df, before)
    for m in (DEW_POINT, VPD, ABS_HUMIDITY):
        pd.testing.assert_series_equal(out[m], before[m])
        assert f"calc_{m}" in out.columns
    assert out["calc_dew_point"].iloc[0] == pytest.approx(
        physics.dew_point(df[TEMPERATURE].iloc[0], df[HUMIDITY].iloc[0])
    )


def test_effective_with_missing_columns_marks_calculated():
    eff = physics.effective(_only_t_rh())
    assert eff[DEW_POINT].iloc[0] == pytest.approx(9.255, abs=1e-3)
    assert list(eff["dew_point_source"]) == ["calculated", "calculated", None]
    assert list(eff["temperature_source"]) == ["measured", "measured", None]
    assert math.isnan(eff[DEW_POINT].iloc[2])


def test_effective_prefers_measured_values():
    df = _only_t_rh()
    df[DEW_POINT] = [9.0, np.nan, np.nan]
    eff = physics.effective(df)
    assert eff[DEW_POINT].iloc[0] == 9.0
    assert eff["dew_point_source"].iloc[0] == "measured"
    assert eff["dew_point_source"].iloc[1] == "calculated"


def test_plausibility_ok_for_consistent_rounded_data():
    df = make_measurements("2026-03-01", "2026-03-08", 10)
    res = physics.plausibility(df)
    assert res.ok
    for m in (DEW_POINT, VPD, ABS_HUMIDITY):
        assert res.items[m].exceeding == 0
        assert res.items[m].compared == len(df)
    json.dumps(res.to_dict())


def test_plausibility_detects_deviations():
    df = make_measurements("2026-03-01", "2026-03-02", 10)
    df.loc[df.index[:20], DEW_POINT] += 2.0
    res = physics.plausibility(df)
    assert not res.ok
    assert res.items[DEW_POINT].exceeding == 20
    assert res.items[VPD].exceeding == 0

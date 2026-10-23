"""Tests für den Berichts-Snapshot (nur synthetische Daten)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from climate_dashboard.analysis.condensation import NO_SURFACE_NOTE
from climate_dashboard.domain.models import SENSOR_ID, AnalysisSettings, Room, Sensor
from climate_dashboard.reports.builder import (
    ALL_SECTIONS,
    SCHEMA_VERSION,
    ReportOptions,
    build_snapshot,
    data_fingerprint,
    series_times,
)
from tests.synthetic import make_measurements

ROOM = Room(id="room-0b7c2e5a-uuid", name="Schlafzimmer Süd")
SENSOR = Sensor(id="sensor-77aa31f0-uuid", room_id=ROOM.id, name="Hygrometer Fenster", device_info="Meter Pro")
START = datetime(2026, 1, 15, tzinfo=UTC)
END = datetime(2026, 3, 1, tzinfo=UTC)


def _df(**kw):
    df = make_measurements("2026-01-15", "2026-03-01", 30, **kw)
    df[SENSOR_ID] = SENSOR.id
    return df


def _snap(df=None, options=None, **kw):
    return build_snapshot(
        _df() if df is None else df, room=ROOM, sensors=[SENSOR], period_start=START, period_end=END,
        options=options or ReportOptions(title="Bericht"), settings=AnalysisSettings(), expected_s=1800,
        version=kw.pop("version", 1), **kw,
    )


@pytest.fixture(scope="module")
def snap():
    return _snap(_df(humid_spells=[("2026-02-01", "2026-02-02 12:00", 74)]))


def test_snapshot_complete_and_serializable(snap):
    assert snap["schema_version"] == SCHEMA_VERSION == 1
    for key in ("meta", "options", "quality", "kpis", "humidity", "condensation", "assessment", "monthly",
                "seasonal", "day_night", "series", "summary", "conclusion"):
        assert snap[key] is not None, key
    json.dumps(snap, allow_nan=False)
    meta = snap["meta"]
    assert meta["title"] == "Bericht" and meta["room_name"] == "Schlafzimmer Süd"
    assert meta["version"] == 1 and meta["timezone"] == "Europe/Berlin"
    assert len(meta["data_fingerprint"]) == 64
    assert meta["period_start"].startswith("2026-01-15")
    assert 3 <= len(snap["summary"]) <= 6
    assert snap["conclusion"]["headline"]


def test_no_internal_ids(snap):
    text = json.dumps(snap)
    for forbidden in (ROOM.id, SENSOR.id, "room_id", "sensor_id", "owner", "user_id"):
        assert forbidden not in text


def test_series_hourly_with_null_gaps():
    s = _snap(_df(gaps=[("2026-02-10", "2026-02-12")]))
    series = s["series"]
    assert series["resolution"] == "h"
    assert len(series_times(series)) == series["n"] == len(series["humidity"])
    assert None in series["humidity"]
    assert series_times(series)[1] - series_times(series)[0] == pd_hour()


def pd_hour():
    import pandas as pd

    return pd.Timedelta(hours=1)


def test_long_period_reduced_and_documented():
    df = make_measurements("2023-01-01", "2025-07-01", 120)
    s = build_snapshot(df, room=ROOM, sensors=[SENSOR], period_start=datetime(2023, 1, 1, tzinfo=UTC),
                       period_end=datetime(2025, 7, 1, tzinfo=UTC), options=ReportOptions(),
                       settings=AnalysisSettings(), expected_s=7200)
    assert s["series"]["resolution"] == "3h"
    assert s["series"]["note"]
    assert s["series"]["n"] < 24 * 366 * 2


def test_incomplete_months_flagged(snap):
    months = {m["month"]: m for m in snap["monthly"]}
    assert months["2026-01"]["complete"] is False
    assert months["2026-01"]["incomplete_reasons"]
    assert months["2026-02"]["complete"] is True


def test_missing_surface_temperature_is_limitation(snap):
    assert snap["condensation"]["surface"] is None
    assert NO_SURFACE_NOTE in snap["assessment"]["limitations"]


def test_surface_assumption_marked():
    s = _snap(options=ReportOptions(surface_offset_k=4))
    assert s["condensation"]["surface"]["basis"] == "assumed"
    assert "ANNAHME" in s["condensation"]["surface"]["assumption"]
    assert s["meta"]["thresholds"]["surface_offset_k"] == 4


def test_sensor_info_only_when_allowed(snap):
    assert snap["meta"]["sensor_info"] is None
    assert "Hygrometer Fenster" not in json.dumps(snap)
    s = _snap(options=ReportOptions(show_sensor_info=True))
    assert s["meta"]["sensor_info"] == [{"name": "Hygrometer Fenster", "device_info": "Meter Pro"}]


def test_sections_excluded():
    s = _snap(options=ReportOptions(include_sections=("kpis", "assessment")))
    assert s["monthly"] is None and s["seasonal"] is None and s["series"] is None
    assert s["options"]["include_sections"] == ["kpis", "assessment"]
    assert set(ReportOptions().include_sections) == set(ALL_SECTIONS)


def test_episodes_limited():
    spells = [(f"2026-02-{d:02d} 08:00", f"2026-02-{d:02d} 11:00", 72) for d in range(1, 26)]
    s = _snap(_df(humid_spells=spells))
    assert s["humidity"]["episodes_total"] >= 25
    assert len(s["humidity"]["episodes"]) == s["humidity"]["episodes_listed"] == 20


def test_fingerprint_stable_and_sensitive():
    df = _df()
    a = data_fingerprint(df, [SENSOR.id], START, END)
    assert a == data_fingerprint(df.sample(frac=1, random_state=3), [SENSOR.id], START, END)
    more = _df().iloc[: len(df) - 5]
    assert data_fingerprint(more, [SENSOR.id], START, END) != a
    changed = df.copy()
    changed.loc[10, "humidity"] += 1
    assert data_fingerprint(changed, [SENSOR.id], START, END) != a
    # Messungen außerhalb des Zeitraums ändern ihn nicht
    outside = make_measurements("2026-03-05", "2026-03-06", 30)
    outside[SENSOR_ID] = SENSOR.id
    import pandas as pd

    assert data_fingerprint(pd.concat([df, outside]), [SENSOR.id], START, END) == a


def test_empty_data_snapshot():
    s = _snap(df=make_measurements("2026-05-01", "2026-05-02", 30))  # außerhalb des Zeitraums
    json.dumps(s, allow_nan=False)
    assert s["quality"]["n"] == 0
    assert s["conclusion"]["status"] == "keine_daten"
    assert s["monthly"] == []

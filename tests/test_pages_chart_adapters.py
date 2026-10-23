"""Adapter Analyse-Ergebnis → Diagrammfabrik (``ui.charts``) mit echten Analyseausgaben."""

from __future__ import annotations

import json

import pandas as pd

from climate_dashboard.analysis.humidity import analyze_humidity
from climate_dashboard.analysis.periods import aggregate, day_night, monthly_overview, seasonal_overview
from climate_dashboard.analysis.stats import prepare
from climate_dashboard.domain.models import HUMIDITY, TEMPERATURE, TS, AnalysisSettings
from climate_dashboard.ui import charts
from tests.synthetic import make_measurements
from tests.test_pages_helpers import GAPS, LONG_END, LONG_START, SPELLS


def _prep():
    df = make_measurements(LONG_START, LONG_END, 30, gaps=GAPS, humid_spells=SPELLS)
    return prepare(df, AnalysisSettings(), 1800)


def test_monthly_and_seasonal_frames_from_analysis_and_snapshot_dicts():
    prep = _prep()
    months = monthly_overview(prep)
    frame = charts.monthly_frame(months, HUMIDITY)
    assert len(frame) == len(months) == 9
    assert not frame["complete"].iloc[0] and not frame["complete"].iloc[-1]  # Februar/Oktober angeschnitten
    assert frame["complete"].iloc[1:-1].all()
    # gleiche Ausgabe aus der JSON-Form eines Berichts-Snapshots
    as_dicts = json.loads(json.dumps([m.to_dict() for m in months]))
    pd.testing.assert_frame_equal(frame, charts.monthly_frame(as_dicts, HUMIDITY))
    fig = charts.monthly_chart(frame, HUMIDITY)
    assert fig.data

    seasons = seasonal_overview(prep)
    sframe = charts.seasonal_frame(seasons, TEMPERATURE)
    winter = sframe[sframe["season"] == "winter"].iloc[0]
    assert winter["year"] == 2026  # Winter 2025/26 → Konvention „Jahr, in dem er endet“
    assert charts.seasonal_chart(sframe, TEMPERATURE).data


def test_day_night_bands_episodes_and_aggregate_adapters():
    prep = _prep()
    dn = charts.day_night_frame(day_night(prep), HUMIDITY)
    assert list(dn["phase"]) == ["day", "night"] and dn["mean"].notna().all()
    assert charts.day_night_chart(dn, HUMIDITY).data
    dnm = charts.day_night_monthly_frame(monthly_overview(prep), TEMPERATURE)
    assert len(dnm) == 18

    hum = analyze_humidity(prep)
    values, labels = charts.band_shares_input(hum.bands)
    assert set(values) == {"trocken", "normal", "erhoeht", "hoch"}
    assert abs(sum(values.values()) - 1.0) < 1e-6
    assert charts.band_share_bar(values, labels=labels).data

    eps = charts.episodes_frame(hum.elevated_episodes, high_threshold=70)
    assert len(eps) == len(hum.elevated_episodes) and set(eps["level"]) <= {"hinweis", "auffaellig"}
    assert charts.episode_timeline(eps).data
    bands = charts.episode_bands(hum.elevated_episodes, high_threshold=70)
    assert bands and all(b["start"] < b["end"] for b in bands)

    agg = aggregate(prep, "D")
    series = charts.aggregate_frame(agg, [TEMPERATURE, HUMIDITY])
    assert list(series.columns) == [TS, TEMPERATURE, HUMIDITY]
    assert series[HUMIDITY].isna().any()  # Lückentage bleiben leer
    assert charts.timeseries_chart(series, [TEMPERATURE, HUMIDITY]).data

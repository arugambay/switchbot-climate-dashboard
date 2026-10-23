"""Tests für Diagrammfabriken, Theme und Komponenten-HTML (nur synthetische Daten)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from climate_dashboard.domain.models import DEW_POINT, HUMIDITY, TEMPERATURE, TS, VPD
from climate_dashboard.ui import charts, theme
from climate_dashboard.ui import components as ui
from tests.synthetic import make_measurements


@pytest.fixture(scope="module")
def week() -> pd.DataFrame:
    # 7 Tage à 30 Min., eine Lücke von 6 Stunden
    return make_measurements(gaps=[("2026-03-03 06:00", "2026-03-03 12:00")])


def _traces(fig: go.Figure, name: str) -> list:
    return [t for t in fig.data if t.name == name]


# ---------------------------------------------------------------------------
# Lücken
# ---------------------------------------------------------------------------
def test_gap_threshold_and_find_gaps(week):
    threshold = charts.estimate_gap_threshold(week[TS])
    assert threshold == 10800  # max(3 × 30 Min., 180 Min.) – wie AnalysisSettings
    assert charts.estimate_gap_threshold(week[TS], min_gap_minutes=60) == 5400
    gaps = charts.find_gaps(week[TS], threshold)
    assert len(gaps) == 1
    a, b = gaps[0]
    assert (b - a) == pd.Timedelta(hours=6, minutes=30)


def test_timeseries_gap_breaks_line_with_none(week):
    fig = charts.timeseries_chart(week, [TEMPERATURE, HUMIDITY])
    temp = _traces(fig, "Temperatur")[0]
    y = np.asarray(temp.y, dtype=float)
    assert temp.connectgaps is False
    assert np.isnan(y).sum() == 1  # genau eine Unterbrechung
    # Die NaN-Zeile liegt innerhalb der Lücke (lokal 06:00–12:00 am 03.03.)
    x = pd.to_datetime(pd.Series(temp.x))
    gap_x = x[np.isnan(y)].iloc[0]
    assert pd.Timestamp("2026-03-03 05:30") < gap_x < pd.Timestamp("2026-03-03 12:00")
    # keine erfundenen Werte: alle anderen y-Werte stammen aus den Originaldaten
    assert set(np.round(y[~np.isnan(y)], 1)) <= set(np.round(week[TEMPERATURE], 1))


def test_timeseries_without_gaps_option(week):
    fig = charts.timeseries_chart(week, [TEMPERATURE], show_gaps=False)
    assert not np.isnan(np.asarray(fig.data[0].y, dtype=float)).any()


def test_insert_gap_breaks_does_not_modify_input(week):
    before = week.copy()
    charts.insert_gap_breaks(week, charts.find_gaps(week[TS], 5400))
    pd.testing.assert_frame_equal(week, before)


# ---------------------------------------------------------------------------
# Downsampling
# ---------------------------------------------------------------------------
def test_downsample_keeps_min_max_and_limits_points():
    df = make_measurements(start="2026-01-01", end="2026-03-01", interval_min=5, seed=3)
    # Ausreißer, die erhalten bleiben müssen
    df.loc[1234, HUMIDITY] = 99.0
    df.loc[5678, TEMPERATURE] = 5.5
    out = charts.downsample_for_display(df, max_points=1000, metrics=[TEMPERATURE, HUMIDITY])
    assert len(out) <= 1000
    assert len(out) < len(df)
    assert out[HUMIDITY].max() == df[HUMIDITY].max() == 99.0
    assert out[TEMPERATURE].min() == df[TEMPERATURE].min() == 5.5
    assert out[HUMIDITY].min() == df[HUMIDITY].min()
    assert out[TEMPERATURE].max() == df[TEMPERATURE].max()
    assert out[TS].is_monotonic_increasing
    # nur Originalzeilen
    merged = out.merge(df, on=list(df.columns), how="left", indicator=True)
    assert (merged["_merge"] == "both").all()
    assert out.attrs["downsampled_from"] == len(df)


def test_downsample_small_input_is_unchanged(week):
    assert charts.downsample_for_display(week, max_points=10_000) is week


def test_timeseries_respects_max_points():
    df = make_measurements(start="2026-01-01", end="2026-04-01", interval_min=5)
    fig = charts.timeseries_chart(df, [TEMPERATURE, HUMIDITY, DEW_POINT], max_points=3000)
    assert all(len(t.y) <= 3000 + 5 for t in fig.data)  # + eingefügte Lückenzeilen
    assert fig.layout.meta["downsampled_from"] == len(df)


# ---------------------------------------------------------------------------
# Achsen, Einheiten, Layout
# ---------------------------------------------------------------------------
def test_timeseries_units_in_axis_titles_and_subplots(week):
    fig = charts.timeseries_chart(week, [TEMPERATURE, HUMIDITY, DEW_POINT, VPD])
    titles = [fig.layout[k].title.text for k in fig.layout if k.startswith("yaxis")]
    assert "Temperatur / Taupunkt (°C)" in titles
    assert "Rel. Feuchte (%)" in titles
    assert "VPD (kPa)" in titles
    assert len(titles) == 3  # °C gemeinsam, % und kPa getrennt
    assert fig.layout.separators == ",."
    # Humidity-Achse bleibt innerhalb 0–100
    hum_axis = next(
        fig.layout[k] for k in fig.layout if k.startswith("yaxis") and "Feuchte" in fig.layout[k].title.text
    )
    assert 0 <= hum_axis.range[0] < hum_axis.range[1] <= 100


def test_timeseries_thresholds_and_bands(week):
    bands = [
        {
            "start": "2026-03-04T00:00:00Z",
            "end": "2026-03-04T06:00:00Z",
            "label": "Feucht <b>",
            "level": "auffaellig",
        }
    ]
    fig = charts.timeseries_chart(week, [HUMIDITY], thresholds={HUMIDITY: [(70, "hoch")]}, bands=bands)
    rng = fig.layout.yaxis.range
    assert rng[0] <= 70 <= rng[1]  # Schwelle sichtbar
    assert any(s.type == "rect" for s in fig.layout.shapes)
    assert any(s.type == "line" for s in fig.layout.shapes)
    assert any("Feucht &lt;b>" in (a.text or "") for a in fig.layout.annotations)


def test_hover_uses_german_units(week):
    fig = charts.timeseries_chart(week, [TEMPERATURE])
    assert "°C" in fig.data[0].hovertemplate
    assert fig.layout.xaxis.hoverformat == "%d.%m.%Y, %H:%M"
    # keine englischen Monatsnamen über %b
    assert all("%b" not in stop["value"] for stop in charts.DATE_TICKFORMATSTOPS)


def test_empty_input_returns_placeholder_figure():
    empty = pd.DataFrame(columns=[TS, TEMPERATURE])
    fig = charts.timeseries_chart(empty, [TEMPERATURE])
    assert "Keine Daten" in fig.layout.annotations[0].text


# ---------------------------------------------------------------------------
# Weitere Diagramme
# ---------------------------------------------------------------------------
def test_band_share_bar_single_and_grouped():
    fig = charts.band_share_bar({"normal": 0.7, "elevated": 0.2, "high": 0.05, "gap": 0.05})
    assert [t.name for t in fig.data] == ["Unauffällig", "Erhöht", "Hoch", "Datenlücke"]
    assert fig.layout.barmode == "stack"
    assert sum(float(t.x[0]) for t in fig.data) == pytest.approx(100)
    long = pd.DataFrame(
        [
            {"group": g, "band": b, "share": s}
            for g in ("Bad", "Küche")
            for b, s in (("normal", 0.6), ("high", 0.4))
        ]
    )
    fig2 = charts.band_share_bar(long, labels={"high": "≥ 70 %"})
    assert {t.name for t in fig2.data} == {"Unauffällig", "≥ 70 %"}
    assert list(fig2.data[0].y) == ["Bad", "Küche"]


def test_monthly_chart_marks_incomplete_months():
    df = pd.DataFrame(
        {
            "month": ["2026-01", "2026-02", "2026-03"],
            "mean": [55.0, 58.0, 52.0],
            "min": [40.0, 42.0, 41.0],
            "max": [80.0, 77.0, 70.0],
            "coverage": [1.0, 0.95, 0.4],
        }
    )
    fig = charts.monthly_chart(df, HUMIDITY)
    names = [t.name for t in fig.data]
    assert "Spanne (unvollständig)" in names
    incomplete = _traces(fig, "Spanne (unvollständig)")[0]
    assert list(incomplete.x) == ["Mär"]
    assert incomplete.marker.pattern.shape == "/"
    mean = _traces(fig, "Mittelwert")[0]
    assert "Unvollständig" in mean.customdata[2][3]
    assert mean.customdata[0][0] == "Januar 2026"
    assert fig.layout.xaxis.title.text == "2026"
    assert "(%)" in fig.layout.yaxis.title.text


def test_seasonal_and_day_night_charts():
    seasons = pd.DataFrame({"season": ["winter", "spring"], "year": [2026, 2026], "mean": [55, 50], "min": [40, 38],
                            "max": [75, 70], "complete": [True, False]})  # fmt: skip
    fig = charts.seasonal_chart(seasons, HUMIDITY)
    assert list(_traces(fig, "Mittelwert")[0].x) == ["Winter 2025/26", "Frühling 2026"]
    dn = pd.DataFrame({"group": ["2026-03", "2026-03", "2026-04", "2026-04"], "phase": ["day", "night", "Tag", "Nacht"],
                       "mean": [50, 56, 49, 55]})  # fmt: skip
    fig2 = charts.day_night_chart(dn, HUMIDITY)
    day = _traces(fig2, "Tag")[0]
    assert list(day.x) == ["Mär 2026", "Apr 2026"]
    assert list(_traces(fig2, "Nacht")[0].y) == [56, 55]


def test_comparison_chart_overlay_and_daily(week):
    a = week[week[TS] < pd.Timestamp("2026-03-04", tz="UTC")]
    b = week[week[TS] >= pd.Timestamp("2026-03-04", tz="UTC")]
    fig = charts.comparison_chart(a, b, HUMIDITY, labels=("A", "B"))
    assert [t.name for t in fig.data] == ["A", "B"]
    assert min(np.nanmin(np.asarray(t.x, dtype=float)) for t in fig.data) >= 1.0  # „Tag 1“
    assert fig.layout.xaxis.tickprefix == "Tag "
    assert np.isnan(np.asarray(fig.data[0].y, dtype=float)).any()  # Lücke in A sichtbar
    fig2 = charts.comparison_chart(a, b, HUMIDITY, mode="daily")
    assert all(t.type == "bar" for t in fig2.data)
    assert list(fig2.data[0].x)[0] == 1


def test_heatmap_hour_by_day_keeps_missing_hours_empty(week):
    fig = charts.heatmap_hour_by_day(week, HUMIDITY)
    z = np.asarray(fig.data[0].z, dtype=float)
    assert z.shape == (24, 7)
    assert np.isnan(z).sum() >= 6  # Lücke 06–12 Uhr am 03.03.
    assert fig.data[0].colorbar.title.text == "%"
    assert "Uhr" in fig.data[0].customdata[0][0]


def test_episode_timeline():
    eps = [
        {"start": pd.Timestamp("2026-03-01T06:00Z"), "end": pd.Timestamp("2026-03-01T09:30Z"), "level": "hinweis",
         "label": "≥ 60 %", "max": 66},
        {"start": pd.Timestamp("2026-03-02T06:00Z"), "end": pd.Timestamp("2026-03-02T06:05Z"), "level": "auffaellig",
         "label": "≥ 70 %", "max": 71},
    ]  # fmt: skip
    fig = charts.episode_timeline(eps)
    assert len(fig.data) == 2
    assert fig.data[0].customdata[0][2] == "3 Std. 30 Min."
    assert "66" in fig.data[0].customdata[0][3]
    assert charts.episode_timeline([]).layout.annotations[0].text.startswith("Keine Phasen")


def test_dew_point_spread_chart(week):
    fig = charts.dew_point_spread_chart(week, warn_spread=3.0, surface_offset=4.0)
    names = [t.name for t in fig.data]
    assert "Abstand Temperatur – Taupunkt" in names
    assert any("Annahme" in n for n in names)
    titles = [fig.layout[k].title.text for k in fig.layout if k.startswith("yaxis")]
    assert "Abstand (K)" in titles and "Temperatur / Taupunkt (°C)" in titles
    assert any("Warnschwelle" in (a.text or "") for a in fig.layout.annotations)


def test_all_figures_serialize(week):
    figs = [
        charts.timeseries_chart(week, [TEMPERATURE, HUMIDITY], rangeslider=True, range_selector=True),
        charts.heatmap_hour_by_day(week),
        charts.dew_point_spread_chart(week),
    ]
    for fig in figs:
        assert fig.to_json()


# ---------------------------------------------------------------------------
# Theme & Komponenten
# ---------------------------------------------------------------------------
def test_plotly_template_registration_idempotent():
    import plotly.io as pio

    assert theme.apply_plotly_template(set_default=False) == "climate"
    assert theme.apply_plotly_template(set_default=False) == "climate"
    assert pio.templates["climate"].layout.separators == ",."


def test_css_variants():
    app_css = theme.build_css("app")
    report_css = theme.build_css("report")
    assert "--cd-primary: #2563EB" in app_css
    assert 'stSidebar"]' in report_css and "display: none" in report_css
    assert "stExpandSidebarButton" not in app_css


def test_normalize_level_aliases():
    assert theme.normalize_level("warning") == "hinweis"
    assert theme.normalize_level("Auffällig") == "auffaellig"
    assert theme.normalize_level("OK") == "ok"
    assert theme.normalize_level(None) == "neutral"
    assert theme.normalize_level("quatsch") == "neutral"


EVIL = '<script>alert("x")</script> & Bad'


def test_components_escape_user_text():
    html_blocks = [
        ui.page_header_html(EVIL, EVIL),
        ui.section_header_html(EVIL, EVIL),
        ui.metric_card_html(EVIL, EVIL, unit=EVIL, sublabel=EVIL, delta=EVIL),
        ui.metric_grid_html([{"label": EVIL, "value": 1.5}]),
        ui.callout_html("hinweis", EVIL, EVIL),
        ui.empty_state_html(EVIL, EVIL),
        ui.key_value_list_html({EVIL: EVIL}),
        ui.finding_list_html([{"level": "hinweis", "category": EVIL, "text": EVIL, "evidence": [EVIL]}]),
        ui.step_indicator_html([EVIL, "Prüfung"], 0),
        ui.status_badge_html("ok", EVIL),
        ui.data_quality_bar_html(50, label=EVIL, note=EVIL),
        ui.brand_html(EVIL, EVIL),
        ui.report_footer_html(EVIL),
    ]
    for block in html_blocks:
        assert "<script>" not in block
        assert "&lt;script&gt;" in block
        assert "&amp; Bad" in block


def test_metric_card_formats_numbers_german():
    html = ui.metric_card_html("Ø Temperatur", 21.44, "°C", status="hinweis")
    assert "21,4" in html and "°C" in html
    assert "cd-metric--hinweis" in html and "Hinweis" in html
    assert ">–<" in ui.metric_card_html("x", None, "°C")


def test_data_quality_levels():
    assert "cd-quality__fill--ok" in ui.data_quality_bar_html(96)
    assert "cd-quality__fill--hinweis" in ui.data_quality_bar_html(75)
    assert "cd-quality__fill--auffaellig" in ui.data_quality_bar_html(20)
    assert "width:100.0%" in ui.data_quality_bar_html(140)
    assert "–" in ui.data_quality_bar_html(None)


def test_finding_list_and_step_indicator():
    html = ui.finding_list_html(
        [{"level": "warning", "category": "humidity", "text": "Feucht", "evidence": {"Max": 78.0}}]
    )
    assert "cd-finding--hinweis" in html and "Luftfeuchtigkeit" in html and "Max: 78,0" in html
    assert ui.finding_list_html([]) == ""
    steps = ui.step_indicator_html(["A", "B", "C"], 1)
    assert steps.count("cd-step--done") == 1 and 'aria-current="step"' in steps
    assert ui.mode_notice_html("supabase") == ""
    assert "lokal" in ui.mode_notice_html("local")


def test_user_labels_are_escaped_in_chart_names(week):
    evil = '<a href="https://example.invalid">Keller</a>'
    fig = charts.comparison_chart(week, week, HUMIDITY, labels=(evil, "B"))
    names = [t.name for t in fig.data]
    assert evil not in names
    assert "&lt;a href=&quot;https://example.invalid&quot;&gt;Keller&lt;/a&gt;" in names


def test_redesign_components_escape_and_keep_contract():
    evil = "<script>alert(1)</script>"
    bar = ui.context_bar_html([("Raum", evil), (evil, "x")])
    assert "<script>" not in bar and "&lt;script&gt;" in bar
    card = ui.metric_card_html("Temperatur", 20.84, "°C", accent=TEMPERATURE, emphasis=True)
    assert "cd-metric--lg" in card and "cd-metric__dot" in card and "20,8" in card
    # unbekannte Messgröße → kein Farbpunkt, kein Fehler
    assert "cd-metric__dot" not in ui.metric_card_html("x", 1, accent="unbekannt")
    steps = ui.step_indicator_html(["A", "B", "C"], 1)
    assert "Schritt 02" in steps and "✓" in steps and steps.count("cd-step--done") == 1


def test_nav_active_css_only_allows_safe_href():
    css = theme.nav_active_css('analyse"]{}body{display:none')
    assert '[href="analysebodydisplaynone"]' in css
    assert '[href=""]' in theme.nav_active_css("")

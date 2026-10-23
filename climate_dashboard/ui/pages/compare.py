"""Seite „Vergleich“ – Zeiträume, Vorjahre, Jahreszeiten sowie Räume/Sensoren gegenüberstellen.

Verglichen werden ausschließlich vorhandene Messdaten (``analysis.periods.compare_periods``,
Δ = B − A). Temperaturen werden in Kelvin, Prozentgrößen in Prozentpunkten verglichen;
eine relative Änderung (%) gibt es nur, wo sie sinnvoll ist.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pandas as pd
import streamlit as st

from climate_dashboard.analysis.humidity import band_shares
from climate_dashboard.analysis.periods import (
    compare_periods,
    period_bounds,
    previous_period,
    seasonal_overview,
)
from climate_dashboard.analysis.stats import prepare
from climate_dashboard.domain.models import (
    DEW_POINT,
    HUMIDITY,
    METRIC_LABELS,
    SENSOR_ID,
    TEMPERATURE,
    TS,
    AnalysisSettings,
    Sensor,
)
from climate_dashboard.ui import charts, state
from climate_dashboard.ui import components as ui
from climate_dashboard.ui import selectors as sel
from climate_dashboard.ui.formatting import (
    MONTH_NAMES,
    fmt_bool,
    fmt_date,
    fmt_metric,
    fmt_month,
    fmt_number,
    fmt_percent,
)

MODES = (
    "Tag / Woche / Monat",
    "Zwei Zeiträume",
    "Gleich lange Zeiträume",
    "Gleicher Monat in Vorjahren",
    "Jahreszeiten",
    "Raum / Sensor",
)
KINDS = {"Tag": "day", "Woche": "week", "Monat": "month"}
EPISODE_KEYS = ("pct_elevated", "pct_high", "hours_elevated", "sustained_episodes", "sustained_per_week",
                "longest_episode_hours", "time_coverage_pct")  # fmt: skip
CHART_METRICS = (HUMIDITY, TEMPERATURE, DEW_POINT)


# ---------------------------------------------------------------------------
# Ergebnisdarstellung
# ---------------------------------------------------------------------------
def _caveats(cmp: Any) -> None:
    generic = cmp.caveats[-1] if cmp.caveats else None
    specific = [c for c in cmp.caveats if c != generic]
    if specific:
        ui.callout("hinweis", "Vergleichbarkeit eingeschränkt", " ".join(specific))
    if generic:
        ui.callout("info", "Keine Ursachenaussage", generic)


def _render_result(
    cmp: Any, a: tuple[Any, Any], b: tuple[Any, Any], settings: AnalysisSettings, key: str
) -> None:
    """``a``/``b``: ``(prepared, (start, end))``."""
    tz = settings.timezone
    (pa, (sa, _ea)), (pb, (sb, _eb)) = a, b
    la, lb = cmp.labels
    ui.section_header("Ergebnis", f"A = {la} · B = {lb} · Δ = B − A")
    ui.metric_grid(
        [
            {"label": f"A: {la} – Ø rel. Feuchte", "value": fmt_number(_item(cmp, "humidity_mean", "a"), 0),
             "unit": "%", "sublabel": f"{fmt_number(cmp.a.n)} Messungen · Abdeckung {fmt_percent(cmp.a.time_coverage_pct)}"},
            {"label": f"B: {lb} – Ø rel. Feuchte", "value": fmt_number(_item(cmp, "humidity_mean", "b"), 0),
             "unit": "%", "sublabel": f"{fmt_number(cmp.b.n)} Messungen · Abdeckung {fmt_percent(cmp.b.time_coverage_pct)}"},
        ],
        min_width=240,
    )  # fmt: skip
    _caveats(cmp)
    sel.table(sel.comparison_rows(cmp), key=f"{key}_table")
    st.caption(
        "Temperaturen und Taupunkte: Differenz in Kelvin. Prozentangaben: Differenz in Prozentpunkten. "
        "Eine relative Änderung (Δ %) wird nur für Mengen wie absolute Feuchte, Stunden oder Anzahlen angegeben."
    )

    ui.section_header("Verlauf im Vergleich", "Beide Zeiträume auf gemeinsamer Achse „Tag 1 … n“.")
    c1, c2 = st.columns([2, 1])
    with c1:
        metric = st.segmented_control(
            "Messgröße", list(CHART_METRICS), default=HUMIDITY, key=f"{key}_metric",
            format_func=lambda m: METRIC_LABELS[m],
        ) or HUMIDITY  # fmt: skip
    with c2:
        mode = st.segmented_control("Darstellung", ["Verlauf", "Tagesmittel"], default="Verlauf",
                                    key=f"{key}_mode") or "Verlauf"  # fmt: skip
    fig = charts.comparison_chart(
        pa.frame[[TS, metric]], pb.frame[[TS, metric]], metric, labels=(la, lb), tz=tz,
        mode="daily" if mode == "Tagesmittel" else "overlay", starts=(sa, sb),
    )  # fmt: skip
    ui.render_chart(fig, key=f"{key}_chart")

    ui.section_header("Feuchtephasen und Datenabdeckung")
    rows = []
    for label, prep in ((la, pa), (lb, pb)):
        values, _labels = charts.band_shares_input(band_shares(prep))
        rows += [{"group": label, "band": k, "share": v} for k, v in values.items()]
    _v, labels = charts.band_shares_input(band_shares(pa))
    ui.render_chart(charts.band_share_bar(pd.DataFrame(rows), labels=labels), key=f"{key}_bands")
    sel.table(sel.comparison_rows(cmp, EPISODE_KEYS), key=f"{key}_episodes")
    left, right = st.columns(2, gap="large")
    with left:
        ui.data_quality_bar(cmp.a.time_coverage_pct, label=f"Zeitabdeckung A ({la})")
    with right:
        ui.data_quality_bar(cmp.b.time_coverage_pct, label=f"Zeitabdeckung B ({lb})")


def _item(cmp: Any, key: str, side: str) -> float | None:
    it = cmp.item(key)
    return getattr(it, side) if it else None


def _compare(
    sensor_a: Sensor, period_a: tuple[Any, Any], sensor_b: Sensor, period_b: tuple[Any, Any],
    labels: tuple[str, str], settings: AnalysisSettings, key: str,
) -> None:  # fmt: skip
    with ui.loading("Zeiträume werden verglichen …"):
        _ra, pa = sel.load_prepared(sensor_a.id, *period_a, settings, sensor_a.expected_interval_s)
        _rb, pb = sel.load_prepared(sensor_b.id, *period_b, settings, sensor_b.expected_interval_s)
        if pa.empty and pb.empty:
            ui.callout("info", "Keine Messungen", "In beiden Zeiträumen liegen keine Messungen vor.")
            return
        cmp = sel.memo(
            ("compare", sensor_a.id, *period_a, sensor_b.id, *period_b, labels, sel.settings_key(settings)),
            lambda: compare_periods(pa, pb, settings, labels=labels),
        )
    if pa.empty or pb.empty:
        empty = labels[0] if pa.empty else labels[1]
        ui.callout("hinweis", "Ein Zeitraum ohne Messungen", f"Für „{empty}“ liegen keine Messungen vor.")
    _render_result(cmp, (pa, period_a), (pb, period_b), settings, key)


def _label(start: Any, end: Any, tz: str) -> str:
    return sel.period_label(start, end, tz)


def _date_range(label: str, key: str, lo, hi, default: tuple) -> tuple[Any, Any]:
    picked = st.date_input(label, value=default, min_value=lo, max_value=hi, format="DD.MM.YYYY", key=key)
    if isinstance(picked, tuple | list):
        d0 = picked[0] if picked else default[0]
        d1 = picked[1] if len(picked) > 1 else d0
    else:
        d0 = d1 = picked
    return (d0, d1) if d0 <= d1 else (d1, d0)


# ---------------------------------------------------------------------------
# Modi
# ---------------------------------------------------------------------------
def _mode_calendar(sensor: Sensor, last: Any, settings: AnalysisSettings) -> None:
    tz = settings.timezone
    kind_label = st.segmented_control("Zeitraum", list(KINDS), default="Monat", key="cmp_kind") or "Monat"
    kind = KINDS[kind_label]
    cur = period_bounds(kind, last, tz)
    prev = previous_period(kind, last, tz)
    st.caption(
        f"Aktueller {kind_label} = der {kind_label}, in dem die letzte Messung liegt "
        f"({fmt_date(last, tz)}); er ist ggf. noch nicht vollständig."
    )
    names = {
        "day": lambda s, e: fmt_date(s, tz, style="weekday"),
        "week": lambda s, e: f"KW {s.isocalendar().week} ({_label(s, e, tz)})",
        "month": lambda s, e: fmt_month(s),
    }[kind]
    labels = (f"{names(*prev)} (vorher)", f"{names(*cur)} (aktuell)")
    _compare(sensor, prev, sensor, cur, labels, settings, "cmp_cal")


def _mode_two(sensor: Sensor, first: Any, last: Any, settings: AnalysisSettings) -> None:
    tz = settings.timezone
    lo, hi = first.tz_convert(tz).date(), last.tz_convert(tz).date()
    c1, c2 = st.columns(2)
    with c1:
        a = _date_range("Zeitraum A", "cmp_two_a", lo, hi, (max(lo, hi - timedelta(days=13)), hi))
    with c2:
        b_default_end = max(lo, hi - timedelta(days=14))
        b = _date_range("Zeitraum B", "cmp_two_b", lo, hi, (max(lo, b_default_end - timedelta(days=13)), b_default_end))
    pa = (sel.local_midnight(a[0], tz), sel.local_midnight(a[1] + timedelta(days=1), tz))
    pb = (sel.local_midnight(b[0], tz), sel.local_midnight(b[1] + timedelta(days=1), tz))
    _compare(sensor, pa, sensor, pb, (_label(*pa, tz), _label(*pb, tz)), settings, "cmp_two")


def _mode_equal(sensor: Sensor, first: Any, last: Any, settings: AnalysisSettings) -> None:
    tz = settings.timezone
    lo, hi = first.tz_convert(tz).date(), last.tz_convert(tz).date()
    c1, c2 = st.columns(2)
    with c1:
        a = _date_range("Zeitraum A", "cmp_eq_a", lo, hi, (max(lo, hi - timedelta(days=6)), hi))
    days = (a[1] - a[0]).days + 1
    with c2:
        b_start = st.date_input("Beginn von Zeitraum B", value=max(lo, a[0] - timedelta(days=days)),
                                min_value=lo, max_value=hi, format="DD.MM.YYYY", key="cmp_eq_b")  # fmt: skip
        st.caption(f"B erhält dieselbe Länge wie A: {days} Tag(e).")
    pa = (sel.local_midnight(a[0], tz), sel.local_midnight(a[1] + timedelta(days=1), tz))
    pb = (sel.local_midnight(b_start, tz), sel.local_midnight(b_start + timedelta(days=days), tz))
    _compare(sensor, pa, sensor, pb, (_label(*pa, tz), _label(*pb, tz)), settings, "cmp_eq")


def _months_with_data(first: Any, last: Any, tz: str) -> list[pd.Period]:
    p0 = first.tz_convert(tz).tz_localize(None).to_period("M")
    p1 = last.tz_convert(tz).tz_localize(None).to_period("M")
    return list(pd.period_range(p0, p1, freq="M"))


def _mode_years(sensor: Sensor, first: Any, last: Any, settings: AnalysisSettings) -> None:
    tz = settings.timezone
    months = _months_with_data(first, last, tz)
    month_numbers = sorted({p.month for p in months}, key=lambda m: -max(p.ordinal for p in months if p.month == m))
    month = st.selectbox("Monat", month_numbers, format_func=lambda m: MONTH_NAMES[m - 1], key="cmp_year_month")
    periods = [p for p in months if p.month == month]
    if len(periods) < 2:
        ui.callout(
            "info",
            "Nur ein Jahr vorhanden",
            f"Für {MONTH_NAMES[month - 1]} liegen nur Daten aus {periods[0].year if periods else '–'} vor. "
            "Ein Vergleich mit Vorjahren ist erst möglich, wenn Messungen aus mehreren Jahren importiert sind.",
        )
        return
    bounds = {p.year: period_bounds("month", p.to_timestamp().tz_localize(tz), tz) for p in periods}
    years = sorted(bounds)
    c1, c2 = st.columns(2)
    with c1:
        ya = st.selectbox("Jahr A", years, index=len(years) - 2, key="cmp_year_a")
    with c2:
        yb = st.selectbox("Jahr B", years, index=len(years) - 1, key="cmp_year_b")
    if len(years) > 2:
        _all_years_table(sensor, bounds, settings)
    labels = (fmt_month(bounds[ya][0]), fmt_month(bounds[yb][0]))
    _compare(sensor, bounds[ya], sensor, bounds[yb], labels, settings, "cmp_years")


def _all_years_table(sensor: Sensor, bounds: dict[int, tuple], settings: AnalysisSettings) -> None:
    keys = ("temperature_mean", "humidity_mean", "pct_elevated", "sustained_episodes", "time_coverage_pct")
    years = sorted(bounds)
    base = years[0]
    _r, pbase = sel.load_prepared(sensor.id, *bounds[base], settings, sensor.expected_interval_s)
    table: dict[str, dict[str, str]] = {}
    for y in years:
        _r, py = sel.load_prepared(sensor.id, *bounds[y], settings, sensor.expected_interval_s)
        cmp = compare_periods(pbase, py, settings, labels=(str(base), str(y)))
        for row in sel.comparison_rows(cmp, keys).to_dict("records"):
            table.setdefault(row["Kennzahl"], {"Kennzahl": row["Kennzahl"]})[str(y)] = row[str(y)]
    with st.expander(f"Alle Jahre ({len(years)})", expanded=True):
        sel.table(pd.DataFrame(list(table.values())), key="cmp_years_all")


def _mode_seasons(sensor: Sensor, first: Any, last: Any, settings: AnalysisSettings) -> None:
    def compute() -> list:
        df = state.load_measurements([sensor.id], first, sel.data_end(last, sensor.expected_interval_s))
        return seasonal_overview(prepare(df.drop(columns=[SENSOR_ID], errors="ignore"), settings,
                                         sensor.expected_interval_s))  # fmt: skip

    with ui.loading("Jahreszeiten werden berechnet …"):
        seasons = sel.memo(("seasons", sensor.id, first, last, sel.settings_key(settings)), compute)
    if not seasons:
        ui.callout("info", "Keine Daten", "Für diesen Sensor liegen keine Messungen vor.")
        return
    names = {"winter": "Winter", "spring": "Frühling", "summer": "Sommer", "autumn": "Herbst"}
    present = list(dict.fromkeys(s.season for s in seasons))
    season = st.segmented_control("Jahreszeit", present, default=present[-1], format_func=lambda k: names[k],
                                  key="cmp_season") or present[-1]  # fmt: skip
    chosen = [s for s in seasons if s.season == season]
    sel.table(
        pd.DataFrame(
            [{"Jahreszeit": s.label, "Abdeckung (ganze Jahreszeit)": fmt_percent(s.time_coverage_pct),
              "Ausreichend": fmt_bool(s.sufficient),
              "Ø Temperatur": fmt_metric(s.kpis[TEMPERATURE].mean, TEMPERATURE),
              "Ø rF": fmt_metric(s.kpis[HUMIDITY].mean, HUMIDITY),
              "Zeit erhöht": fmt_percent(s.pct_elevated, 1), "Anhaltende Phasen": s.sustained_episodes,
              "Hinweis": " ".join(s.reasons)} for s in chosen]
        ),
        key="cmp_season_table",
    )  # fmt: skip
    if len(chosen) < 2:
        ui.callout(
            "info",
            "Nur eine Jahreszeit dieser Art vorhanden",
            f"„{chosen[0].label}“ ist die einzige {names[season]}-Periode in den Daten – ein Vergleich über "
            "Jahre ist erst mit weiteren Messungen möglich.",
        )
        return
    ui.render_chart(charts.seasonal_chart(charts.seasonal_frame(chosen, HUMIDITY), HUMIDITY), key="cmp_season_chart")
    a, b = chosen[-2], chosen[-1]
    _compare(sensor, (a.start, a.end), sensor, (b.start, b.end), (a.label, b.label), settings, "cmp_seasons")


def _mode_sensors(settings: AnalysisSettings) -> None:
    repo = state.repo()
    rooms = {r.id: r for r in repo.list_rooms()}
    sensors = [s for s in repo.list_sensors() if s.room_id in rooms]
    with_data = [s for s in sensors if sel.sensor_extent(s.id)[2] > 0]
    if len(with_data) < 2:
        ui.callout("info", "Zu wenige Sensoren", "Für diesen Vergleich werden mindestens zwei Sensoren mit Daten benötigt.")
        return
    by_id = {s.id: s for s in with_data}

    def name(sid: str) -> str:
        s = by_id[sid]
        return f"{rooms[s.room_id].name} – {s.name}"

    ids = list(by_id)
    c1, c2 = st.columns(2)
    with c1:
        a_id = st.selectbox("A", ids, index=0, format_func=name, key="cmp_sens_a")
    with c2:
        b_id = st.selectbox("B", ids, index=1, format_func=name, key="cmp_sens_b")
    if a_id == b_id:
        ui.callout("info", "Gleicher Sensor", "Bitte zwei verschiedene Sensoren wählen.")
        return
    first, last, _n = sel.combined_extent([a_id, b_id])
    start, end = sel.period_selector(key="cmp_sens_period", first=first, last=last, tz=settings.timezone)
    _compare(by_id[a_id], (start, end), by_id[b_id], (start, end), (name(a_id), name(b_id)), settings, "cmp_sens")


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def _body() -> None:
    ui.page_header(
        "Vergleich", "Zwei Zeiträume oder Räume gegenüberstellen – nur mit tatsächlich vorhandenen Daten.",
        eyebrow="Auswertung",
    )
    repo = state.repo()
    rooms = repo.list_rooms()
    if not rooms:
        ui.empty_state("Noch keine Daten", "Importiere zuerst einen SwitchBot-CSV-Export.")
        sel.nav_link("import", "Zum Import")
        return
    settings = state.analysis_settings()
    mode = st.radio("Art des Vergleichs", list(MODES), horizontal=True, key="cmp_mode")
    if mode == "Raum / Sensor":
        _mode_sensors(settings)
        return
    c1, c2 = st.columns(2)
    with c1:
        room = sel.room_selector(rooms, key="cmp_room")
    sensors = repo.list_sensors(room.id) if room else []
    with c2:
        sensor = sel.sensor_selector(sensors, key=f"cmp_sensor_{room.id}") if sensors else None
    if sensor is None:
        ui.callout("info", "Kein Sensor", "Für diesen Raum ist noch kein Sensor vorhanden.")
        return
    first, last, n = sel.sensor_extent(sensor.id)
    if not n:
        ui.callout("info", "Noch keine Messdaten", "Für diesen Sensor wurden noch keine Daten importiert.")
        sel.nav_link("import", "Daten importieren")
        return
    st.caption(f"Daten vorhanden: {sel.period_label(first, last, settings.timezone)}")
    if mode == MODES[0]:
        _mode_calendar(sensor, last, settings)
    elif mode == MODES[1]:
        _mode_two(sensor, first, last, settings)
    elif mode == MODES[2]:
        _mode_equal(sensor, first, last, settings)
    elif mode == MODES[3]:
        _mode_years(sensor, first, last, settings)
    else:
        _mode_seasons(sensor, first, last, settings)


def render() -> None:
    sel.guarded(_body)

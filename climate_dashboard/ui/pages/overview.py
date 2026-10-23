"""Seite „Übersicht“ – aktuelle Lage je Raum.

Je Raum wird der Sensor mit der jüngsten Messung verwendet. Kennzahlen beziehen sich auf die
letzte vorhandene Messung (nicht auf „heute“), weil die Daten als Export vorliegen.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from climate_dashboard.analysis.assessment import assess
from climate_dashboard.analysis.humidity import analyze_humidity
from climate_dashboard.analysis.periods import compare_periods, previous_period
from climate_dashboard.analysis.quality import assess_quality
from climate_dashboard.analysis.stats import prepare, slice_prepared, summarize_prepared
from climate_dashboard.domain.models import (
    ABS_HUMIDITY,
    DEW_POINT,
    HUMIDITY,
    SENSOR_ID,
    TEMPERATURE,
    TS,
    VPD,
    AnalysisSettings,
    Room,
    utcnow,
)
from climate_dashboard.ui import charts, state
from climate_dashboard.ui import components as ui
from climate_dashboard.ui import selectors as sel
from climate_dashboard.ui.formatting import (
    fmt_datetime,
    fmt_delta,
    fmt_duration,
    fmt_metric,
    fmt_month,
    fmt_number,
    fmt_percent,
)

STALE_AFTER = pd.Timedelta(days=2)
DAY = pd.Timedelta(days=1)


# ---------------------------------------------------------------------------
# Berechnung (gecacht je Raum, Sensor, letzter Messung, Einstellungen, Datenstand)
# ---------------------------------------------------------------------------
def _snapshot(room: Room, settings: AnalysisSettings) -> dict[str, Any] | None:
    sensors = state.repo().list_sensors(room.id)
    sensor = sel.main_sensor(sensors)
    if sensor is None:
        return None
    first, last, n = sel.sensor_extent(sensor.id)
    if not n:
        return {"sensor": sensor, "n": 0, "sensors": len(sensors)}
    exp = sensor.expected_interval_s
    tz = settings.timezone

    def compute() -> dict[str, Any]:
        end = sel.data_end(last, exp)
        month_b = previous_period("month", last, tz)  # letzter vollständiger Kalendermonat
        month_a = previous_period("month", month_b[0], tz)  # Vormonat
        load_start = min(month_a[0].tz_convert("UTC"), end - 30 * DAY)
        df = state.load_measurements([sensor.id], load_start, end + DAY)
        df = df.drop(columns=[SENSOR_ID], errors="ignore")
        full = prepare(df, settings, exp)
        p7 = slice_prepared(full, end - 7 * DAY, end)
        p30 = slice_prepared(full, end - 30 * DAY, end)
        prev7 = slice_prepared(full, end - 14 * DAY, end - 7 * DAY)
        q30 = assess_quality(df[[TS]], max(end - 30 * DAY, sel.utc(first)), end, exp, settings)
        hum30 = analyze_humidity(p30)
        latest = full.frame.iloc[-1]
        result: dict[str, Any] = {
            "end": end,
            "latest": {m: latest.get(m) for m in (TEMPERATURE, HUMIDITY, DEW_POINT, VPD, ABS_HUMIDITY)},
            "latest_ts": latest[TS],
            "s7": summarize_prepared(p7, (TEMPERATURE, HUMIDITY)),
            "s30": summarize_prepared(p30, (TEMPERATURE, HUMIDITY, DEW_POINT, VPD, ABS_HUMIDITY)),
            "quality": q30,
            "episodes": hum30.elevated_summary,
            "assessment": assess(p30, settings, q30),
            "trend7": None,
            "trend_month": None,
            "series": p7.frame[[TS, TEMPERATURE, HUMIDITY]].copy(),
            "gap_threshold_s": full.gap_threshold_s,
        }
        if len(prev7.frame) >= 2 and len(p7.frame) >= 2:
            result["trend7"] = compare_periods(prev7, p7, settings, labels=("vorherige 7 Tage", "letzte 7 Tage"))
        pa, pb = slice_prepared(full, *month_a), slice_prepared(full, *month_b)
        if len(pa.frame) >= 2 and len(pb.frame) >= 2:
            result["trend_month"] = compare_periods(
                pa, pb, settings, labels=(fmt_month(month_a[0]), fmt_month(month_b[0]))
            )
            result["month_labels"] = (fmt_month(month_a[0]), fmt_month(month_b[0]))
        return result

    data = sel.memo(("overview", room.id, sensor.id, last, sel.settings_key(settings), exp), compute)
    return {"sensor": sensor, "n": n, "first": first, "last": last, "sensors": len(sensors), **data}


# ---------------------------------------------------------------------------
# Darstellung
# ---------------------------------------------------------------------------
def _room_card(room: Room, snap: dict[str, Any] | None) -> dict[str, Any]:
    if not snap or not snap.get("n"):
        return {"label": room.name, "value": "–", "sublabel": "Noch keine Messdaten"}
    latest = snap["latest"]
    overall = snap["assessment"].overall
    age = utcnow() - snap["last"].to_pydatetime()
    sub = f"{fmt_metric(latest[TEMPERATURE], TEMPERATURE)} · Stand {fmt_datetime(snap['last'])}"
    if age > STALE_AFTER:
        sub += " (älter als 2 Tage)"
    return {
        "label": room.name,
        "value": fmt_number(latest[HUMIDITY], 0),
        "unit": "% rF",
        "accent": HUMIDITY,
        "sublabel": sub,
        "delta": overall.label,
        "status": sel.OVERALL_LEVEL.get(overall.key, "neutral"),
    }


def _trend_text(cmp: Any, key: str, metric: str) -> str:
    item = cmp.item(key) if cmp else None
    if item is None or item.delta is None:
        return "–"
    if key == "pct_elevated":
        return f"{fmt_number(item.delta, 0, sign=True)} Prozentpunkte"
    return fmt_delta(item.delta, metric)


def _render_trends(snap: dict[str, Any]) -> None:
    ui.section_header(
        "Entwicklung", "Unterschiede zwischen Zeiträumen – beschreibend, ohne Aussage über Ursachen."
    )
    rows = []
    t7, tm = snap.get("trend7"), snap.get("trend_month")
    month_label = " vs. ".join(reversed(snap.get("month_labels", ("Vormonat", "letzter Monat"))))
    for title, cmp in (("Letzte 7 Tage vs. 7 Tage davor", t7), (month_label, tm)):
        if cmp is None:
            rows.append((title, "Für diesen Vergleich liegen nicht genügend Daten vor."))
            continue
        rows.append(
            (
                title,
                f"Temperatur {_trend_text(cmp, 'temperature_mean', TEMPERATURE)} · "
                f"rel. Feuchte {_trend_text(cmp, 'humidity_mean', HUMIDITY)} · "
                f"Zeit mit erhöhter Feuchte {_trend_text(cmp, 'pct_elevated', HUMIDITY)}",
            )
        )
    ui.key_value_list(rows)
    caveats = [c for cmp in (t7, tm) if cmp for c in cmp.caveats[:-1]]
    if caveats:
        st.caption(" ".join(dict.fromkeys(caveats)))


def _render_room(room: Room, snap: dict[str, Any] | None, settings: AnalysisSettings) -> None:
    if snap is None:
        ui.callout("info", "Keine Sensoren", f"Für „{room.name}“ ist noch kein Sensor angelegt.")
        sel.nav_link("import", "Daten importieren")
        return
    if not snap.get("n"):
        ui.callout("info", "Noch keine Messdaten", f"Für „{room.name}“ wurden noch keine Daten importiert.")
        sel.nav_link("import", "Daten importieren")
        return
    tz = settings.timezone
    latest, s7, s30 = snap["latest"], snap["s7"], snap["s30"]
    eps, q = snap["episodes"], snap["quality"]
    sensor_note = f"„{snap['sensor'].name}“" + (
        f" (jüngste Daten von {snap['sensors']} Sensoren)" if snap["sensors"] > 1 else ""
    )
    ui.context_bar(
        [
            ("Raum", room.name),
            ("Sensor", sensor_note),
            ("Letzte Messung", fmt_datetime(snap["latest_ts"], tz)),
            ("Zeitabdeckung 30 Tage", fmt_percent(q.time_coverage_pct, 0)),
        ]
    )
    if room.description:
        st.caption(room.description)

    age = utcnow() - snap["last"].to_pydatetime()
    if age > STALE_AFTER:
        ui.callout(
            "hinweis",
            "Daten nicht aktuell",
            f"Die letzte Messung ist vom {fmt_datetime(snap['last'], tz)} ({fmt_duration(age.total_seconds())} her). "
            "Für aktuelle Werte einen neuen Export importieren.",
        )

    ui.section_header("Letzte Messung")
    # Hauptwerte groß, abgeleitete Größen kompakter daneben (gleiche Inhalte wie zuvor).
    ui.metric_grid(
        [
            {"label": "Temperatur", "value": fmt_number(latest[TEMPERATURE], 1), "unit": "°C",
             "accent": TEMPERATURE, "emphasis": True,
             "sublabel": f"Ø 7 Tage {fmt_metric(s7[TEMPERATURE].mean, TEMPERATURE)} · "
                         f"Ø 30 Tage {fmt_metric(s30[TEMPERATURE].mean, TEMPERATURE)}"},
            {"label": "Relative Luftfeuchtigkeit", "value": fmt_number(latest[HUMIDITY], 0), "unit": "%",
             "accent": HUMIDITY, "emphasis": True,
             "sublabel": f"Ø 7 Tage {fmt_metric(s7[HUMIDITY].mean, HUMIDITY)} · "
                         f"Ø 30 Tage {fmt_metric(s30[HUMIDITY].mean, HUMIDITY)}"},
        ],
        min_width=280,
    )  # fmt: skip
    ui.metric_grid(
        [
            {"label": "Taupunkt", "value": fmt_number(latest[DEW_POINT], 1), "unit": "°C", "accent": DEW_POINT,
             "sublabel": f"Ø 30 Tage {fmt_metric(s30[DEW_POINT].mean, DEW_POINT)}"},
            {"label": "Absolute Feuchte", "value": fmt_number(latest[ABS_HUMIDITY], 1), "unit": "g/m³",
             "accent": ABS_HUMIDITY, "sublabel": f"Ø 30 Tage {fmt_metric(s30[ABS_HUMIDITY].mean, ABS_HUMIDITY)}"},
            {"label": "VPD", "value": fmt_number(latest[VPD], 2), "unit": "kPa", "accent": VPD,
             "sublabel": f"Ø 30 Tage {fmt_metric(s30[VPD].mean, VPD)}"},
        ],
        min_width=190,
    )  # fmt: skip

    assessment = snap["assessment"]
    overall = assessment.overall
    ui.section_header("Einschätzung der letzten 30 Tage")
    left, right = st.columns([3, 2], gap="medium")
    with left:
        ui.callout(sel.OVERALL_LEVEL.get(overall.key, "neutral"), overall.label, " ".join(overall.reasons))
        top = sel.finding_dicts(assessment.findings, top=3, skip_ok=True)
        if top:
            ui.finding_list(top)
    with right:
        ui.metric_grid(
            [
                {"label": "Feuchtephasen (30 Tage)", "value": str(eps.count_total), "accent": HUMIDITY,
                 "sublabel": f"davon {eps.count_sustained} anhaltend · ab {fmt_number(eps.threshold)} % rF"},
                {"label": "Längste Feuchtephase", "value": fmt_duration(eps.longest_duration_s) if eps.longest else "–",
                 "sublabel": f"am {fmt_datetime(eps.longest.start, tz)}" if eps.longest else "keine Phase"},
                {"label": "Zeitabdeckung (30 Tage)", "value": fmt_number(q.time_coverage_pct, 0), "unit": "%",
                 "sublabel": f"Messvollständigkeit {fmt_percent(q.completeness_pct)} · {q.gap_count} Lücke(n)"},
            ],
            min_width=9999,  # einspaltig in der rechten Spalte
        )  # fmt: skip

    ui.section_header("Verlauf der letzten 7 Tage")
    fig = charts.timeseries_chart(
        snap["series"],
        [TEMPERATURE, HUMIDITY],
        tz=tz,
        thresholds=charts.default_thresholds(settings),
        gap_threshold_s=snap["gap_threshold_s"],
        panel_height=200,
    )
    ui.render_chart(fig, key=f"ov_ts_{room.id}")
    sel.nav_link("analyse", "Ausführliche Analyse öffnen")

    _render_trends(snap)


def _body() -> None:
    ui.page_header("Übersicht", "Aktuelle Lage aller Räume auf einen Blick.", eyebrow="Dashboard")
    repo = state.repo()
    rooms = repo.list_rooms()
    if not rooms:
        ui.empty_state(
            "Noch keine Daten",
            "Importiere einen SwitchBot-CSV-Export, um Räume und Messwerte anzulegen.",
            icon="◌",
        )
        sel.nav_link("import", "Zum Import")
        return
    settings = state.analysis_settings()
    with ui.loading("Kennzahlen werden berechnet …"):
        snaps = {r.id: _snapshot(r, settings) for r in rooms}
    if not any(s and s.get("n") for s in snaps.values()):
        ui.empty_state("Noch keine Messdaten", "Es sind Räume angelegt, aber noch keine Messungen importiert.")
        sel.nav_link("import", "Zum Import")
        return

    if len(rooms) > 1:
        ui.section_header("Räume", "Aktuelle relative Feuchte und Einschätzung der letzten 30 Tage.")
        ui.metric_grid([_room_card(r, snaps[r.id]) for r in rooms], min_width=220)
        room = sel.room_selector(rooms, key="ov_room", label="Raum im Detail")
    else:
        room = rooms[0]
    if room is not None:
        if not (snaps[room.id] or {}).get("n"):
            ui.section_header(room.name, room.description or None)
        _render_room(room, snaps[room.id], settings)


def render() -> None:
    sel.guarded(_body)

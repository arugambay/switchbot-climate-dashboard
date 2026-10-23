"""Seite „Analyse“ – Detailanalyse eines Raums bzw. Sensors in einem Zeitraum.

Geladen wird nur der gewählte Zeitraum (+ kleiner Puffer, ``selectors.load_prepared``); die
Aufbereitung (``analysis.stats.prepare``) passiert einmal und wird von allen Tabs genutzt.
Alle Analysen sind je (Sensor, Zeitraum, Einstellungen, Datenstand) gecacht.
"""

from __future__ import annotations

import dataclasses
from datetime import time, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from climate_dashboard.analysis import physics
from climate_dashboard.analysis.assessment import assess, change_after
from climate_dashboard.analysis.condensation import analyze_condensation
from climate_dashboard.analysis.humidity import analyze_humidity
from climate_dashboard.analysis.periods import day_night, monthly_overview, seasonal_overview
from climate_dashboard.analysis.quality import assess_quality
from climate_dashboard.analysis.stats import Prepared, summarize_prepared
from climate_dashboard.domain.models import (
    ABS_HUMIDITY,
    DEW_POINT,
    HUMIDITY,
    METRIC_DECIMALS,
    METRIC_LABELS,
    TEMPERATURE,
    TS,
    VPD,
    AnalysisSettings,
)
from climate_dashboard.ui import charts, state
from climate_dashboard.ui import components as ui
from climate_dashboard.ui import selectors as sel
from climate_dashboard.ui.formatting import (
    fmt_bool,
    fmt_datetime,
    fmt_duration,
    fmt_metric,
    fmt_number,
    fmt_percent,
)

HEATMAP_MAX_DAYS = 93
TABS = (
    "Verlauf",
    "Feuchtigkeit",
    "Taupunkt & Kondensation",
    "VPD & abs. Feuchte",
    "Monate & Jahreszeiten",
    "Einschätzung",
    "Datenqualität",
    "Veränderung ab Zeitpunkt",
)
CRITERIA_LABELS: dict[str, str] = {
    "humidity_elevated": "Erhöhte Feuchte ab (% rF)",
    "humidity_high": "Hohe Feuchte ab (% rF)",
    "humidity_low": "Trockene Luft unter (% rF)",
    "min_episode_minutes": "Anhaltende Phase ab (Minuten)",
    "dew_point_spread_warn": "Geringer Taupunktabstand unter (K)",
    "surface_offset_k": "Angenommene Oberflächen-Differenz (K, Annahme)",
    "elevated_share_note_pct": "Hinweis ab Zeitanteil erhöhte Feuchte (%)",
    "elevated_share_strong_pct": "Auffällig ab Zeitanteil erhöhte Feuchte (%)",
    "high_share_strong_pct": "Auffällig ab Zeitanteil hohe Feuchte (%)",
    "high_sustained_strong_count": "Auffällig ab Anzahl anhaltender Phasen mit hoher Feuchte",
    "spread_share_note_pct": "Hinweis ab Zeitanteil geringer Taupunktabstand (%)",
    "spread_share_strong_pct": "Auffällig ab Zeitanteil geringer Taupunktabstand (%)",
    "condensation_min_hours": "Mögliche Kondensationsbedingungen ab (Stunden, nur mit Oberflächenwert)",
    "mould_min_days": "Schimmelbegünstigend: mindestens so viele Tage …",
    "mould_window_days": "… innerhalb von (Tagen)",
    "mould_daily_hours_room": "… mit je mindestens (Stunden) anhaltend hoher Raumfeuchte",
    "surface_rh_critical": "Kritische Oberflächenfeuchte ab (% rF, nur mit Oberflächenwert)",
}
BASIS_LABELS: dict[str, str] = {
    "n": "Messungen",
    "covered_hours": "Abgedeckte Stunden",
    "time_coverage_pct": "Zeitabdeckung (%)",
    "quality_rating": "Datenqualität",
    "pct_elevated": "Zeitanteil erhöhte Feuchte (%)",
    "pct_high": "Zeitanteil hohe Feuchte (%)",
    "pct_dry": "Zeitanteil trockene Luft (%)",
    "elevated_episodes": "Feuchtephasen gesamt",
    "elevated_sustained": "davon anhaltend",
    "high_sustained": "Anhaltende Phasen mit hoher Feuchte",
    "longest_elevated_hours": "Längste Feuchtephase (Stunden)",
    "sustained_per_week": "Anhaltende Phasen je Woche",
    "pct_spread_below_warn": "Zeitanteil geringer Taupunktabstand (%)",
    "pct_spread_below_1k": "Zeitanteil Taupunktabstand < 1 K (%)",
    "spread_min": "Kleinster Taupunktabstand (K)",
    "pct_below_cool": "Zeitanteil unter 16 °C (%)",
    "mould_days_room": "Tage mit ≥ 6 h anhaltend hoher Feuchte",
    "mould_days_room_max_7d": "… davon höchstens innerhalb von 7 Tagen",
    "surface_basis": "Oberflächenbetrachtung",
}


# ---------------------------------------------------------------------------
# Gecachte Analysen
# ---------------------------------------------------------------------------
class Ctx:
    """Alles, was die Tabs brauchen – Analysen werden bei Bedarf berechnet und gecacht."""

    def __init__(self, sensor_id: str, start: Any, end: Any, settings: AnalysisSettings,
                 expected_s: float | None, raw: pd.DataFrame, prep: Prepared) -> None:  # fmt: skip
        self.sensor_id, self.start, self.end = sensor_id, start, end
        self.settings, self.expected_s, self.raw, self.prep = settings, expected_s, raw, prep
        self.tz = settings.timezone
        self.offset: float | None = None

    def _key(self, kind: str, *extra: Any) -> tuple:
        return (kind, self.sensor_id, self.start, self.end, sel.settings_key(self.settings), self.expected_s, *extra)

    def humidity(self):
        return sel.memo(self._key("humidity"), lambda: analyze_humidity(self.prep))

    def summaries(self):
        metrics = (TEMPERATURE, HUMIDITY, DEW_POINT, VPD, ABS_HUMIDITY, "dew_point_spread")
        return sel.memo(self._key("summaries"), lambda: summarize_prepared(self.prep, metrics))

    def quality(self):
        return sel.memo(
            self._key("quality"),
            lambda: assess_quality(self.prep.frame[[TS]], self.start, self.end, self.prep.expected_s, self.settings),
        )

    def condensation(self):
        return sel.memo(
            self._key("condensation", self.offset),
            lambda: analyze_condensation(self.prep, surface_offset_k=self.offset),
        )

    def assessment(self):
        def compute():
            s = dataclasses.replace(self.settings, surface_offset_k=self.offset)
            return assess(self.prep, s, self.quality())

        return sel.memo(self._key("assessment", self.offset), compute)

    def monthly(self):
        return sel.memo(self._key("monthly"), lambda: monthly_overview(self.prep))

    def seasonal(self):
        return sel.memo(self._key("seasonal"), lambda: seasonal_overview(self.prep))

    def day_night(self):
        return sel.memo(self._key("day_night"), lambda: day_night(self.prep))

    def plausibility(self):
        return sel.memo(self._key("plausibility"), lambda: physics.plausibility(self.raw))


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
def _tab_series(ctx: Ctx, metrics: list[str]) -> None:
    if not metrics:
        ui.callout("info", "Keine Messgröße gewählt", "Bitte oben mindestens eine Messgröße auswählen.")
        return
    hum = ctx.humidity()
    fig = charts.timeseries_chart(
        ctx.prep.frame[[TS, *metrics]],
        metrics,
        tz=ctx.tz,
        bands=charts.episode_bands(hum.elevated_episodes, high_threshold=ctx.settings.humidity_high),
        thresholds=charts.default_thresholds(ctx.settings),
        gap_threshold_s=ctx.prep.gap_threshold_s,
        range_selector=True,
    )
    ui.render_chart(fig, key="an_ts")
    notes = [
        "Farbige Hinterlegung: anhaltende Phasen mit erhöhter Feuchte "
        f"(ab {fmt_number(ctx.settings.humidity_elevated)} % rF, mindestens "
        f"{ctx.settings.min_episode_minutes} Minuten; dunkler = Höchstwert ab {fmt_number(ctx.settings.humidity_high)} %).",
        f"Unterbrochene Linie = Datenlücke (Abstand größer als {fmt_duration(ctx.prep.gap_threshold_s)}); "
        "es werden keine Werte ergänzt.",
    ]
    meta = fig.layout.meta or {}
    if isinstance(meta, dict) and meta.get("downsampled_from"):
        notes.append(
            f"Anzeige vereinfacht: {fmt_number(meta['points_shown'])} von {fmt_number(meta['downsampled_from'])} "
            "Messpunkten (Minimum und Maximum je Zeitfenster bleiben erhalten). Kennzahlen nutzen alle Messwerte."
        )
    calc = [m for m in metrics if m in (DEW_POINT, VPD, ABS_HUMIDITY) and ctx.raw[m].isna().any()]
    if calc:
        notes.append(
            "Fehlende Werte für " + ", ".join(METRIC_LABELS[m] for m in calc)
            + " wurden aus Temperatur und relativer Feuchte berechnet (Magnus-Formel)."
        )
    for n in notes:
        st.caption(n)


def _tab_humidity(ctx: Ctx) -> None:
    hum = ctx.humidity()
    b, el, hi = hum.bands, hum.elevated_summary, hum.high_summary
    s = ctx.settings
    ui.section_header("Zeitanteile in Feuchtebereichen", "Bezogen auf die Zeit mit Messwerten; Lücken zählen nicht mit.")
    values, labels = charts.band_shares_input(b)
    ui.render_chart(charts.band_share_bar(values, labels=labels), key="an_bands")
    st.caption(
        f"Abgedeckt: {fmt_duration(b.covered_hours * 3600)} von {fmt_duration(b.period_hours * 3600)}; "
        f"ohne Messwerte: {fmt_duration(b.gap_hours * 3600)}."
    )

    ui.section_header("Feuchtephasen", f"Zusammenhängende Zeiten ab {fmt_number(s.humidity_elevated)} % rF.")
    ui.metric_grid(
        [
            {"label": "Phasen gesamt", "value": str(el.count_total), "accent": HUMIDITY,
             "sublabel": f"{el.count_short} kurze Ausschläge · {el.count_sustained} anhaltend (≥ {s.min_episode_minutes} min)"},
            {"label": "Längste Phase", "value": fmt_duration(el.longest_duration_s) if el.longest else "–",
             "sublabel": (f"Höchstwert {fmt_metric(el.longest.max_rh, HUMIDITY)}" if el.longest else None)},
            {"label": "Anhaltende Phasen je Woche", "value": fmt_number(el.sustained_per_week, 1),
             "sublabel": f"an {el.days_with_sustained} Kalendertag(en)"},
            {"label": f"Phasen ab {fmt_number(s.humidity_high)} %", "value": str(hi.count_total),
             "sublabel": f"davon {hi.count_sustained} anhaltend"},
        ],
        min_width=180,
    )  # fmt: skip
    eps = hum.elevated_episodes
    if eps:
        rows = [
            {
                "Beginn": fmt_datetime(e.start, ctx.tz),
                "Ende": fmt_datetime(e.end, ctx.tz),
                "Dauer": fmt_duration(e.duration_s),
                "Art": "anhaltend" if e.is_sustained else "kurz",
                "Höchstwert": fmt_metric(e.max_rh, HUMIDITY),
                "Ø rF": fmt_metric(e.mean_rh, HUMIDITY),
                "Hinweis": "endet an Datenlücke" if e.interrupted_by_gap else
                ("beginnt mit Zeitraum" if e.at_period_start else ("reicht bis Zeitraumende" if e.at_period_end else "")),
            }
            for e in sorted(eps, key=lambda e: e.start, reverse=True)
        ]  # fmt: skip
        with st.expander(f"Liste der Phasen ({len(eps)})", expanded=len(eps) <= 8):
            sel.table(pd.DataFrame(rows), key="an_eps")
        with st.expander("Phasen als Zeitleiste"):
            ui.render_chart(
                charts.episode_timeline(
                    charts.episodes_frame(eps, high_threshold=s.humidity_high),
                    tz=ctx.tz, period=(ctx.start, ctx.end),
                ),
                key="an_ep_timeline",
            )  # fmt: skip
    else:
        ui.callout("ok", "Keine Feuchtephasen", f"Die relative Feuchte lag immer unter {fmt_number(s.humidity_elevated)} %.")

    ui.section_header("Tagesverlauf", "Stundenmittel je Kalendertag – leere Felder: keine Messung.")
    days = (ctx.end - ctx.start).total_seconds() / 86400
    if days <= HEATMAP_MAX_DAYS:
        ui.render_chart(charts.heatmap_hour_by_day(ctx.prep.frame, HUMIDITY, ctx.tz), key="an_heat")
    else:
        ui.callout("info", "Zeitraum zu lang für die Stundenansicht",
                   f"Die Stunde-×-Tag-Ansicht ist bis {HEATMAP_MAX_DAYS} Tage verfügbar.")  # fmt: skip

    dn = hum.day_night
    ui.section_header("Tag und Nacht", f"Nacht = {dn.night_hours} (Ortszeit).")
    left, right = st.columns(2, gap="large")
    with left:
        ui.render_chart(charts.day_night_chart(charts.day_night_frame(ctx.day_night(), HUMIDITY), HUMIDITY), key="an_dn")
    with right:
        ui.key_value_list(
            {
                "Ø rF tagsüber": fmt_metric(dn.day.mean_rh, HUMIDITY),
                "Ø rF nachts": fmt_metric(dn.night.mean_rh, HUMIDITY),
                "Erhöht tagsüber": fmt_percent(dn.day.pct_elevated, 1) + " der Zeit",
                "Erhöht nachts": fmt_percent(dn.night.pct_elevated, 1) + " der Zeit",
                "Hoch tagsüber": fmt_percent(dn.day.pct_high, 1) + " der Zeit",
                "Hoch nachts": fmt_percent(dn.night.pct_high, 1) + " der Zeit",
            }
        )


def _tab_condensation(ctx: Ctx) -> None:
    s = ctx.settings
    ui.callout(
        "info",
        "Was zeigt der Taupunktabstand?",
        "Der Abstand zwischen Lufttemperatur und Taupunkt gibt an, um wie viele Kelvin eine Oberfläche kälter "
        "als die Raumluft sein müsste, damit dort rechnerisch Tauwasser entstehen kann. Aus Raumluftdaten allein "
        "lässt sich nicht feststellen, ob tatsächlich Kondensation auftritt.",
    )
    use = st.toggle(
        "Kalte Oberfläche annehmen (ANNAHME, keine Messung)",
        value=s.surface_offset_k is not None,
        key="an_surface_on",
        help="Zum Beispiel eine Außenwandecke oder ein Fensterrahmen, der um einige Kelvin kälter ist als die Raumluft.",
    )
    if use:
        ctx.offset = float(
            st.number_input(
                "Angenommene Differenz Raumluft → Oberfläche (K)",
                min_value=0.5, max_value=15.0, step=0.5, value=float(s.surface_offset_k or 4.0),
                key="an_surface_offset",
            )
        )  # fmt: skip
    cond = ctx.condensation()
    if cond.surface is not None and cond.surface.basis == "assumed":
        ui.callout("hinweis", "ANNAHME", cond.surface.assumption or "")
    ui.render_chart(
        charts.dew_point_spread_chart(
            ctx.prep.frame, ctx.tz, warn_spread=s.dew_point_spread_warn, surface_offset=ctx.offset,
            gap_threshold_s=ctx.prep.gap_threshold_s,
        ),
        key="an_spread",
    )  # fmt: skip
    cards = [
        {"label": f"Zeit mit Abstand < {fmt_number(s.dew_point_spread_warn, 1)} K",
         "value": fmt_number(cond.pct_below_warn, 1), "unit": "%", "sublabel": fmt_duration(cond.hours_below_warn * 3600)},
        {"label": "Zeit mit Abstand < 1 K", "value": fmt_number(cond.pct_below_1k, 1), "unit": "%",
         "sublabel": fmt_duration(cond.hours_below_1k * 3600)},
        {"label": "Kleinster Abstand", "value": fmt_number(cond.spread_min, 1), "unit": "K",
         "sublabel": fmt_datetime(cond.spread_min_time, ctx.tz) if cond.spread_min_time is not None else None},
        {"label": "Mittlerer Abstand", "value": fmt_number(cond.spread_mean, 1), "unit": "K",
         "sublabel": f"Median {fmt_number(cond.spread_median, 1)} K"},
    ]  # fmt: skip
    ui.metric_grid(cards, min_width=180)
    surf = cond.surface
    if surf is not None:
        ui.section_header("Oberfläche (Annahme)" if surf.basis == "assumed" else "Oberfläche (gemessen)")
        ui.key_value_list(
            {
                "Zeit am/unter Taupunkt (rechnerisch)": f"{fmt_duration(surf.hours_at_or_below_dew_point * 3600)} "
                f"({fmt_percent(surf.pct_at_or_below_dew_point, 1)})",
                "Zeit mit Oberflächenfeuchte ≥ 80 %": f"{fmt_duration(surf.hours_surface_rh_critical * 3600)} "
                f"({fmt_percent(surf.pct_surface_rh_critical, 1)})",
                "Tage mit ≥ 12 h Oberflächenfeuchte ≥ 80 %": surf.days_surface_rh_critical,
                "Höchste rechnerische Oberflächenfeuchte": fmt_percent(surf.max_surface_rh),
            }
        )
    if cond.low_spread_phases:
        with st.expander(f"Phasen mit geringem Taupunktabstand ({len(cond.low_spread_phases)})"):
            sel.table(
                pd.DataFrame(
                    [
                        {"Beginn": fmt_datetime(p.start, ctx.tz), "Ende": fmt_datetime(p.end, ctx.tz),
                         "Dauer": fmt_duration(p.duration_s), "Kleinster Abstand": f"{fmt_number(p.min_spread, 1)} K",
                         "Hinweis": "endet an Datenlücke" if p.interrupted_by_gap else ""}
                        for p in cond.low_spread_phases
                    ]
                ),
                key="an_spread_phases",
            )  # fmt: skip
    for text in cond.notes:
        st.caption(text)
    for text in cond.limitations:
        if text:
            st.caption(text)


def _tab_vpd(ctx: Ctx) -> None:
    summ = ctx.summaries()
    st.caption(
        "Beschreibende Angaben. Absolute Feuchte = Wassermenge je Kubikmeter Luft; das Dampfdruckdefizit (VPD) "
        "beschreibt, wie viel Wasserdampf die Luft bei dieser Temperatur noch aufnehmen könnte. Aus diesen Werten "
        "wird hier keine Komfort- oder Schimmelbewertung abgeleitet."
    )
    cards = []
    for m in (ABS_HUMIDITY, VPD):
        x = summ[m]
        src = {"calculated": " · berechnet", "mixed": " · teilweise berechnet"}.get(x.source or "", "")
        cards.append({
            "label": f"Ø {METRIC_LABELS[m]}", "value": fmt_number(x.mean, METRIC_DECIMALS[m]),
            "unit": {ABS_HUMIDITY: "g/m³", VPD: "kPa"}[m],
            "sublabel": f"Min {fmt_metric(x.min, m)} · Max {fmt_metric(x.max, m)}{src}",
        })  # fmt: skip
    ui.metric_grid(cards, min_width=220)
    fig = charts.timeseries_chart(
        ctx.prep.frame[[TS, ABS_HUMIDITY, VPD]], [ABS_HUMIDITY, VPD], tz=ctx.tz,
        gap_threshold_s=ctx.prep.gap_threshold_s, panel_height=180,
    )  # fmt: skip
    ui.render_chart(fig, key="an_vpd")
    dn = ctx.day_night()
    ui.key_value_list(
        {
            "Ø abs. Feuchte tagsüber": fmt_metric(dn.day.means.get(ABS_HUMIDITY), ABS_HUMIDITY),
            "Ø abs. Feuchte nachts": fmt_metric(dn.night.means.get(ABS_HUMIDITY), ABS_HUMIDITY),
        }
    )


def _tab_months(ctx: Ctx) -> None:
    months = ctx.monthly()
    if not months:
        ui.callout("info", "Keine Monatswerte", "Im gewählten Zeitraum liegen keine Messungen vor.")
        return
    metric = st.segmented_control(
        "Messgröße", [HUMIDITY, TEMPERATURE, DEW_POINT, ABS_HUMIDITY], default=HUMIDITY,
        format_func=lambda m: METRIC_LABELS[m], key="an_month_metric",
    ) or HUMIDITY  # fmt: skip
    ui.section_header("Monate", "Schraffiert/hohl = unvollständiger Monat (Datenbeginn/-ende oder < 80 % Abdeckung).")
    ui.render_chart(charts.monthly_chart(charts.monthly_frame(months, metric), metric), key="an_months")
    rows = [
        {
            "Monat": m.label,
            "Abdeckung": fmt_percent(m.time_coverage_pct),
            "Vollständig": fmt_bool(m.complete),
            "Ø Temperatur": fmt_metric(m.kpis[TEMPERATURE].mean, TEMPERATURE),
            "Ø rF": fmt_metric(m.kpis[HUMIDITY].mean, HUMIDITY),
            "Zeit erhöht": fmt_percent(m.pct_elevated, 1),
            "Anhaltende Phasen": m.sustained_episodes,
            "Hinweis": " ".join(m.incomplete_reasons),
        }
        for m in months
    ]
    sel.table(pd.DataFrame(rows), key="an_month_table")
    if metric in (TEMPERATURE, HUMIDITY) and len(months) > 1:
        with st.expander("Tag und Nacht je Monat"):
            ui.render_chart(charts.day_night_chart(charts.day_night_monthly_frame(months, metric), metric),
                            key="an_dn_months")  # fmt: skip

    seasons = ctx.seasonal()
    ui.section_header("Jahreszeiten", "Meteorologisch: Frühling März–Mai, Sommer Juni–Aug., Herbst Sep.–Nov., Winter Dez.–Feb.")
    s_metric = metric if metric in (TEMPERATURE, HUMIDITY, DEW_POINT) else HUMIDITY
    ui.render_chart(charts.seasonal_chart(charts.seasonal_frame(seasons, s_metric), s_metric), key="an_seasons")
    sel.table(
        pd.DataFrame(
            [
                {"Jahreszeit": x.label, "Abdeckung (ganze Jahreszeit)": fmt_percent(x.time_coverage_pct),
                 "Ausreichend": fmt_bool(x.sufficient), "Ø rF": fmt_metric(x.kpis[HUMIDITY].mean, HUMIDITY),
                 "Zeit erhöht": fmt_percent(x.pct_elevated, 1), "Hinweis": " ".join(x.reasons)}
                for x in seasons
            ]
        ),
        key="an_season_table",
    )  # fmt: skip


def _tab_assessment(ctx: Ctx) -> None:
    a = ctx.assessment()
    o = a.overall
    ui.callout(sel.OVERALL_LEVEL.get(o.key, "neutral"), o.label, " ".join(o.reasons))
    if ctx.offset is not None:
        st.caption(f"Berücksichtigt die ANNAHME einer um {fmt_number(ctx.offset, 1)} K kälteren Oberfläche "
                   "(Tab „Taupunkt & Kondensation“).")  # fmt: skip
    ui.section_header("Beobachtungen")
    ui.finding_list(sel.finding_dicts(a.findings))
    if a.ventilation_hints:
        ui.section_header("Hinweise zum Lüften", "Nur, wenn die Daten sie stützen.")
        for i, h in enumerate(a.ventilation_hints, start=1):
            with st.container(border=True):
                ui.key_value_list(
                    [
                        ("Beobachtung", h.observation),
                        ("Mögliche Erklärung", h.possible_explanation.removeprefix("Mögliche Erklärung: ")),
                        ("Empfehlung", h.recommendation),
                        ("Unbekannt", h.unknown),
                    ]
                )
            if i < len(a.ventilation_hints):
                st.write("")
    if a.descriptive:
        ui.section_header("Beschreibend")
        for d in a.descriptive:
            st.caption(d)
    ui.section_header("Einschränkungen")
    for text in a.limitations:
        if text:
            ui.callout("neutral", text)
    with st.expander("Kriterien der Einschätzung (transparent)"):
        st.caption("Orientierungswerte, keine validierten Grenzwerte. Details: docs/FORMELN.md, Abschnitt 8 und B7.")
        sel.table(
            pd.DataFrame(
                [{"Kriterium": CRITERIA_LABELS.get(k, k), "Wert": "–" if v is None else fmt_number(v, 1).removesuffix(",0")}
                 for k, v in a.criteria.items()]
            ),
            key="an_criteria",
        )  # fmt: skip
    with st.expander("Zahlen, auf denen die Einschätzung beruht"):
        rows = []
        for k, v in a.basis.items():
            value = v if isinstance(v, str) else ("–" if v is None else fmt_number(v, 1).removesuffix(",0"))
            rows.append({"Kennzahl": BASIS_LABELS.get(k, k), "Wert": value})
        sel.table(pd.DataFrame(rows), key="an_basis")


def _tab_quality(ctx: Ctx) -> None:
    q = ctx.quality()
    level = {"gut": "ok", "eingeschränkt": "hinweis", "unzureichend": "auffaellig"}.get(q.rating, "neutral")
    ui.callout(level, f"Datenqualität: {q.rating}", " ".join(q.rating_reasons) or None)
    left, right = st.columns(2, gap="large")
    with left:
        ui.data_quality_bar(q.time_coverage_pct, label="Zeitabdeckung",
                            note="Anteil des Zeitraums, der nicht in Datenlücken liegt.")  # fmt: skip
        ui.data_quality_bar(q.completeness_pct, label="Messvollständigkeit",
                            note="Messungen ÷ (Zeitraum ÷ erwartetes Intervall), höchstens 100 %.")  # fmt: skip
    with right:
        mix = ", ".join(f"{k} min: {fmt_percent(v)}" for k, v in list(q.observed_intervals.items())[:4])
        ui.key_value_list(
            {
                "Messungen": q.n,
                "Erste / letzte Messung": f"{fmt_datetime(q.first_ts, ctx.tz)} / {fmt_datetime(q.last_ts, ctx.tz)}",
                "Erwartetes Intervall": f"{fmt_duration(q.expected_interval_s)} ({q.expected_interval_source})",
                "Beobachtetes Intervall (Median)": fmt_duration(q.observed_median_interval_s),
                "Häufigste Abstände": mix or "–",
                "Lückengrenze": fmt_duration(q.gap_threshold_s),
                "Lücken": f"{q.gap_count} · zusammen {fmt_duration(q.gap_total_s)}",
            }
        )
    if q.gaps:
        pos = {"start": "am Anfang", "inside": "", "end": "am Ende", "all": "keine Daten"}
        with st.expander(f"Lückenliste ({len(q.gaps)})", expanded=len(q.gaps) <= 5):
            sel.table(
                pd.DataFrame(
                    [{"Von": fmt_datetime(g.start, ctx.tz), "Bis": fmt_datetime(g.end, ctx.tz),
                      "Dauer": fmt_duration(g.duration_s), "Lage": pos.get(g.position, "")}
                     for g in sorted(q.gaps, key=lambda g: -g.duration_s)]
                ),
                key="an_gaps",
            )  # fmt: skip
    ui.section_header("Plausibilität: importiert vs. berechnet",
                      "Taupunkt, VPD und absolute Feuchte aus der Datei im Vergleich zur Berechnung aus T + rF.")  # fmt: skip
    pl = ctx.plausibility()
    rows = []
    for m, it in pl.items.items():
        rows.append({
            "Messgröße": METRIC_LABELS.get(m, m), "Verglichen": fmt_number(it.compared),
            "Toleranz": fmt_metric(it.tolerance, m, 2), "Über Toleranz": fmt_percent(it.exceeding_pct, 1),
            "Größte Abweichung": fmt_metric(it.max_abs_diff, m, 2),
            "Mittlere Abweichung": fmt_metric(it.mean_diff, m, 2),
        })  # fmt: skip
    sel.table(pd.DataFrame(rows), key="an_plaus")
    if pl.ok:
        st.caption("Die importierten abgeleiteten Werte passen zur Berechnung (bis auf Rundung).")
    for note in pl.notes:
        ui.callout("hinweis", "Abweichung", note)


def _tab_change(ctx: Ctx) -> None:
    st.caption(
        "Vergleicht gleich lange Zeitfenster vor und nach einem Zeitpunkt, z. B. einer Maßnahme wie neuem "
        "Lüftungsverhalten. Die Auswertung beschreibt Unterschiede, nennt aber keine Ursache."
    )
    mid = (ctx.start + (ctx.end - ctx.start) / 2).tz_convert(ctx.tz)
    lo = ctx.start.tz_convert(ctx.tz).date()
    hi = (ctx.end - pd.Timedelta(seconds=1)).tz_convert(ctx.tz).date()
    c1, c2, c3 = st.columns(3)
    with c1:
        d = st.date_input("Datum", value=mid.date(), min_value=lo, max_value=hi, format="DD.MM.YYYY", key="an_chg_date")
    with c2:
        t = st.time_input("Uhrzeit", value=time(12, 0), key="an_chg_time", step=timedelta(minutes=30))
    with c3:
        days = st.number_input("Fenster je Seite (Tage)", min_value=1, max_value=90, value=7, key="an_chg_days")
    ts = sel.local_datetime(d, t, ctx.tz)
    res = sel.memo(ctx._key("change", ts, int(days)),
                   lambda: change_after(ctx.prep, ts, ctx.settings, window_s=float(days) * 86400))  # fmt: skip
    ui.finding_list(sel.finding_dicts(res.findings))
    if res.comparison is not None:
        st.caption(
            f"Verglichen: je {fmt_duration(res.window_s)} vor und nach {fmt_datetime(res.timestamp, ctx.tz)} "
            "(begrenzt auf die vorhandenen Daten im gewählten Zeitraum)."
        )
        sel.table(
            sel.comparison_rows(res.comparison, ["temperature_mean", "humidity_mean", "dew_point_mean",
                                                 "abs_humidity_mean", "pct_elevated", "sustained_episodes",
                                                 "time_coverage_pct"]),
            key="an_chg_table",
        )  # fmt: skip
    for c in res.caveats:
        ui.callout("info", c)


# ---------------------------------------------------------------------------
# Seite
# ---------------------------------------------------------------------------
def _body() -> None:
    ui.page_header("Analyse", "Verlauf, Feuchtephasen, Taupunkt und Datenqualität eines Raums.", eyebrow="Auswertung")
    repo = state.repo()
    rooms = repo.list_rooms()
    if not rooms:
        ui.empty_state("Noch keine Daten", "Importiere zuerst einen SwitchBot-CSV-Export.")
        sel.nav_link("import", "Zum Import")
        return
    settings = state.analysis_settings()
    c1, c2 = st.columns(2)
    with c1:
        room = sel.room_selector(rooms, key="an_room")
    sensors = repo.list_sensors(room.id) if room else []
    with c2:
        sensor = sel.sensor_selector(sensors, key=f"an_sensor_{room.id}") if sensors else None
        if sensor is not None and len(sensors) == 1:
            st.text_input("Sensor", sensor.name, disabled=True, key="an_sensor_single")
    if sensor is None:
        ui.callout("info", "Kein Sensor", "Für diesen Raum ist noch kein Sensor mit Daten vorhanden.")
        sel.nav_link("import", "Daten importieren")
        return
    first, last, n = sel.sensor_extent(sensor.id)
    if not n:
        ui.callout("info", "Noch keine Messdaten", "Für diesen Sensor wurden noch keine Daten importiert.")
        sel.nav_link("import", "Daten importieren")
        return
    exp = sensor.expected_interval_s
    start, end = sel.period_selector(key="an_period", first=first, last=last, tz=settings.timezone, expected_s=exp)
    metrics = sel.metric_selector(key="an_metrics")

    with ui.loading("Messdaten werden geladen und aufbereitet …"):
        raw, prep = sel.load_prepared(sensor.id, start, end, settings, exp)
    if prep.empty or len(prep.frame) < 2:
        ui.callout("info", "Keine Messungen im Zeitraum", "Bitte einen anderen Zeitraum wählen.")
        return
    ctx = Ctx(sensor.id, start, end, settings, exp, raw, prep)
    summ = ctx.summaries()
    q = ctx.quality()
    ui.metric_grid(
        [
            {"label": "Ø Temperatur", "value": fmt_number(summ[TEMPERATURE].mean, 1), "unit": "°C", "accent": TEMPERATURE,
             "sublabel": f"{fmt_metric(summ[TEMPERATURE].min, TEMPERATURE)} – {fmt_metric(summ[TEMPERATURE].max, TEMPERATURE)}"},
            {"label": "Ø Rel. Feuchte", "value": fmt_number(summ[HUMIDITY].mean, 0), "unit": "%", "accent": HUMIDITY,
             "sublabel": f"Max {fmt_metric(summ[HUMIDITY].max, HUMIDITY)} am {fmt_datetime(summ[HUMIDITY].max_time, ctx.tz)}"},
            {"label": "Ø Taupunkt", "value": fmt_number(summ[DEW_POINT].mean, 1), "unit": "°C", "accent": DEW_POINT,
             "sublabel": f"kleinster Abstand {fmt_number(summ['dew_point_spread'].min, 1)} K"},
            {"label": "Zeitabdeckung", "value": fmt_number(q.time_coverage_pct, 0), "unit": "%",
             "sublabel": f"{fmt_number(q.n)} Messungen"},
        ]
    )  # fmt: skip
    st.caption("Alle Mittelwerte und Anteile sind zeitgewichtet (Zeit zwischen Messungen, Lücken ausgenommen).")

    tabs = st.tabs(list(TABS))
    with tabs[0]:
        _tab_series(ctx, metrics)
    with tabs[1]:
        _tab_humidity(ctx)
    with tabs[2]:
        _tab_condensation(ctx)
    with tabs[3]:
        _tab_vpd(ctx)
    with tabs[4]:
        _tab_months(ctx)
    with tabs[5]:
        _tab_assessment(ctx)
    with tabs[6]:
        _tab_quality(ctx)
    with tabs[7]:
        _tab_change(ctx)


def render() -> None:
    sel.guarded(_body)

"""Transparente, regelbasierte Einschätzung (kein Score).

Jede Aussage ist ein ``Finding`` mit Stufe (``'ok'|'hinweis'|'auffaellig'``), Kategorie, einem
sachlichen deutschen Text und den Zahlen, auf denen sie beruht (``evidence``). Texte erscheinen
nur, wenn die Daten sie stützen. Die Kriterien sind als Konstanten unten definiert und in
docs/FORMELN.md erklärt.

Sprache (Grundregel 6): Beobachtung → mögliche Bedingung → Einschränkung. Es wird nie
behauptet, dass Schimmel oder Kondensation tatsächlich vorliegt.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.analysis import physics
from climate_dashboard.analysis.condensation import (
    NO_SURFACE_NOTE,
    SURFACE_RH_CRITICAL,
    analyze_condensation,
)
from climate_dashboard.analysis.humidity import HOUR, analyze_humidity, mask_runs, max_days_in_window
from climate_dashboard.analysis.periods import ComparisonResult, compare_periods
from climate_dashboard.analysis.quality import QualityReport, ResultMixin, _ts, assess_quality
from climate_dashboard.analysis.stats import (
    Prepared,
    W,
    metric_view,
    prepare,
    slice_prepared,
    summarize_prepared,
)
from climate_dashboard.domain.models import HUMIDITY, TEMPERATURE, TS, AnalysisSettings

# --- Kriterien (Orientierungswerte, keine validierten Grenzwerte) -----------------------------
ELEVATED_SHARE_NOTE_PCT = 10.0  # ≥ 10 % der Zeit rF ≥ erhöht → Hinweis
ELEVATED_SHARE_STRONG_PCT = 30.0  # ≥ 30 % der Zeit rF ≥ erhöht → auffällig
HIGH_SHARE_STRONG_PCT = 10.0  # ≥ 10 % der Zeit rF ≥ hoch → auffällig
HIGH_SUSTAINED_STRONG_COUNT = 3  # ≥ 3 anhaltende Phasen mit rF ≥ hoch → auffällig
SPREAD_SHARE_NOTE_PCT = 5.0  # ≥ 5 % der Zeit Taupunktabstand < Warnschwelle → Hinweis
SPREAD_SHARE_STRONG_PCT = 20.0  # ≥ 20 % → auffällig
DRY_SHARE_NOTE_PCT = 20.0  # ≥ 20 % der Zeit rF < trocken → Hinweis
COOL_ROOM_C = 16.0
COOL_SHARE_NOTE_PCT = 20.0  # ≥ 20 % der Zeit unter 16 °C → Hinweis
CONDENSATION_MIN_HOURS = 1.0  # Oberfläche ≤ Taupunkt insgesamt ≥ 1 h → mögliche Kondensationsbedingungen
MOULD_MIN_DAYS = 3  # schimmelbegünstigend: an ≥ 3 Tagen innerhalb von 7 aufeinanderfolgenden Tagen …
MOULD_WINDOW_DAYS = 7
MOULD_DAILY_HOURS_ROOM = 6.0  # … je ≥ 6 h in anhaltenden Phasen mit Raum-rF ≥ humidity_high
# … oder (nur mit Oberflächenwert) an ≥ 3 Tagen je ≥ 12 h Oberflächen-rF ≥ 80 % (condensation.py)
NIGHT_DAY_DIFF_PP = 15.0
NIGHT_SHARE_MIN_PCT = 20.0
SHORT_SPIKES_PER_WEEK = 2.0
SUSTAINED_PER_WEEK_HINT = 1.0
CHANGE_DEFAULT_WINDOW_S = 7 * 86400.0
CHANGE_MIN_RH_PP = 2.0
CHANGE_MIN_T_K = 0.5
CHANGE_MIN_SHARE_PP = 5.0

OVERALL = {
    "unauffaellig": "Gemessene Bedingungen unauffällig",
    "feuchte": "Möglicherweise kritische Feuchtebedingungen",
    "kondensation": "Mögliche Kondensationsbedingungen",
    "schimmel": "Möglicherweise schimmelbegünstigende Bedingungen",
    "keine_daten": "Keine Einschätzung möglich",
}
LIMITATION_MOULD = ("Schimmelbefall kann aus Raumluftmessungen weder festgestellt noch "
                    "ausgeschlossen werden.")
LIMITATION_OUTDOOR = ("Ohne Außenmessung ist keine Aussage darüber möglich, ob und wann Lüften "
                      "die Feuchte tatsächlich gesenkt hätte.")
LIMITATION_POINT = ("Die Messung gilt für den Ort des Sensors; an Außenwänden, Ecken oder hinter "
                    "Möbeln können die Bedingungen abweichen.")


def fmt(x: float | None, decimals: int = 0) -> str:
    """Deutsche Zahlendarstellung (``1.234,5``)."""
    if x is None or not math.isfinite(x):
        return "–"
    s = f"{x:,.{decimals}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


@dataclass
class Finding(ResultMixin):
    level: str  # 'ok' | 'hinweis' | 'auffaellig'
    category: str  # 'feuchte'|'dauer'|'taupunkt'|'kondensation'|'datenqualitaet'|'veraenderung'|'trocken'|'temperatur'
    text: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class VentilationHint(ResultMixin):
    observation: str
    possible_explanation: str
    recommendation: str
    unknown: str


@dataclass
class OverallStatus(ResultMixin):
    key: str  # 'unauffaellig' | 'feuchte' | 'kondensation' | 'schimmel' | 'keine_daten'
    label: str
    reasons: list[str]
    uncertain: bool  # Datenqualität nicht „gut“


@dataclass
class Assessment(ResultMixin):
    overall: OverallStatus
    findings: list[Finding]
    limitations: list[str]
    ventilation_hints: list[VentilationHint]
    descriptive: list[str]  # rein beschreibende Angaben (z. B. VPD, absolute Feuchte)
    criteria: dict[str, Any]
    basis: dict[str, Any]  # Kennzahlen, auf denen die Einschätzung beruht

    def by_category(self, category: str) -> list[Finding]:
        return [f for f in self.findings if f.category == category]


def criteria(settings: AnalysisSettings) -> dict[str, Any]:
    return {
        "humidity_elevated": settings.humidity_elevated,
        "humidity_high": settings.humidity_high,
        "humidity_low": settings.humidity_low,
        "min_episode_minutes": settings.min_episode_minutes,
        "dew_point_spread_warn": settings.dew_point_spread_warn,
        "surface_offset_k": settings.surface_offset_k,
        "elevated_share_note_pct": ELEVATED_SHARE_NOTE_PCT,
        "elevated_share_strong_pct": ELEVATED_SHARE_STRONG_PCT,
        "high_share_strong_pct": HIGH_SHARE_STRONG_PCT,
        "high_sustained_strong_count": HIGH_SUSTAINED_STRONG_COUNT,
        "spread_share_note_pct": SPREAD_SHARE_NOTE_PCT,
        "spread_share_strong_pct": SPREAD_SHARE_STRONG_PCT,
        "condensation_min_hours": CONDENSATION_MIN_HOURS,
        "mould_min_days": MOULD_MIN_DAYS,
        "mould_window_days": MOULD_WINDOW_DAYS,
        "mould_daily_hours_room": MOULD_DAILY_HOURS_ROOM,
        "surface_rh_critical": SURFACE_RH_CRITICAL,
    }


def _mould_days_room(prep: Prepared, settings: AnalysisSettings) -> tuple[int, int]:
    """Tage (Lokalzeit) mit ≥ 6 h in anhaltenden Phasen mit rF ≥ humidity_high:
    (Anzahl insgesamt, größte Anzahl innerhalb von 7 aufeinanderfolgenden Tagen)."""
    f = metric_view(prep, HUMIDITY)
    if len(f) == 0:
        return 0, 0
    mask = f[HUMIDITY].to_numpy(dtype=float) >= settings.humidity_high
    runs = mask_runs(f, mask, settings.episode_merge_gap_minutes * 60.0)
    in_sustained = np.zeros(len(f), dtype=bool)
    for r in runs:
        if r.end_s - r.start_s >= settings.min_episode_minutes * 60.0:
            in_sustained[r.i0 : r.i1 + 1] = True
    if not in_sustained.any():
        return 0, 0
    w = np.where(in_sustained & mask, f[W].to_numpy(dtype=float), 0.0)
    days = f[TS].dt.tz_convert(prep.tz).dt.date.to_numpy()
    per_day = pd.Series(w).groupby(days).sum()
    hit = per_day.index[per_day.to_numpy() >= MOULD_DAILY_HOURS_ROOM * HOUR]
    return len(hit), max_days_in_window(list(hit), MOULD_WINDOW_DAYS)


def _share_below(prep: Prepared, metric: str, limit: float) -> float | None:
    f = metric_view(prep, metric)
    w = f[W].to_numpy(dtype=float)
    total = float(w.sum())
    if total <= 0:
        return None
    return float(w[f[metric].to_numpy(dtype=float) < limit].sum()) / total * 100.0


def assess(
    df: pd.DataFrame | Prepared | None,
    settings: AnalysisSettings | None = None,
    quality: QualityReport | None = None,
    expected_s: float | None = None,
    *,
    surface_temperature: pd.Series | None = None,
    period_start: Any = None,
    period_end: Any = None,
) -> Assessment:
    """Regelbasierte Einschätzung eines Zeitraums. ``quality`` wird bei Bedarf berechnet."""
    settings = settings or (df.settings if isinstance(df, Prepared) else AnalysisSettings())
    prep = prepare(df, settings, expected_s, None, period_start, period_end)
    s = prep.settings
    if quality is None:
        quality = assess_quality(prep.frame[[TS]], prep.period_start, prep.period_end, prep.expected_s, s)
    findings: list[Finding] = []
    limitations = [LIMITATION_MOULD, LIMITATION_OUTDOOR, LIMITATION_POINT]
    hints: list[VentilationHint] = []
    descriptive: list[str] = []
    reasons: list[str] = []

    if prep.empty or quality.n < 2:
        findings.append(Finding("hinweis", "datenqualitaet",
                                "Für diesen Zeitraum liegen zu wenige Messungen für eine Einschätzung vor.",
                                {"n": quality.n}))
        return Assessment(OverallStatus("keine_daten", OVERALL["keine_daten"], ["Zu wenige Messungen."], True),
                          findings, limitations, hints, descriptive, criteria(s), {"n": quality.n})

    hum = analyze_humidity(prep)
    cond = analyze_condensation(prep, surface_temperature=surface_temperature)
    b = hum.bands
    el, hi = hum.elevated_summary, hum.high_summary
    cool_pct = _share_below(prep, TEMPERATURE, COOL_ROOM_C)
    mould_days_room, mould_days_room_7d = _mould_days_room(prep, s)
    basis: dict[str, Any] = {
        "n": quality.n,
        "covered_hours": b.covered_hours,
        "time_coverage_pct": quality.time_coverage_pct,
        "quality_rating": quality.rating,
        "pct_elevated": b.pct_at_or_above_elevated,
        "pct_high": b.pct_at_or_above_high,
        "pct_dry": b.pct_below_low,
        "elevated_episodes": el.count_total,
        "elevated_sustained": el.count_sustained,
        "high_sustained": hi.count_sustained,
        "longest_elevated_hours": el.longest_duration_s / HOUR,
        "sustained_per_week": el.sustained_per_week,
        "pct_spread_below_warn": cond.pct_below_warn,
        "pct_spread_below_1k": cond.pct_below_1k,
        "spread_min": cond.spread_min,
        "pct_below_cool": cool_pct,
        "mould_days_room": mould_days_room,
        "mould_days_room_max_7d": mould_days_room_7d,
        "surface_basis": cond.surface.basis if cond.surface else None,
    }

    # --- Datenqualität ------------------------------------------------------------------------
    if quality.rating == "unzureichend":
        findings.append(Finding("auffaellig", "datenqualitaet",
                                "Die Datenbasis ist für diesen Zeitraum unzureichend; die Einschätzung ist "
                                "nur sehr eingeschränkt aussagekräftig.",
                                {"time_coverage_pct": quality.time_coverage_pct, "gaps": quality.gap_count}))
    elif quality.rating == "eingeschränkt":
        findings.append(Finding("hinweis", "datenqualitaet",
                                "Wegen längerer Messlücken ist die Bewertung dieses Zeitraums unsicher.",
                                {"time_coverage_pct": quality.time_coverage_pct,
                                 "longest_gap_hours": quality.longest_gap_s / HOUR,
                                 "gaps": quality.gap_count}))
    plaus = physics.plausibility(prep.frame)
    if not plaus.ok:
        findings.append(Finding("hinweis", "datenqualitaet",
                                "Importierte abgeleitete Werte (z. B. Taupunkt) weichen von der Berechnung "
                                "aus Temperatur und Luftfeuchtigkeit ab.",
                                {k: v.exceeding_pct for k, v in plaus.items.items()}))

    # --- Feuchte ------------------------------------------------------------------------------
    pe, ph = b.pct_at_or_above_elevated or 0.0, b.pct_at_or_above_high or 0.0
    ev = {"pct_elevated": pe, "hours_elevated": b.hours_at_or_above_elevated, "pct_high": ph,
          "episodes": el.count_total, "threshold": s.humidity_elevated}
    humidity_strong = pe >= ELEVATED_SHARE_STRONG_PCT or ph >= HIGH_SHARE_STRONG_PCT
    if pe >= ELEVATED_SHARE_NOTE_PCT or humidity_strong:
        text = ("Die Luftfeuchtigkeit war in diesem Zeitraum wiederholt erhöht." if el.count_total >= 2
                else "Die Luftfeuchtigkeit war in diesem Zeitraum über längere Zeit erhöht.")
        text += f" Etwa {fmt(pe)} % der gemessenen Zeit lag sie bei {fmt(s.humidity_elevated)} % oder darüber."
        findings.append(Finding("auffaellig" if humidity_strong else "hinweis", "feuchte", text, ev))
    elif el.count_sustained == 0:
        findings.append(Finding("ok", "feuchte",
                                f"Die Luftfeuchtigkeit lag überwiegend unter {fmt(s.humidity_elevated)} %.", ev))

    sustained_strong = hi.count_sustained >= HIGH_SUSTAINED_STRONG_COUNT
    if el.count_sustained >= 1:
        text = "Die erhöhten Werte hielten über mehrere Stunden an."
        text += (f" Es gab {el.count_sustained} anhaltende Phase(n) (mindestens {s.min_episode_minutes} min); "
                 f"die längste dauerte etwa {fmt(el.longest_duration_s / HOUR, 1)} Stunden.")
        findings.append(Finding("auffaellig" if sustained_strong else "hinweis", "dauer", text, {
            "sustained": el.count_sustained, "short": el.count_short,
            "longest_hours": el.longest_duration_s / HOUR, "days_with_sustained": el.days_with_sustained,
            "high_sustained": hi.count_sustained, "interrupted_by_gap": el.count_interrupted_by_gap,
        }))
    elif el.count_short >= 1:
        findings.append(Finding("ok", "dauer",
                                "Erhöhte Werte traten nur kurzzeitig auf und hielten nicht über längere Zeit an.",
                                {"short": el.count_short}))

    # --- Taupunkt / Kondensation --------------------------------------------------------------
    pw = cond.pct_below_warn or 0.0
    spread_strong = pw >= SPREAD_SHARE_STRONG_PCT
    spread_note = pw >= SPREAD_SHARE_NOTE_PCT
    if spread_note:
        findings.append(Finding("auffaellig" if spread_strong else "hinweis", "taupunkt",
                                "Der geringe Abstand zum Taupunkt könnte bei ausreichend kalten Oberflächen "
                                f"relevant sein. Etwa {fmt(pw)} % der Zeit lag die Raumluft weniger als "
                                f"{fmt(s.dew_point_spread_warn, 1)} K über dem Taupunkt.",
                                {"pct_below_warn": pw, "pct_below_1k": cond.pct_below_1k,
                                 "spread_min": cond.spread_min, "warn_k": s.dew_point_spread_warn}))
    surf = cond.surface
    condensation_possible = False
    if surf is None:
        if spread_note or humidity_strong or el.count_sustained >= 1:
            findings.append(Finding("hinweis", "kondensation", NO_SURFACE_NOTE,
                                    {"spread_min": cond.spread_min, "spread_p05": cond.spread_p05}))
    else:
        where = (f"An einer Oberfläche, die {fmt(surf.offset_k, 1)} K kälter als die Raumluft ist (Annahme),"
                 if surf.basis == "assumed" else "An der gemessenen Oberfläche")
        sev = {"basis": surf.basis, "offset_k": surf.offset_k,
               "hours_at_or_below_dew_point": surf.hours_at_or_below_dew_point,
               "pct_at_or_below_dew_point": surf.pct_at_or_below_dew_point,
               "hours_surface_rh_critical": surf.hours_surface_rh_critical,
               "days_surface_rh_critical": surf.days_surface_rh_critical,
               "max_surface_rh": surf.max_surface_rh}
        if surf.hours_at_or_below_dew_point >= CONDENSATION_MIN_HOURS:
            condensation_possible = True
            findings.append(Finding("auffaellig", "kondensation",
                                    f"{where} wären zeitweise rechnerisch Kondensationsbedingungen gegeben "
                                    f"(insgesamt etwa {fmt(surf.hours_at_or_below_dew_point, 1)} Stunden). "
                                    "Das ist eine Berechnung, keine Beobachtung von Tauwasser.", sev))
        elif surf.hours_surface_rh_critical > 0:
            findings.append(Finding("hinweis", "kondensation",
                                    f"{where} erreichte die rechnerische relative Feuchte zeitweise "
                                    f"{fmt(SURFACE_RH_CRITICAL)} % oder mehr (etwa "
                                    f"{fmt(surf.hours_surface_rh_critical, 1)} Stunden).", sev))
        else:
            findings.append(Finding("ok", "kondensation",
                                    f"{where} ergaben sich rechnerisch keine Kondensationsbedingungen.", sev))
    limitations.extend(x for x in cond.limitations if x and x not in limitations)

    # --- Trocken / Temperatur -----------------------------------------------------------------
    pd_ = b.pct_below_low or 0.0
    if pd_ >= DRY_SHARE_NOTE_PCT:
        findings.append(Finding("hinweis", "trocken",
                                f"Die Luft war zeitweise trocken: etwa {fmt(pd_)} % der Zeit lag die relative "
                                f"Luftfeuchtigkeit unter {fmt(s.humidity_low)} %.",
                                {"pct_dry": pd_, "threshold": s.humidity_low}))
    if cool_pct is not None and cool_pct >= COOL_SHARE_NOTE_PCT:
        findings.append(Finding("hinweis", "temperatur",
                                f"Der Raum war zeitweise kühl (etwa {fmt(cool_pct)} % der Zeit unter "
                                f"{fmt(COOL_ROOM_C)} °C). In kühlen Räumen sind auch Wand- und "
                                "Fensteroberflächen kälter.", {"pct_below": cool_pct, "limit_c": COOL_ROOM_C}))

    # --- Beschreibend: VPD / absolute Feuchte ---------------------------------------------------
    summ = summarize_prepared(prep, ("abs_humidity", "vpd"))
    ah, vp = summ["abs_humidity"], summ["vpd"]
    if ah.mean is not None:
        descriptive.append(f"Die absolute Luftfeuchtigkeit betrug im Mittel {fmt(ah.mean, 1)} g/m³ "
                           f"(zwischen {fmt(ah.min, 1)} und {fmt(ah.max, 1)} g/m³).")
    if vp.mean is not None:
        descriptive.append(f"Das Dampfdruckdefizit (VPD) betrug im Mittel {fmt(vp.mean, 2)} kPa.")

    # --- Lüftungshinweise (nur wenn die Daten es stützen) ----------------------------------------
    dn = hum.day_night
    if (dn.night.pct_elevated or 0) - (dn.day.pct_elevated or 0) >= NIGHT_DAY_DIFF_PP and \
            (dn.night.pct_elevated or 0) >= NIGHT_SHARE_MIN_PCT:
        hints.append(VentilationHint(
            observation=(f"Erhöhte Feuchte trat vor allem nachts auf ({fmt(dn.night.pct_elevated)} % der "
                         f"Nachtzeit gegenüber {fmt(dn.day.pct_elevated)} % tagsüber)."),
            possible_explanation="Mögliche Erklärung: Feuchteabgabe durch Atmung bei geschlossenem Raum.",
            recommendation=("Vor dem Schlafen und nach dem Aufstehen einige Minuten stoßlüften und beobachten, "
                            "ob sich die nächtlichen Werte verändern."),
            unknown="Ob und wie gelüftet wurde und wie feucht die Außenluft war, ist aus den Daten nicht bekannt.",
        ))
    weeks = (b.covered_hours / (24 * 7)) if b.covered_hours else 0.0
    if weeks > 0 and el.count_short / weeks >= SHORT_SPIKES_PER_WEEK and el.count_short > el.count_sustained:
        hints.append(VentilationHint(
            observation=(f"Es gab viele kurze Feuchtespitzen ({el.count_short} kurze Ausschläge, etwa "
                         f"{fmt(el.count_short / weeks, 1)} je Woche)."),
            possible_explanation="Mögliche Erklärung: kurzzeitige Feuchtequellen wie Duschen, Kochen oder Wäsche.",
            recommendation="Nach solchen Ereignissen kurz und kräftig lüften.",
            unknown="Die Ursache der einzelnen Spitzen ist aus den Messdaten nicht erkennbar.",
        ))
    if el.sustained_per_week is not None and el.sustained_per_week >= SUSTAINED_PER_WEEK_HINT:
        hints.append(VentilationHint(
            observation=(f"Erhöhte Feuchte hielt wiederholt über mehrere Stunden an "
                         f"(etwa {fmt(el.sustained_per_week, 1)} anhaltende Phasen je Woche)."),
            possible_explanation=("Mögliche Erklärung: Feuchte wird nicht ausreichend abgeführt, oder der Raum "
                                  "ist vergleichsweise kühl."),
            recommendation=("Mehrmals täglich einige Minuten stoßlüften, den Raum gleichmäßig temperieren und "
                            "den Verlauf danach beobachten."),
            unknown="Ohne Außenmessung ist nicht bekannt, ob die Außenluft zu diesen Zeiten trockener war.",
        ))

    # --- Gesamtstatus -------------------------------------------------------------------------
    mould_room = mould_days_room_7d >= MOULD_MIN_DAYS
    mould_surface = surf is not None and surf.days_surface_rh_critical_max_7d >= MOULD_MIN_DAYS
    if mould_room or mould_surface:
        key = "schimmel"
        if mould_room:
            reasons.append(f"An {mould_days_room} Tagen (davon bis zu {mould_days_room_7d} innerhalb einer Woche) "
                           f"lag die Raumluftfeuchte jeweils mindestens {fmt(MOULD_DAILY_HOURS_ROOM)} Stunden "
                           f"anhaltend bei {fmt(s.humidity_high)} % oder darüber.")
        if mould_surface:
            reasons.append(f"An {surf.days_surface_rh_critical} Tagen (davon bis zu "
                           f"{surf.days_surface_rh_critical_max_7d} innerhalb einer Woche) lag die rechnerische "
                           f"Oberflächenfeuchte mindestens 12 Stunden bei {fmt(SURFACE_RH_CRITICAL)} % oder darüber"
                           + (" (Annahme zur Oberflächentemperatur)." if surf.basis == "assumed" else "."))
    elif condensation_possible:
        key = "kondensation"
        reasons.append("Die Oberflächentemperatur lag rechnerisch zeitweise am oder unter dem Taupunkt"
                       + (" (Annahme)." if surf and surf.basis == "assumed" else "."))
    elif humidity_strong or sustained_strong or spread_strong:
        key = "feuchte"
        if humidity_strong:
            reasons.append(f"Erhöhte Feuchte während {fmt(pe)} % der Zeit (hoch: {fmt(ph)} %).")
        if sustained_strong:
            reasons.append(f"{hi.count_sustained} anhaltende Phasen mit {fmt(s.humidity_high)} % oder mehr.")
        if spread_strong:
            reasons.append(f"Geringer Taupunktabstand während {fmt(pw)} % der Zeit.")
    else:
        key = "unauffaellig"
        reasons.append("Keines der Kriterien für eine auffällige Einschätzung ist erfüllt.")
        if any(f.level == "hinweis" and f.category not in ("datenqualitaet",) for f in findings):
            reasons.append("Einzelne Hinweise siehe unten.")
    uncertain = quality.rating != "gut"
    if uncertain:
        reasons.append("Die Einschätzung ist wegen der Datenqualität unsicher.")

    return Assessment(
        overall=OverallStatus(key, OVERALL[key], reasons, uncertain),
        findings=findings,
        limitations=limitations,
        ventilation_hints=hints,
        descriptive=descriptive,
        criteria=criteria(s),
        basis=basis,
    )


# ---------------------------------------------------------------------------
# Veränderung vorher / nachher
# ---------------------------------------------------------------------------
@dataclass
class ChangeResult(ResultMixin):
    timestamp: pd.Timestamp
    window_s: float
    comparison: ComparisonResult | None
    findings: list[Finding]
    caveats: list[str]


def change_after(
    df: pd.DataFrame | Prepared | None,
    timestamp: Any,
    settings: AnalysisSettings | None = None,
    *,
    window_s: float | None = None,
    expected_s: float | None = None,
) -> ChangeResult:
    """Vergleicht gleich lange Zeitfenster vor und nach ``timestamp`` (z. B. einer Maßnahme).

    Standardfenster: 7 Tage, begrenzt auf die vorhandenen Daten davor/danach. Die Texte
    beschreiben Unterschiede, behaupten aber keine Ursache.
    """
    prep = prepare(df, settings, expected_s)
    s = prep.settings
    t = _ts(timestamp)
    tz = s.timezone
    caveats = [
        "Ob eine Veränderung mit einer Maßnahme zusammenhängt, lässt sich aus den Messdaten allein nicht "
        "ableiten – Wetter, Jahreszeit und Nutzung wirken ebenfalls.",
    ]
    if prep.empty or t is None or prep.period_start is None or not (prep.period_start < t < prep.period_end):
        return ChangeResult(t.tz_convert(tz) if t is not None else None, 0.0, None,
                            [Finding("hinweis", "veraenderung",
                                     "Für einen Vorher-nachher-Vergleich liegen nicht genügend Daten vor.", {})],
                            caveats)
    before = (t - prep.period_start).total_seconds()
    after = (prep.period_end - t).total_seconds()
    win = min(window_s or CHANGE_DEFAULT_WINDOW_S, before, after)
    d = pd.Timedelta(seconds=win)
    a = slice_prepared(prep, t - d, t)
    b = slice_prepared(prep, t, t + d)
    cmp = compare_periods(a, b, s, labels=("vorher", "nachher"))
    caveats.extend(c for c in cmp.caveats if c not in caveats)
    days = win / 86400.0
    span = f"{fmt(win / 3600.0)} Stunden" if days < 2 else f"{fmt(days)} Tagen"
    when = f"{t.tz_convert(tz):%d.%m.%Y %H:%M}"
    findings: list[Finding] = []

    def describe(key: str, what: str, unit: str, minimum: float, decimals: int) -> None:
        item = cmp.item(key)
        if item is None or item.delta is None:
            return
        ev = {"vorher": item.a, "nachher": item.b, "delta": item.delta, "einheit": item.delta_unit}
        if abs(item.delta) < minimum:
            findings.append(Finding("ok", "veraenderung",
                                    f"{what} war in den {span} nach dem {when} praktisch unverändert gegenüber "
                                    f"den {span} davor.", ev))
            return
        direction = "höher" if item.delta > 0 else "niedriger"
        findings.append(Finding("hinweis", "veraenderung",
                                f"In den {span} nach dem {when} lag {what[0].lower() + what[1:]} "
                                f"um {fmt(abs(item.delta), decimals)} {unit} {direction} als in den {span} davor.",
                                ev))

    describe("humidity_mean", "Die mittlere relative Luftfeuchtigkeit", "Prozentpunkte", CHANGE_MIN_RH_PP, 0)
    describe("pct_elevated", "Der Zeitanteil mit erhöhter Feuchte", "Prozentpunkte", CHANGE_MIN_SHARE_PP, 0)
    describe("temperature_mean", "Die mittlere Temperatur", "K", CHANGE_MIN_T_K, 1)
    return ChangeResult(t.tz_convert(tz), win, cmp, findings, caveats)

"""Darstellung eines Berichts-Snapshots in Streamlit.

Wird von der öffentlichen Berichtsansicht (``?r=<token>``) und der internen Vorschau
(„Berichte & Freigaben“) gemeinsam genutzt. Rendert ausschließlich aus dem Snapshot – keine
Datenbankabfragen, keine IDs, keine Tokens.

Diagramm-Adapter (Snapshot → DataFrames für ``ui.charts``) und eigenes Berichts-CSS
(Klassen ``cd-report-*``) liegen ebenfalls hier, damit ``charts.py``/``theme.py`` unverändert
bleiben.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.domain.models import DEW_POINT, HUMIDITY, TEMPERATURE, TS
from climate_dashboard.reports.builder import series_times
from climate_dashboard.ui import charts
from climate_dashboard.ui import components as ui
from climate_dashboard.ui.components import esc
from climate_dashboard.ui.formatting import (
    MISSING,
    fmt_date_range,
    fmt_datetime,
    fmt_duration,
    fmt_month,
    fmt_number,
    fmt_percent,
)

FOOTER_TEXT = "Erstellt mit SwitchBot Climate Dashboard · schreibgeschützte Ansicht"
LEVEL_BY_STATUS = {"unauffaellig": "ok", "feuchte": "hinweis", "kondensation": "hinweis",
                   "schimmel": "auffaellig", "keine_daten": "neutral"}
QUALITY_LABEL = {"gut": "gut", "eingeschränkt": "eingeschränkt", "unzureichend": "unzureichend"}
QUALITY_LEVEL = {"gut": "ok", "eingeschränkt": "hinweis", "unzureichend": "auffaellig"}

# ---------------------------------------------------------------------------
# Ergänzendes CSS für die Berichtsansicht (nur cd-report-*-Klassen; Tokens aus theme.py)
# ---------------------------------------------------------------------------
REPORT_CSS = """
/* Kopfbereich (Hero) des Berichts */
.cd-report-hero { background: var(--cd-surface); border: 1px solid var(--cd-border); border-radius: var(--cd-radius-lg);
  padding: var(--cd-space-6) var(--cd-space-6) var(--cd-space-5); box-shadow: var(--cd-shadow-md); }
.cd-report-hero__eyebrow { font-size: var(--cd-text-xs); font-weight: 650; letter-spacing: .08em; text-transform: uppercase;
  color: var(--cd-primary); margin: 0 0 var(--cd-space-2); }
.cd-report-hero__title { font-size: 2rem; font-weight: 680; letter-spacing: -0.02em; line-height: 1.15; margin: 0;
  overflow-wrap: anywhere; }
.cd-report-hero__room { font-size: var(--cd-text-lg); color: var(--cd-text-muted); margin: var(--cd-space-2) 0 0;
  overflow-wrap: anywhere; }
.cd-report-hero__meta { display: flex; flex-wrap: wrap; gap: var(--cd-space-2) var(--cd-space-5);
  margin-top: var(--cd-space-4); font-size: var(--cd-text-sm); color: var(--cd-text-muted); }
.cd-report-hero__meta b { color: var(--cd-text); font-weight: 600; }
.cd-report-hero__note { margin-top: var(--cd-space-4); padding: var(--cd-space-3) var(--cd-space-4);
  background: var(--cd-surface-muted); border-radius: var(--cd-radius-md); font-size: var(--cd-text-sm);
  color: var(--cd-text-muted); white-space: pre-line; overflow-wrap: anywhere; }
/* Inhaltsnavigation (Anker-Links) */
.cd-report-toc { display: flex; flex-wrap: wrap; gap: 6px; margin: var(--cd-space-4) 0 var(--cd-space-2); }
.cd-report-toc a { font-size: var(--cd-text-xs); font-weight: 600; text-decoration: none; color: var(--cd-text-muted);
  background: var(--cd-surface); border: 1px solid var(--cd-border); padding: 4px 10px; border-radius: var(--cd-radius-pill); }
.cd-report-toc a:hover { color: var(--cd-primary); border-color: var(--cd-primary); }
/* Kurzfazit-Karte */
.cd-report-verdict { border-radius: var(--cd-radius-lg); padding: var(--cd-space-5) var(--cd-space-6); border: 1px solid;
  margin: var(--cd-space-4) 0; }
.cd-report-verdict__label { font-size: var(--cd-text-xs); font-weight: 650; letter-spacing: .07em; text-transform: uppercase;
  margin: 0 0 var(--cd-space-1); }
.cd-report-verdict__title { font-size: var(--cd-text-xl); font-weight: 650; margin: 0 0 var(--cd-space-2); }
.cd-report-verdict__text { font-size: var(--cd-text-md); margin: 0; line-height: 1.6; }
.cd-report-verdict--ok { background: var(--cd-ok-bg); border-color: var(--cd-ok-border); }
.cd-report-verdict--ok .cd-report-verdict__label, .cd-report-verdict--ok .cd-report-verdict__title { color: var(--cd-ok-fg); }
.cd-report-verdict--hinweis { background: var(--cd-hinweis-bg); border-color: var(--cd-hinweis-border); }
.cd-report-verdict--hinweis .cd-report-verdict__label, .cd-report-verdict--hinweis .cd-report-verdict__title { color: var(--cd-hinweis-fg); }
.cd-report-verdict--auffaellig { background: var(--cd-auffaellig-bg); border-color: var(--cd-auffaellig-border); }
.cd-report-verdict--auffaellig .cd-report-verdict__label, .cd-report-verdict--auffaellig .cd-report-verdict__title { color: var(--cd-auffaellig-fg); }
.cd-report-verdict--neutral { background: var(--cd-surface-muted); border-color: var(--cd-border); }
/* Absatz-Text und Anker */
.cd-report-anchor { display: block; position: relative; top: -12px; visibility: hidden; }
.cd-report-prose { font-size: var(--cd-text-md); line-height: 1.7; max-width: 75ch; margin: 0; }
.cd-report-prose p { margin: 0 0 var(--cd-space-2); }
.cd-report-small { font-size: var(--cd-text-sm); color: var(--cd-text-muted); max-width: 80ch; margin: var(--cd-space-1) 0; }
/* Ebenen der Einschätzung (Messwerte / berechnet / mögliche Risiken / keine Feststellung) */
.cd-report-layers { display: grid; gap: var(--cd-space-3); grid-template-columns: repeat(auto-fit, minmax(min(100%, 210px), 1fr));
  margin: var(--cd-space-3) 0; }
.cd-report-layer { background: var(--cd-surface); border: 1px solid var(--cd-border); border-radius: var(--cd-radius-md);
  padding: var(--cd-space-3) var(--cd-space-4); }
.cd-report-layer__kicker { font-size: var(--cd-text-xs); font-weight: 650; text-transform: uppercase; letter-spacing: .05em;
  color: var(--cd-text-subtle); margin: 0 0 4px; }
.cd-report-layer p { font-size: var(--cd-text-sm); margin: 0; }
.cd-report-layer ul { margin: 0; padding-left: 1.1em; font-size: var(--cd-text-sm); }
/* Tabellen */
.cd-report-table-wrap { overflow-x: auto; border: 1px solid var(--cd-border); border-radius: var(--cd-radius-md);
  background: var(--cd-surface); margin: var(--cd-space-2) 0; }
.cd-report-table { width: 100%; border-collapse: collapse; font-size: var(--cd-text-sm); font-variant-numeric: tabular-nums; }
.cd-report-table th { text-align: left; font-weight: 600; color: var(--cd-text-muted); background: var(--cd-surface-muted);
  padding: 8px 12px; border-bottom: 1px solid var(--cd-border); white-space: nowrap; }
.cd-report-table td { padding: 8px 12px; border-bottom: 1px solid var(--cd-border); vertical-align: top; }
.cd-report-table tr:last-child td { border-bottom: 0; }
.cd-report-table td.num, .cd-report-table th.num { text-align: right; white-space: nowrap; }
.cd-report-table tr.is-incomplete td { background: var(--cd-hinweis-bg); }
.cd-report-table tr.is-insufficient td { color: var(--cd-text-subtle); }
.cd-report-footer-note { margin-top: var(--cd-space-2); }
@media (max-width: 640px) {
  .cd-report-hero { padding: var(--cd-space-4); }
  .cd-report-hero__title { font-size: 1.45rem; }
  .cd-report-verdict { padding: var(--cd-space-4); }
  .cd-report-table th, .cd-report-table td { padding: 6px 8px; }
}
@media print { .cd-report-toc { display: none; } }
"""


def _st():
    import streamlit as st

    return st


def _g(d: Any, *path: str, default: Any = None) -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return default if cur is None else cur


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, int | float):
        return None
    return float(x) if np.isfinite(float(x)) else None


def _unit(x: Any, d: int, unit: str) -> str:
    return f"{fmt_number(x, d)} {unit}" if _num(x) is not None else MISSING


def _included(snap: dict[str, Any], section: str) -> bool:
    sections = _g(snap, "options", "include_sections")
    return section in sections if isinstance(sections, list) else True


# ---------------------------------------------------------------------------
# Snapshot → DataFrames (Diagramm-Adapter)
# ---------------------------------------------------------------------------
def series_frame(series: dict[str, Any] | None) -> pd.DataFrame:
    """Snapshot-Zeitreihe → kanonischer DataFrame (``ts`` UTC) ohne leere Zeitfenster.

    Leere Fenster werden entfernt; die Lücken erkennt ``charts.timeseries_chart`` dann über
    ``gap_threshold_s`` (siehe ``series_gap_threshold``) und unterbricht die Linie.
    """
    times = series_times(series)
    cols = {TS: times}
    for src, dst in (("temperature", TEMPERATURE), ("humidity", HUMIDITY), ("dew_point", DEW_POINT),
                     ("humidity_min", "humidity_min"), ("humidity_max", "humidity_max")):
        vals = (series or {}).get(src) or []
        arr = np.array([np.nan if v is None else float(v) for v in vals], dtype=float)
        cols[dst] = arr if len(arr) == len(times) else np.full(len(times), np.nan)
    df = pd.DataFrame(cols)
    if df.empty:
        return df
    return df[df[[TEMPERATURE, HUMIDITY, DEW_POINT]].notna().any(axis=1)].reset_index(drop=True)


def series_gap_threshold(series: dict[str, Any] | None) -> float:
    step = (series or {}).get("step_s") or 86400
    return float(step) * 1.5


def monthly_frame(months: list[dict[str, Any]], metric: str) -> pd.DataFrame:
    rows = []
    for m in months or []:
        k = _g(m, "kpis", metric, default={})
        if m.get("insufficient") or _num(k.get("mean")) is None:
            continue
        rows.append({"month": m.get("month"), "mean": k.get("mean"), "min": k.get("min"), "max": k.get("max"),
                     "complete": bool(m.get("complete")), "coverage_pct": m.get("time_coverage_pct")})
    return pd.DataFrame(rows)


def seasonal_frame(seasons: list[dict[str, Any]], metric: str) -> pd.DataFrame:
    rows = []
    for s in seasons or []:
        k = _g(s, "kpis", metric, default={})
        if _num(k.get("mean")) is None:
            continue
        key = s.get("season")
        try:
            start_year = int(str(s.get("key", "")).split("-")[-1])
        except ValueError:
            start_year = None
        # formatting.season_label erwartet beim Winter das Jahr, in dem er endet
        year = start_year + 1 if (key == "winter" and start_year) else start_year
        rows.append({"season": key, "year": year, "mean": k.get("mean"), "min": k.get("min"), "max": k.get("max"),
                     "complete": bool(s.get("sufficient")), "coverage_pct": s.get("time_coverage_pct")})
    return pd.DataFrame(rows)


def day_night_frame(months: list[dict[str, Any]], metric: str = HUMIDITY) -> pd.DataFrame:
    rows = []
    key = {"temperature": "temperature", "humidity": "humidity"}[metric]
    for m in months or []:
        if m.get("insufficient"):
            continue
        for phase, col in (("day", f"day_mean_{key}"), ("night", f"night_mean_{key}")):
            if _num(m.get(col)) is not None:
                rows.append({"group": m.get("month"), "phase": phase, "mean": m.get(col)})
    return pd.DataFrame(rows)


def episode_bands(episodes: list[dict[str, Any]], *, sustained_only: bool = True) -> list[dict[str, Any]]:
    return [
        {"start": e["start"], "end": e["end"], "level": "auffaellig" if e.get("reaches_high") else "hinweis"}
        for e in episodes or []
        if e.get("start") and e.get("end") and (e.get("is_sustained") or not sustained_only)
    ]


# ---------------------------------------------------------------------------
# HTML-Bausteine
# ---------------------------------------------------------------------------
def _wrap(inner: str) -> str:
    return f'<div class="cd">{inner}</div>'


def _anchor(section_id: str) -> str:
    return f'<span class="cd-report-anchor" id="{esc(section_id)}"></span>'


def hero_html(snap: dict[str, Any]) -> str:
    meta = snap.get("meta") or {}
    tz = meta.get("timezone") or "Europe/Berlin"
    q = snap.get("quality") or {}
    cov = _num(q.get("time_coverage_pct"))
    rating = q.get("rating")
    period = fmt_date_range(meta.get("period_start"), meta.get("period_end"), tz, end_exclusive=True)
    parts = [
        f"<span>Zeitraum <b>{esc(period)}</b></span>",
        f"<span>Erstellt am <b>{esc(fmt_datetime(meta.get('created_at'), tz))}</b></span>",
    ]
    if meta.get("version") is not None:
        parts.append(f"<span>Version <b>{esc(meta.get('version'))}</b></span>")
    if cov is not None:
        badge = ui.status_badge_html(QUALITY_LEVEL.get(rating, "neutral"),
                                     f"Datenabdeckung {fmt_percent(cov)} · {QUALITY_LABEL.get(rating, rating or '–')}")
        parts.append(badge)
    sensors = meta.get("sensor_info")
    if sensors:
        names = "; ".join(s.get("name", "") + (f" ({s['device_info']})" if s.get("device_info") else "")
                          for s in sensors)
        parts.append(f"<span>Sensor <b>{esc(names)}</b></span>")
    room = meta.get("room_name")
    note = meta.get("note")
    return _wrap(
        '<header class="cd-report-hero">'
        '<p class="cd-report-hero__eyebrow">Klimabericht</p>'
        f'<h1 class="cd-report-hero__title">{esc(meta.get("title") or "Klimabericht")}</h1>'
        + (f'<p class="cd-report-hero__room">{esc(room)}</p>' if room else "")
        + f'<div class="cd-report-hero__meta">{"".join(parts)}</div>'
        + (f'<div class="cd-report-hero__note"><b>Anmerkung des Erstellers:</b> {esc(note)}</div>' if note else "")
        + "</header>"
    )


def toc_html(entries: list[tuple[str, str]], prefix: str) -> str:
    links = "".join(f'<a href="#{esc(prefix + sid)}">{esc(label)}</a>' for sid, label in entries)
    return _wrap(f'<nav class="cd-report-toc" aria-label="Inhalt">{links}</nav>')


def verdict_html(conclusion: dict[str, Any] | None) -> str:
    c = conclusion or {}
    lvl = c.get("level") or "neutral"
    text = esc(c.get("text") or "")
    return _wrap(
        f'<section class="cd-report-verdict cd-report-verdict--{esc(lvl)}" role="note">'
        '<p class="cd-report-verdict__label">Kurzfazit</p>'
        f'<h2 class="cd-report-verdict__title">{esc(c.get("headline") or "Fazit")}</h2>'
        f'<p class="cd-report-verdict__text">{text}</p></section>'
    )


def table_html(headers: list[str], rows: list[list[Any]], *, numeric: set[int] | None = None,
               row_classes: list[str] | None = None) -> str:
    numeric = numeric or set()
    head = "".join(f'<th class="{"num" if i in numeric else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    body = []
    for r_i, r in enumerate(rows):
        cls = (row_classes or [""] * len(rows))[r_i]
        cells = "".join(f'<td class="{"num" if i in numeric else ""}">{esc(c)}</td>' for i, c in enumerate(r))
        body.append(f'<tr class="{esc(cls)}">{cells}</tr>')
    return _wrap(f'<div class="cd-report-table-wrap"><table class="cd-report-table"><thead><tr>{head}</tr></thead>'
                 f'<tbody>{"".join(body)}</tbody></table></div>')


def _small(text: str) -> str:
    return _wrap(f'<p class="cd-report-small">{esc(text)}</p>')


# ---------------------------------------------------------------------------
# Abschnitte
# ---------------------------------------------------------------------------
def _section(st, prefix: str, sid: str, title: str, subtitle: str | None = None) -> None:
    st.html(_wrap(_anchor(prefix + sid)) + ui.section_header_html(title, subtitle))


def _render_kpis(st, snap: dict[str, Any]) -> None:
    kpis = snap.get("kpis") or {}
    th = _g(snap, "meta", "thresholds", default={})
    hum = snap.get("humidity") or {}
    pe = _g(hum, "bands", "pct_at_or_above_elevated")
    if pe is None:
        pe = _g(snap, "assessment", "basis", "pct_elevated")
    t, h, sp = kpis.get("temperature") or {}, kpis.get("humidity") or {}, kpis.get("dew_point_spread") or {}
    cards = [
        {"label": "Ø Temperatur", "value": _num(t.get("mean")), "unit": "°C", "decimals": 1, "accent": "temperature",
         "sublabel": f"{_unit(t.get('min'), 1, '°C')} bis {_unit(t.get('max'), 1, '°C')}"},
        {"label": "Ø rel. Luftfeuchtigkeit", "value": _num(h.get("mean")), "unit": "%", "decimals": 0, "accent": "humidity",
         "sublabel": f"{fmt_percent(h.get('min'))} bis {fmt_percent(h.get('max'))}"},
        {"label": f"Zeit mit ≥ {fmt_number(th.get('humidity_elevated', 60))} % rF", "value": _num(pe),
         "unit": "%", "decimals": 0, "sublabel": "Anteil der gemessenen Zeit"},
        {"label": "Ø Abstand zum Taupunkt", "value": _num(sp.get("mean")), "unit": "K", "decimals": 1,
         "accent": "dew_point", "sublabel": f"kleinster Abstand {_unit(sp.get('min'), 1, 'K')}"},
    ]
    st.html(ui.metric_grid_html(cards, min_width=200))
    st.html(_small("Messwerte: Temperatur und relative Luftfeuchtigkeit. Berechnete Kennzahlen: Mittelwerte "
                   "(zeitgewichtet), Zeitanteile und Taupunkt."))


def _render_assessment(st, snap: dict[str, Any]) -> None:
    a = snap.get("assessment") or {}
    overall = a.get("overall") or {}
    lvl = LEVEL_BY_STATUS.get(overall.get("key"), "neutral")
    reasons = overall.get("reasons") or []
    st.html(ui.callout_html(lvl, overall.get("label") or "Einschätzung", " ".join(reasons) or None))
    kpis = snap.get("kpis") or {}
    t, h = kpis.get("temperature") or {}, kpis.get("humidity") or {}
    basis = a.get("basis") or {}
    risks = [f.get("text") for f in a.get("findings") or [] if f.get("level") in ("hinweis", "auffaellig")
             and f.get("category") != "datenqualitaet"]
    layers = [
        ("Messwerte", f"<p>Ø {esc(_unit(t.get('mean'), 1, '°C'))} · Ø {esc(fmt_percent(h.get('mean')))} rF · "
                      f"{esc(fmt_number(_g(snap, 'quality', 'n')))} Messungen</p>"),
        ("Berechnete Kennzahlen",
         f"<p>{esc(fmt_percent(basis.get('pct_elevated')))} der Zeit erhöhte Feuchte · "
         f"{esc(fmt_number(basis.get('elevated_sustained') or 0))} anhaltende Phasen · "
         f"{esc(fmt_percent(basis.get('pct_spread_below_warn')))} der Zeit geringer Taupunktabstand</p>"),
        ("Mögliche Risiken",
         "<ul>" + "".join(f"<li>{esc(r)}</li>" for r in risks[:4]) + "</ul>" if risks
         else "<p>Aus den Messwerten ergeben sich keine Hinweise auf ungünstige Feuchtebedingungen.</p>"),
        ("Keine Schadensfeststellung",
         "<p>Die Einschätzung beschreibt Bedingungen der Raumluft. Ob Schimmel oder Feuchteschäden vorliegen, "
         "lässt sich nur vor Ort beurteilen.</p>"),
    ]
    st.html(_wrap('<div class="cd-report-layers">' + "".join(
        f'<div class="cd-report-layer"><p class="cd-report-layer__kicker">{esc(k)}</p>{v}</div>' for k, v in layers)
        + "</div>"))
    findings = a.get("findings") or []
    if findings:
        st.html(ui.finding_list_html([{"level": f.get("level"), "category": _CATEGORY.get(f.get("category")),
                                       "text": f.get("text")} for f in findings]))
    hints = a.get("ventilation_hints") or []
    if hints:
        st.markdown("**Hinweise zum Lüften**")
        for hnt in hints:
            st.html(ui.callout_html("info", hnt.get("observation") or "",
                                    f"{hnt.get('possible_explanation', '')} Empfehlung: "
                                    f"{hnt.get('recommendation', '')} Unbekannt: {hnt.get('unknown', '')}"))
    st.html(_small("Die Kriterien sind Orientierungswerte (keine Grenzwerte im rechtlichen Sinn) und werden im "
                   "Abschnitt „Datenqualität & Einschränkungen“ eingeordnet."))


_CATEGORY = {"feuchte": "Luftfeuchtigkeit", "dauer": "Dauer", "taupunkt": "Taupunkt",
             "kondensation": "Oberflächen", "datenqualitaet": "Datenqualität", "trocken": "Trockene Luft",
             "temperatur": "Temperatur", "veraenderung": "Veränderung"}


def _render_timeseries(st, snap: dict[str, Any], key: str) -> None:
    series = snap.get("series")
    df = series_frame(series)
    if df.empty:
        st.html(ui.callout_html("neutral", "Keine Messwerte im Zeitraum."))
        return
    tz = _g(snap, "meta", "timezone", default="Europe/Berlin")
    th = SimpleNamespace(**(_g(snap, "meta", "thresholds", default={}) or {}))
    hum = snap.get("humidity") or {}
    bands = episode_bands(hum.get("episodes") or [])
    fig = charts.timeseries_chart(
        df, (TEMPERATURE, HUMIDITY), tz, bands=bands, thresholds=charts.default_thresholds(th),
        gap_threshold_s=series_gap_threshold(series), rangeslider=True, range_selector=True,
    )
    ui.render_chart(fig, key=f"{key}_ts")
    note = (f"{(series or {}).get('label', 'stündlich').capitalize()}e Mittelwerte. Unterbrochene Linien = "
            "Datenlücken (keine Werte ergänzt). Farbig hinterlegt: anhaltende Phasen erhöhter Feuchte "
            "(rot: hohe Feuchte erreicht). Zoomen per Auswahl im Diagramm oder über den Schieberegler.")
    st.html(_small(note))
    if (series or {}).get("note"):
        st.html(_small(series["note"]))


def _render_humidity(st, snap: dict[str, Any], key: str) -> None:
    hum = snap.get("humidity") or {}
    tz = _g(snap, "meta", "timezone", default="Europe/Berlin")
    th = _g(snap, "meta", "thresholds", default={})
    bands = _g(hum, "bands", "bands", default=[])
    mapping = {"trocken": "dry", "normal": "normal", "erhoeht": "elevated", "hoch": "high"}
    shares = {mapping.get(b.get("key"), b.get("key")): (b.get("pct") or 0.0) for b in bands}
    labels = {mapping.get(b.get("key"), b.get("key")): b.get("label") for b in bands}
    if any(v for v in shares.values()):
        ui.render_chart(charts.band_share_bar(shares, labels=labels, value_is_fraction=False), key=f"{key}_bands")
        st.html(_small("Anteile beziehen sich auf die Zeit mit Messwerten; Datenlücken sind nicht enthalten."))
    es = hum.get("elevated_summary") or {}
    el = th.get("humidity_elevated", 60)
    total = int(es.get("count_total") or 0)
    st.markdown(f"**Phasen mit {fmt_number(el)} % rF oder mehr – wann, wie lange, wie oft**")
    if total == 0:
        st.html(ui.callout_html("ok", f"Keine Phase mit {fmt_number(el)} % oder mehr im Zeitraum."))
        return
    st.html(ui.metric_grid_html([
        {"label": "Phasen insgesamt", "value": str(total), "sublabel": f"davon {es.get('count_short', 0)} kurz"},
        {"label": "Anhaltende Phasen", "value": str(es.get("count_sustained", 0)),
         "sublabel": f"mindestens {fmt_duration((es.get('min_sustained_minutes') or 120) * 60)}"},
        {"label": "Längste Phase", "value": fmt_duration(es.get("longest_duration_s"))},
        {"label": "Anhaltend je Woche", "value": _num(es.get("sustained_per_week")), "decimals": 1},
    ], min_width=170))
    eps = hum.get("episodes") or []
    if eps:
        rows_tl = [{"start": e["start"], "end": e["end"], "max": e.get("max_rh"),
                    "level": "auffaellig" if e.get("reaches_high") else "hinweis",
                    "label": "anhaltend" if e.get("is_sustained") else "kurz"} for e in eps]
        period = (_g(snap, "meta", "period_start"), _g(snap, "meta", "period_end"))
        ui.render_chart(charts.episode_timeline(rows_tl, tz=tz, period=period), key=f"{key}_eps")
        rows, classes = [], []
        for e in eps:
            notes = []
            if e.get("interrupted_by_gap"):
                notes.append("endet an Datenlücke (Dauer evtl. länger)")
            if e.get("at_period_start"):
                notes.append("begann evtl. vor dem Zeitraum")
            rows.append([fmt_datetime(e.get("start"), tz), fmt_datetime(e.get("end"), tz),
                         fmt_duration(e.get("duration_s")), fmt_percent(e.get("max_rh")),
                         "anhaltend" if e.get("is_sustained") else "kurzer Ausschlag", "; ".join(notes) or "–"])
            classes.append("")
        st.html(table_html(["Beginn", "Ende", "Dauer", "Höchstwert", "Art", "Hinweis"], rows, numeric={2, 3},
                           row_classes=classes))
        listed, tot = hum.get("episodes_listed") or len(eps), hum.get("episodes_total") or len(eps)
        if tot > listed:
            st.html(_small(f"Gezeigt sind die {listed} längsten von {tot} Phasen (zeitlich sortiert)."))


def _render_condensation(st, snap: dict[str, Any], key: str) -> None:
    cond = snap.get("condensation") or {}
    tz = _g(snap, "meta", "timezone", default="Europe/Berlin")
    st.html(_wrap('<div class="cd-report-prose"><p>Der <b>Taupunkt</b> ist die Temperatur, ab der Feuchtigkeit '
                  "aus der Luft an Oberflächen kondensieren kann. Der <b>Abstand</b> zwischen Raumtemperatur und "
                  "Taupunkt sagt, um wie viel Grad eine Oberfläche – z. B. eine Fensterscheibe oder eine "
                  "Außenwandecke – kälter als die Raumluft sein müsste, damit dort rechnerisch Tauwasser "
                  "entsteht. Je kleiner der Abstand, desto eher.</p></div>"))
    warn = cond.get("warn_threshold_k") or 3.0
    st.html(ui.metric_grid_html([
        {"label": "Ø Abstand zum Taupunkt", "value": _num(cond.get("spread_mean")), "unit": "K", "decimals": 1},
        {"label": "Kleinster Abstand", "value": _num(cond.get("spread_min")), "unit": "K", "decimals": 1,
         "sublabel": fmt_datetime(cond.get("spread_min_time"), tz) if cond.get("spread_min_time") else None},
        {"label": f"Zeit mit Abstand < {fmt_number(warn, 1)} K", "value": _num(cond.get("pct_below_warn")),
         "unit": "%", "decimals": 1},
    ], min_width=190))
    df = series_frame(snap.get("series"))
    surface = cond.get("surface")
    if not df.empty:
        fig = charts.dew_point_spread_chart(
            df, tz, warn_spread=float(warn),
            surface_offset=surface.get("offset_k") if surface and surface.get("basis") == "assumed" else None,
            gap_threshold_s=series_gap_threshold(snap.get("series")),
        )
        ui.render_chart(fig, key=f"{key}_dew")
    if surface:
        lvl = "hinweis" if (surface.get("hours_at_or_below_dew_point") or 0) >= 1 else "info"
        title = (f"Annahme: Oberfläche {fmt_number(surface.get('offset_k'), 1)} K kälter als die Raumluft"
                 if surface.get("basis") == "assumed" else "Gemessene Oberflächentemperatur")
        st.html(ui.callout_html(lvl, title,
                                f"Rechnerisch am oder unter dem Taupunkt: "
                                f"{fmt_number(surface.get('hours_at_or_below_dew_point'), 1)} Std. "
                                f"({fmt_percent(surface.get('pct_at_or_below_dew_point'), 1)}). Rechnerische "
                                f"Oberflächenfeuchte ≥ 80 %: {fmt_number(surface.get('hours_surface_rh_critical'), 1)}"
                                f" Std. {surface.get('assumption') or ''}"))
    for lim in cond.get("limitations") or []:
        if surface and lim == surface.get("assumption"):
            continue
        st.html(ui.callout_html("neutral", lim))


def _month_status(m: dict[str, Any]) -> str:
    if m.get("insufficient"):
        return "zu wenig Daten"
    return "vollständig" if m.get("complete") else "unvollständig"


def _render_monthly(st, snap: dict[str, Any], key: str) -> None:
    months = snap.get("monthly") or []
    th = _g(snap, "meta", "thresholds", default={})
    hum_df = monthly_frame(months, HUMIDITY)
    tmp_df = monthly_frame(months, TEMPERATURE)
    if not hum_df.empty:
        tabs = st.tabs(["Relative Luftfeuchtigkeit", "Temperatur"])
        with tabs[0]:
            ui.render_chart(charts.monthly_chart(hum_df, HUMIDITY), key=f"{key}_mh")
        with tabs[1]:
            ui.render_chart(charts.monthly_chart(tmp_df, TEMPERATURE), key=f"{key}_mt")
    rows, classes = [], []
    for m in months:
        ins = m.get("insufficient")
        rows.append([
            m.get("label") or fmt_month(m.get("month")),
            MISSING if ins else _unit(_g(m, "kpis", "temperature", "mean"), 1, "°C"),
            MISSING if ins else fmt_percent(_g(m, "kpis", "humidity", "mean")),
            MISSING if ins else fmt_percent(_g(m, "kpis", "humidity", "max")),
            MISSING if ins else fmt_percent(m.get("pct_elevated")),
            MISSING if ins else fmt_number(m.get("sustained_episodes") or 0),
            fmt_percent(m.get("time_coverage_pct")),
            _month_status(m),
        ])
        classes.append("is-insufficient is-incomplete" if ins else ("" if m.get("complete") else "is-incomplete"))
    st.html(table_html(["Monat", "Ø Temperatur", "Ø rF", "Höchste rF",
                        f"Zeit ≥ {fmt_number(th.get('humidity_elevated', 60))} %", "Anhaltende Phasen",
                        "Abdeckung", "Datenbasis"], rows, numeric={1, 2, 3, 4, 5, 6}, row_classes=classes))
    reasons = [f"{m.get('label')}: {' '.join(m.get('incomplete_reasons') or [])}" for m in months
               if not m.get("complete") and m.get("incomplete_reasons")]
    st.html(_small("Hinterlegte Monate sind unvollständig (nur teilweise im Zeitraum oder unter 80 % Zeitabdeckung) "
                   "und nur eingeschränkt vergleichbar. Bei „zu wenig Daten“ werden keine Werte angegeben."))
    for r in reasons[:6]:
        st.html(_small(r))


def _render_seasonal(st, snap: dict[str, Any], key: str) -> None:
    seasons = snap.get("seasonal") or []
    df = seasonal_frame(seasons, HUMIDITY)
    if len(df) > 1:
        ui.render_chart(charts.seasonal_chart(df, HUMIDITY), key=f"{key}_season")
    rows, classes = [], []
    for s in seasons:
        rows.append([s.get("label", ""), _unit(_g(s, "kpis", "temperature", "mean"), 1, "°C"),
                     fmt_percent(_g(s, "kpis", "humidity", "mean")), fmt_percent(s.get("pct_elevated")),
                     fmt_number(s.get("sustained_episodes") or 0), fmt_percent(s.get("time_coverage_pct")),
                     "ausreichend" if s.get("sufficient") else
                     ("nur Teil der Jahreszeit" if s.get("partial") else "lückenhaft")])
        classes.append("" if s.get("sufficient") else "is-incomplete")
    st.html(table_html(["Jahreszeit", "Ø Temperatur", "Ø rF", "Zeit erhöht", "Anhaltende Phasen", "Abdeckung",
                        "Datenbasis"], rows, numeric={1, 2, 3, 4, 5}, row_classes=classes))
    st.html(_small("Meteorologische Jahreszeiten (Winter = Dezember bis Februar). Die Abdeckung bezieht sich auf die "
                   "ganze Jahreszeit; unter 80 % sind die Werte nicht repräsentativ."))


def _render_day_night(st, snap: dict[str, Any], key: str) -> None:
    dn = snap.get("day_night") or {}
    day, night = dn.get("day") or {}, dn.get("night") or {}
    diff = dn.get("night_minus_day") or {}
    df = day_night_frame(snap.get("monthly") or [], HUMIDITY)
    if not df.empty and df["group"].nunique() > 1:
        ui.render_chart(charts.day_night_chart(df, HUMIDITY), key=f"{key}_dn")

    def signed(x: Any, d: int, unit: str) -> str:
        return f"{fmt_number(x, d, sign=True)} {unit}" if _num(x) is not None else MISSING

    rows = [
        ["Ø Temperatur", _unit(_g(day, "means", "temperature"), 1, "°C"),
         _unit(_g(night, "means", "temperature"), 1, "°C"), signed(diff.get("temperature"), 1, "K")],
        ["Ø relative Luftfeuchtigkeit", fmt_percent(_g(day, "means", "humidity")),
         fmt_percent(_g(night, "means", "humidity")), signed(diff.get("humidity"), 0, "Prozentpunkte")],
        ["Ø Taupunkt", _unit(_g(day, "means", "dew_point"), 1, "°C"), _unit(_g(night, "means", "dew_point"), 1, "°C"),
         signed(diff.get("dew_point"), 1, "K")],
        ["Zeitanteil erhöhte Feuchte", fmt_percent(day.get("pct_elevated")), fmt_percent(night.get("pct_elevated")),
         "–"],
    ]
    st.html(table_html(["", "Tag", f"Nacht ({dn.get('night_hours', '')})", "Nacht minus Tag"], rows,
                       numeric={1, 2, 3}))


def _render_quality(st, snap: dict[str, Any]) -> None:
    q = snap.get("quality") or {}
    tz = _g(snap, "meta", "timezone", default="Europe/Berlin")
    rating = q.get("rating")
    st.html(ui.data_quality_bar_html(_num(q.get("time_coverage_pct")),
                                     note="Anteil des Zeitraums, für den Messwerte vorliegen."))
    st.html(ui.key_value_list_html([
        ("Einstufung", QUALITY_LABEL.get(rating, rating or MISSING)),
        ("Messungen", fmt_number(q.get("n"))),
        ("Messvollständigkeit", fmt_percent(q.get("completeness_pct"), 1)),
        ("Messintervall", fmt_duration(q.get("expected_interval_s"))),
        ("Datenlücken", f"{q.get('gap_count', 0)} (zusammen {fmt_duration(q.get('gap_total_s'))})"),
    ]))
    for r in q.get("rating_reasons") or []:
        st.html(ui.callout_html("hinweis", r))
    gaps = q.get("gaps") or []
    if gaps:
        st.html(table_html(["Lücke von", "bis", "Dauer"],
                           [[fmt_datetime(g.get("start"), tz), fmt_datetime(g.get("end"), tz),
                             fmt_duration(g.get("duration_s"))] for g in gaps], numeric={2}))
        if (q.get("gap_count") or 0) > len(gaps):
            st.html(_small(f"Gezeigt sind die {len(gaps)} längsten von {q.get('gap_count')} Lücken."))
    lims: list[str] = []
    for src in (_g(snap, "assessment", "limitations", default=[]), _g(snap, "conclusion", "limitations", default=[])):
        for x in src:
            if x and x not in lims:
                lims.append(x)
    if lims:
        st.markdown("**Einschränkungen**")
        st.html(_wrap('<div class="cd-report-prose"><ul>' + "".join(f"<li>{esc(x)}</li>" for x in lims)
                      + "</ul></div>"))


# ---------------------------------------------------------------------------
# Gesamtansicht
# ---------------------------------------------------------------------------
def render_report(
    snapshot: dict[str, Any],
    *,
    key: str = "report",
    pdf_bytes: Callable[[], bytes] | None = None,
    pdf_file_name: str = "klimabericht.pdf",
) -> None:
    """Rendert den kompletten Bericht. ``pdf_bytes``: Funktion, die das PDF liefert (lazy,
    wird erst beim Klick auf den Download-Button aufgerufen)."""
    st = _st()
    snap = snapshot or {}
    st.html(f"<style>{REPORT_CSS}</style>")
    prefix = f"{key}-"
    st.html(hero_html(snap))

    plan: list[tuple[str, str, bool]] = [
        ("summary", "Zusammenfassung", True),
        ("kpis", "Kennzahlen", _included(snap, "kpis") and bool(snap.get("kpis"))),
        ("assessment", "Einschätzung", _included(snap, "assessment") and bool(snap.get("assessment"))),
        ("timeseries", "Zeitverlauf", _included(snap, "timeseries") and bool(snap.get("series"))),
        ("humidity", "Feuchtephasen", bool(snap.get("humidity"))),
        ("condensation", "Taupunkt", bool(snap.get("condensation"))),
        ("monthly", "Monate", bool(snap.get("monthly"))),
        ("seasonal", "Jahreszeiten", bool(snap.get("seasonal"))),
        ("day_night", "Tag & Nacht", bool(snap.get("day_night"))),
        ("data_quality", "Datenqualität", True),
        ("conclusion", "Fazit", True),
    ]
    visible = [(sid, label) for sid, label, ok in plan if ok]
    st.html(toc_html(visible, prefix))
    st.html(verdict_html(snap.get("conclusion")))
    if pdf_bytes is not None:
        st.download_button("PDF herunterladen", data=pdf_bytes, file_name=pdf_file_name, mime="application/pdf",
                           icon=":material/download:", key=f"{key}_pdf", on_click="ignore")

    ids = {sid for sid, _ in visible}
    _section(st, prefix, "summary", "Zusammenfassung")
    summary = snap.get("summary") or []
    st.html(_wrap('<div class="cd-report-prose">' + "".join(f"<p>{esc(s)}</p>" for s in summary) + "</div>")
            if summary else ui.callout_html("neutral", "Keine Zusammenfassung verfügbar."))
    if "kpis" in ids:
        _section(st, prefix, "kpis", "Kennzahlen", "Zeitgewichtete Werte über den gesamten Zeitraum")
        _render_kpis(st, snap)
    if "assessment" in ids:
        _section(st, prefix, "assessment", "Einschätzung",
                 "Regelbasiert und transparent – Beobachtung, mögliche Bedingung, Einschränkung")
        _render_assessment(st, snap)
    if "timeseries" in ids:
        _section(st, prefix, "timeseries", "Zeitverlauf", "Temperatur und relative Luftfeuchtigkeit")
        _render_timeseries(st, snap, key)
    if "humidity" in ids:
        _section(st, prefix, "humidity", "Luftfeuchtigkeit & Feuchtephasen")
        _render_humidity(st, snap, key)
    if "condensation" in ids:
        _section(st, prefix, "condensation", "Taupunkt & Oberflächen")
        _render_condensation(st, snap, key)
    if "monthly" in ids:
        _section(st, prefix, "monthly", "Monatsübersicht")
        _render_monthly(st, snap, key)
    if "seasonal" in ids:
        _section(st, prefix, "seasonal", "Jahreszeiten")
        _render_seasonal(st, snap, key)
    if "day_night" in ids:
        _section(st, prefix, "day_night", "Tag und Nacht",
                 f"Nacht = {_g(snap, 'day_night', 'night_hours', default='22–6 Uhr')}")
        _render_day_night(st, snap, key)
    _section(st, prefix, "data_quality", "Datenqualität & Einschränkungen")
    _render_quality(st, snap)
    _section(st, prefix, "conclusion", "Fazit")
    c = snap.get("conclusion") or {}
    st.html(ui.callout_html(c.get("level") or "neutral", c.get("headline") or "Fazit", c.get("text") or None))
    st.html(ui.report_footer_html(FOOTER_TEXT)
            + _wrap('<p class="cd-report-small cd-report-footer-note">Dieser Bericht beruht auf Raumluftmessungen '
                    "und ersetzt keine Begutachtung vor Ort.</p>"))


def snapshot_key(snapshot: dict[str, Any]) -> str:
    """Stabiler Schlüssel für Caches (z. B. PDF)."""
    return json.dumps(snapshot, sort_keys=True, ensure_ascii=False, allow_nan=False)


__all__ = ["render_report", "series_frame", "monthly_frame", "seasonal_frame", "day_night_frame", "hero_html",
           "verdict_html", "REPORT_CSS", "FOOTER_TEXT"]

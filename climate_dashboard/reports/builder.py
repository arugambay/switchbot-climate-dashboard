"""Berichts-Snapshot: vollständig berechnetes, JSON-serialisierbares Berichtsdokument.

Ein Snapshot wird einmal berechnet und als ``report_versions.content`` gespeichert. Die
öffentliche Ansicht, die interne Vorschau und der PDF-Export rendern ausschließlich daraus –
nie aus Rohdaten. Spätere Importe ändern einen gespeicherten Snapshot daher nie.

Struktur (``schema_version: 1``)
--------------------------------
``meta``          Titel, Raumname, Zeitraum, Erstellzeit, Version, Zeitzone, Fingerprint,
                  Schwellenwerte, Notiz, Sensorinfo (nur mit ``show_sensor_info``)
``options``       Gewählte Abschnitte und Optionen (ohne interne IDs)
``quality``       Datenqualität (Abdeckung, Lücken – die 20 längsten + Gesamtzahl)
``kpis``          Zeitgewichtete Kennzahlen je Messgröße (+ Taupunktabstand)
``humidity``      Feuchtebereiche, Phasen (20 relevanteste) + Zusammenfassungen, Tag/Nacht-Anteile
``condensation``  Taupunktabstand, optionale Oberflächenbetrachtung (ANNAHME)
``assessment``    Gesamtstatus, Befunde, Einschränkungen, Lüftungshinweise
``monthly`` / ``seasonal`` / ``day_night``  Zeitraum-Übersichten
``series``        Diagrammreihe (stündlich; > 2 Jahre: 3-stündlich; > 5 Jahre: täglich),
                  Lücken als ``null``
``summary``       Zusammenfassung (Sätze) – siehe ``reports.summary``
``conclusion``    Fazit (Überschrift, Text, Stufe)

Nicht ausgewählte Abschnitte sind ``None``. Interne IDs (Raum, Sensor, Nutzer) kommen nie vor.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.analysis.assessment import assess
from climate_dashboard.analysis.condensation import analyze_condensation
from climate_dashboard.analysis.humidity import analyze_humidity
from climate_dashboard.analysis.periods import aggregate, day_night, monthly_overview, seasonal_overview
from climate_dashboard.analysis.quality import _ts, assess_quality, jsonable, to_seconds
from climate_dashboard.analysis.stats import DEW_POINT_SPREAD, prepare, summarize_prepared
from climate_dashboard.domain.models import (
    DEW_POINT,
    HUMIDITY,
    METRICS,
    SENSOR_ID,
    TEMPERATURE,
    TS,
    AnalysisSettings,
    Room,
    Sensor,
    utcnow,
)

SCHEMA_VERSION = 1
GENERATOR = "SwitchBot Climate Dashboard"

SECTIONS: dict[str, str] = {
    "kpis": "Kennzahlen",
    "timeseries": "Zeitverlauf",
    "humidity": "Luftfeuchtigkeit & Feuchtephasen",
    "condensation": "Taupunkt",
    "monthly": "Monatsübersicht",
    "seasonal": "Jahreszeiten",
    "day_night": "Tag und Nacht",
    "assessment": "Einschätzung",
    "data_quality": "Datenqualität",
}
ALL_SECTIONS: tuple[str, ...] = tuple(SECTIONS)

MAX_EPISODES = 20
MAX_GAPS = 20
MAX_LOW_SPREAD_PHASES = 20
SERIES_HOURLY_MAX_DAYS = 2 * 366  # bis ca. 2 Jahre stündlich
SERIES_3H_MAX_DAYS = 5 * 366  # bis ca. 5 Jahre 3-stündlich, darüber täglich
SERIES_DECIMALS = 2
FINGERPRINT_DECIMALS = 4


# ---------------------------------------------------------------------------
# Optionen
# ---------------------------------------------------------------------------
@dataclass
class ReportOptions:
    """Vom Ersteller gewählte Berichtsoptionen."""

    title: str = "Klimabericht"
    include_sections: tuple[str, ...] = ALL_SECTIONS
    show_sensor_info: bool = False  # Sensornamen/Geräteinfo nur, wenn ausdrücklich freigegeben
    surface_offset_k: float | None = None  # ANNAHME: Oberfläche = Raumluft − Offset
    note: str | None = None  # freier Text des Erstellers

    def __post_init__(self) -> None:
        self.include_sections = tuple(s for s in ALL_SECTIONS if s in set(self.include_sections or ()))
        self.title = (self.title or "").strip() or "Klimabericht"
        self.note = (self.note or "").strip() or None
        if self.surface_offset_k is not None:
            value = float(self.surface_offset_k)
            self.surface_offset_k = value if math.isfinite(value) and value > 0 else None

    def includes(self, section: str) -> bool:
        return section in self.include_sections

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "include_sections": list(self.include_sections),
            "show_sensor_info": bool(self.show_sensor_info),
            "surface_offset_k": self.surface_offset_k,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ReportOptions:
        data = data or {}
        return cls(
            title=data.get("title") or "Klimabericht",
            include_sections=tuple(data.get("include_sections") or ALL_SECTIONS),
            show_sensor_info=bool(data.get("show_sensor_info", False)),
            surface_offset_k=data.get("surface_offset_k"),
            note=data.get("note"),
        )


@dataclass
class _Ctx:
    tz: str
    sections: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------
def _period_iso(value: Any) -> str:
    t = _ts(value)
    return t.isoformat() if t is not None else ""


def data_fingerprint(df: pd.DataFrame | None, sensor_ids: list[str], start: Any, end: Any) -> str:
    """SHA-256 über Sensor-IDs, Zeitraum, Anzahl und alle Messungen in ``[start, end)``.

    Messungen werden nach (Sensor, Zeit) sortiert, Zeit als ganze Millisekunden, Werte auf
    vier Nachkommastellen. Gleiche Daten ergeben denselben Wert; jede neue, gelöschte oder
    geänderte Messung im Zeitraum ändert ihn. Der Fingerprint wird gespeichert, aber nie
    öffentlich angezeigt (er enthält über die Sensor-IDs indirekt interne Bezüge).
    """
    h = hashlib.sha256()
    h.update(("|".join(sorted(sensor_ids)) + f"#{_period_iso(start)}#{_period_iso(end)}").encode())
    data = _filter_period(df, start, end)
    n = 0 if data is None else len(data)
    h.update(f"#n={n}".encode())
    if n:
        if SENSOR_ID in data.columns:
            sid = data[SENSOR_ID].astype(str).to_numpy()
        else:
            sid = np.full(n, sensor_ids[0] if len(sensor_ids) == 1 else "", dtype=object)
        ms = np.round(to_seconds(data[TS]) * 1000.0).astype(np.int64)
        order = np.lexsort((ms, sid))
        cols: dict[str, Any] = {"s": sid[order], "t": ms[order]}
        for m in METRICS:
            v = data[m].to_numpy(dtype=float)[order] if m in data.columns else np.full(n, np.nan)
            cols[m] = np.round(v, FINGERPRINT_DECIMALS)
        frame = pd.DataFrame(cols)
        h.update(frame.to_csv(index=False, header=False, float_format=f"%.{FINGERPRINT_DECIMALS}f",
                              na_rep="").encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------
def _filter_period(df: pd.DataFrame | None, start: Any, end: Any) -> pd.DataFrame | None:
    if df is None or len(df) == 0 or TS not in df.columns:
        return df
    ts = pd.to_datetime(df[TS], utc=True)
    mask = pd.Series(True, index=df.index)
    s, e = _ts(start), _ts(end)
    if s is not None:
        mask &= ts >= s
    if e is not None:
        mask &= ts < e
    out = df.loc[mask].copy()
    out[TS] = ts[mask]
    return out


def _combine_sensors(df: pd.DataFrame | None) -> tuple[pd.DataFrame, int]:
    """Messungen mehrerer Sensoren eines Raums zu einer Reihe zusammenführen.

    Gleiche Zeitpunkte werden gemittelt (keine erfundenen Werte, nur vorhandene). Gibt auch
    die Anzahl der Sensoren mit Daten zurück.
    """
    cols = [TS, *METRICS]
    if df is None or len(df) == 0:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in cols}).assign(
            **{TS: pd.Series(pd.DatetimeIndex([], tz="UTC"))}), 0
    n_sensors = int(df[SENSOR_ID].nunique()) if SENSOR_ID in df.columns else 1
    data = df[[c for c in cols if c in df.columns]].copy()
    for m in METRICS:
        if m not in data.columns:
            data[m] = np.nan
    if n_sensors > 1 or data[TS].duplicated().any():
        data = data.groupby(TS, as_index=False)[list(METRICS)].mean()
    return data.sort_values(TS).reset_index(drop=True), n_sensors


def _round(values: np.ndarray, decimals: int = SERIES_DECIMALS) -> list[float | None]:
    return [None if not math.isfinite(v) else round(float(v), decimals) for v in values]


def _iso_local(value: Any, tz: str) -> str | None:
    t = _ts(value)
    return t.tz_convert(tz).isoformat() if t is not None else None


def _episode_relevance(ep: dict[str, Any]) -> tuple:
    return (bool(ep.get("is_sustained")), float(ep.get("duration_s") or 0.0), float(ep.get("max_rh") or 0.0))


def _slim_episode(ep: dict[str, Any], high: float) -> dict[str, Any]:
    keys = ("start", "end", "duration_s", "above_s", "max_rh", "max_rh_time", "mean_rh", "mean_temp",
            "min_dew_point_spread", "is_sustained", "interrupted_by_gap", "at_period_start", "at_period_end")
    out = {k: ep.get(k) for k in keys}
    out["reaches_high"] = (ep.get("max_rh") or 0.0) >= high
    return out


def _limit(items: list[dict[str, Any]], n: int, key) -> list[dict[str, Any]]:
    """Die ``n`` relevantesten Einträge, wieder chronologisch sortiert."""
    top = sorted(items, key=key, reverse=True)[:n]
    return sorted(top, key=lambda x: x.get("start") or "")


# ---------------------------------------------------------------------------
# Zeitreihe für Diagramme
# ---------------------------------------------------------------------------
def _series(prep: Any, tz: str) -> dict[str, Any]:
    days = prep.period_s / 86400.0
    metrics = (TEMPERATURE, HUMIDITY, DEW_POINT)
    if prep.empty or prep.period_start is None:
        return {"resolution": "h", "label": "stündlich", "note": None, "t_start": None, "step_s": 3600, "n": 0,
                "t": None, "temperature": [],
                "humidity": [], "humidity_min": [], "humidity_max": [], "dew_point": [], "coverage_pct": []}
    if days <= SERIES_3H_MAX_DAYS:
        agg = aggregate(prep, "h", metrics=metrics)
        resolution, label = "h", "stündlich"
        if days > SERIES_HOURLY_MAX_DAYS:
            agg = _regroup(agg, 3, metrics)
            resolution, label = "3h", "3-stündlich"
    else:
        agg = aggregate(prep, "D", metrics=metrics)
        resolution, label = "D", "täglich"
    note = None
    if resolution != "h":
        note = (f"Wegen des langen Zeitraums ({days:.0f} Tage) zeigt der Zeitverlauf {label}e Mittelwerte "
                "(rF zusätzlich mit Minimum und Maximum). Kennzahlen und Phasen beruhen auf allen Messungen.")
    return {
        "resolution": resolution,
        "label": label,
        "note": note,
        # Stunden-/3-Stunden-Fenster sind in UTC regelmäßig → Start + Schrittweite genügt;
        # Tagesfenster (23/25 h an Umstellungstagen) werden explizit aufgeführt.
        "t_start": pd.Timestamp(agg["start"].iloc[0]).tz_convert(tz).isoformat() if len(agg) else None,
        "step_s": {"h": 3600, "3h": 10800}.get(resolution),
        "n": len(agg),
        "t": ([pd.Timestamp(t).tz_convert(tz).isoformat() for t in agg["start"]] if resolution == "D" else None),
        "temperature": _round(agg[f"{TEMPERATURE}_mean"].to_numpy(dtype=float)),
        "humidity": _round(agg[f"{HUMIDITY}_mean"].to_numpy(dtype=float)),
        "humidity_min": _round(agg[f"{HUMIDITY}_min"].to_numpy(dtype=float)),
        "humidity_max": _round(agg[f"{HUMIDITY}_max"].to_numpy(dtype=float)),
        "dew_point": _round(agg[f"{DEW_POINT}_mean"].to_numpy(dtype=float)),
        "coverage_pct": _round(agg["coverage_pct"].to_numpy(dtype=float), 0),
    }


def _regroup(agg: pd.DataFrame, k: int, metrics: tuple[str, ...]) -> pd.DataFrame:
    """Stündliche Aggregation zu ``k``-Stunden-Fenstern zusammenfassen (gewichtet nach Abdeckung)."""
    grp = np.arange(len(agg)) // k
    w = agg["covered_s"].to_numpy(dtype=float)
    out = pd.DataFrame({"start": agg["start"].groupby(grp).first().to_numpy()})
    hours = agg["hours"].groupby(grp).sum().to_numpy()
    cov = pd.Series(w).groupby(grp).sum().to_numpy()
    out["coverage_pct"] = np.minimum(cov / (hours * 3600.0) * 100.0, 100.0)
    for m in metrics:
        v = agg[f"{m}_mean"].to_numpy(dtype=float)
        ok = np.isfinite(v)
        ww = np.where(ok, np.where(w > 0, w, 1.0), 0.0)
        num = pd.Series(np.where(ok, v, 0.0) * ww).groupby(grp).sum().to_numpy()
        den = pd.Series(ww).groupby(grp).sum().to_numpy()
        with np.errstate(invalid="ignore", divide="ignore"):
            out[f"{m}_mean"] = np.where(den > 0, num / den, np.nan)
        out[f"{m}_min"] = agg[f"{m}_min"].groupby(grp).min().to_numpy()
        out[f"{m}_max"] = agg[f"{m}_max"].groupby(grp).max().to_numpy()
    return out


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------
def build_snapshot(
    df: pd.DataFrame | None,
    *,
    room: Room,
    sensors: list[Sensor],
    period_start: datetime | Any,
    period_end: datetime | Any,
    options: ReportOptions,
    settings: AnalysisSettings,
    expected_s: int | None,
    version: int | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Berechnet den vollständigen Berichts-Snapshot (JSON-serialisierbar, ohne interne IDs).

    ``df``: Messungen der ``sensors`` (kanonisches Format, optional mit ``sensor_id``);
    es werden nur Messungen in ``[period_start, period_end)`` verwendet.
    """
    from climate_dashboard.reports import summary as summary_mod

    s = replace(settings, surface_offset_k=options.surface_offset_k)
    tz = s.timezone
    start, end = _ts(period_start), _ts(period_end)
    if start is None or end is None or end <= start:
        raise ValueError("Ungültiger Berichtszeitraum.")
    sensor_ids = [x.id for x in sensors]
    fingerprint = data_fingerprint(df, sensor_ids, start, end)
    period_df = _filter_period(df, start, end)
    data, n_sensors = _combine_sensors(period_df)

    prep = prepare(data, s, expected_s, None, start, end)
    quality = assess_quality(data, start, end, prep.expected_s, s)
    kpis = summarize_prepared(prep, (*METRICS, DEW_POINT_SPREAD))
    hum = analyze_humidity(prep)
    cond = analyze_condensation(prep)
    assessment = assess(prep, s, quality)
    monthly = monthly_overview(prep) if not prep.empty else []
    seasonal = seasonal_overview(prep) if not prep.empty else []
    dn = day_night(prep)

    # --- Abschnitte -------------------------------------------------------------------------
    q = quality.to_dict()
    gaps = sorted(q.get("gaps") or [], key=lambda g: g.get("duration_s") or 0.0, reverse=True)
    q["gaps"] = sorted(gaps[:MAX_GAPS], key=lambda g: g.get("start") or "")
    q["gaps_listed"] = len(q["gaps"])
    q["sensor_count"] = n_sensors
    q.pop("observed_intervals", None)

    hd = hum.to_dict()
    high = s.humidity_high
    el_eps = [_slim_episode(e, high) for e in hd["elevated_episodes"]]
    humidity = {
        "bands": hd["bands"],
        "elevated_summary": _slim_summary(hd["elevated_summary"]),
        "high_summary": _slim_summary(hd["high_summary"]),
        "episodes": _limit(el_eps, MAX_EPISODES, _episode_relevance),
        "episodes_total": len(el_eps),
        "episodes_listed": min(len(el_eps), MAX_EPISODES),
        "day_night": hd["day_night"],
    }

    cd = cond.to_dict()
    phases = cd.pop("low_spread_phases") or []
    cd["low_spread_phases"] = _limit(phases, MAX_LOW_SPREAD_PHASES, lambda p: p.get("duration_s") or 0.0)
    cd["low_spread_phases_total"] = len(phases)

    ad = assessment.to_dict()
    assessment_out = {k: ad[k] for k in ("overall", "findings", "limitations", "ventilation_hints",
                                         "descriptive", "criteria", "basis")}
    if n_sensors > 1:
        assessment_out["limitations"].append(
            f"Die Messungen von {n_sensors} Sensoren in diesem Raum wurden zusammengefasst; gleiche "
            "Zeitpunkte wurden gemittelt."
        )

    monthly_out = []
    def overlap_days(x: Any) -> float:
        return (min(x.end, end) - max(x.start, start)).total_seconds() / 86400.0

    # Monate, die nur wenige Stunden in den Berichtszeitraum ragen (z. B. UTC-/Ortszeit-Rand), weglassen
    for m in (x.to_dict() for x in monthly if overlap_days(x) >= 1.0):
        m["insufficient"] = (m.get("time_coverage_pct") or 0.0) < 30.0 or (m.get("n") or 0) < 2
        monthly_out.append(m)
    seasonal_out = [x.to_dict() for x in seasonal if overlap_days(x) >= 1.0]

    ctx = _Ctx(tz, set(options.include_sections))
    meta: dict[str, Any] = {
        "title": options.title,
        "room_name": room.name,
        "sensor_info": (
            [{"name": x.name, "device_info": x.device_info} for x in sensors] if options.show_sensor_info else None
        ),
        "period_start": _iso_local(start, tz),
        "period_end": _iso_local(end, tz),
        "created_at": _iso_local(created_at or utcnow(), tz),
        "version": version,
        "timezone": tz,
        "data_fingerprint": fingerprint,
        "note": options.note,
        "generator": GENERATOR,
        "thresholds": {
            "humidity_low": s.humidity_low,
            "humidity_elevated": s.humidity_elevated,
            "humidity_high": s.humidity_high,
            "dew_point_spread_warn": s.dew_point_spread_warn,
            "min_episode_minutes": s.min_episode_minutes,
            "night_start_hour": s.night_start_hour,
            "night_end_hour": s.night_end_hour,
            "surface_offset_k": s.surface_offset_k,
        },
    }

    def sec(name: str, value: Any) -> Any:
        return value if name in ctx.sections else None

    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "meta": meta,
        "options": options.to_dict() | {"sections": {k: SECTIONS[k] for k in options.include_sections}},
        "quality": q,  # immer enthalten (Datenabdeckung im Kopf); Detailabschnitt über options
        "kpis": {k: v.to_dict() for k, v in kpis.items()},  # immer (Kurzfazit/PDF); Anzeige über options
        "humidity": sec("humidity", humidity),
        "condensation": sec("condensation", cd),
        "assessment": assessment_out,  # Status/Einschränkungen immer (Fazit); Details über options
        "monthly": sec("monthly", monthly_out),
        "seasonal": sec("seasonal", seasonal_out),
        "day_night": sec("day_night", dn.to_dict()),
        "series": sec("timeseries", _series(prep, tz)),
    }
    if snapshot["series"] is None and ("condensation" in ctx.sections):
        snapshot["series"] = _series(prep, tz)  # Taupunkt-Diagramm braucht die Reihe

    sumtexts = summary_mod.build_summary(
        meta=meta, quality=q, kpis=snapshot["kpis"], humidity=humidity,
        condensation=cd, assessment=assessment_out,
    )
    snapshot["summary"] = sumtexts
    snapshot["conclusion"] = summary_mod.build_conclusion(assessment=assessment_out, quality=q,
                                                          condensation=cd)
    snapshot = jsonable(snapshot)
    json.dumps(snapshot, allow_nan=False)  # Absicherung: muss strikt serialisierbar sein
    return snapshot


def _slim_summary(summary: dict[str, Any]) -> dict[str, Any]:
    out = dict(summary)
    longest = out.pop("longest", None)
    out["longest_start"] = longest.get("start") if longest else None
    out["longest_end"] = longest.get("end") if longest else None
    out["longest_max_rh"] = longest.get("max_rh") if longest else None
    return out


def series_times(series: dict[str, Any] | None) -> pd.DatetimeIndex:
    """Zeitpunkte (Fensteranfang, tz-aware UTC) einer Snapshot-Zeitreihe."""
    if not series or not series.get("n"):
        return pd.DatetimeIndex([], tz="UTC")
    if series.get("t"):
        return pd.DatetimeIndex(pd.to_datetime(series["t"], utc=True))
    start = pd.Timestamp(series["t_start"]).tz_convert("UTC")
    return pd.date_range(start, periods=int(series["n"]), freq=pd.Timedelta(seconds=int(series["step_s"])))


def snapshot_to_json(snapshot: dict[str, Any]) -> str:
    """Kanonische JSON-Darstellung (für Caches und Vergleiche)."""
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False)

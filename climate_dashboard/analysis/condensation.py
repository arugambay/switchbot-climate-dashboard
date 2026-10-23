"""Abstand Raumluft–Taupunkt und (optional) Oberflächenbetrachtung.

Wichtig (Grundregel 6): Aus Raumluftmessungen allein lässt sich keine Kondensation feststellen.
Kondensation tritt an *Oberflächen* auf, deren Temperatur am oder unter dem Taupunkt der Luft
liegt. Ohne Oberflächentemperatur wird daher nur der Abstand Lufttemperatur − Taupunkt
beschrieben. Eine Oberflächenbetrachtung erfolgt nur
- mit gemessener Oberflächentemperatur (``surface_temperature``) oder
- mit einer ausdrücklich als ANNAHME gekennzeichneten Differenz ``surface_offset_k``
  (Oberfläche = Raumluft − Offset).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.analysis import physics
from climate_dashboard.analysis.humidity import HOUR, _pct, mask_runs, max_days_in_window
from climate_dashboard.analysis.quality import ResultMixin, from_seconds
from climate_dashboard.analysis.stats import (
    DEW_POINT_SPREAD,
    Prepared,
    W,
    _summarize_frame,
    metric_view,
    prepare,
)
from climate_dashboard.domain.models import DEW_POINT, TEMPERATURE, TS, AnalysisSettings

SURFACE_RH_CRITICAL = 80.0  # % rF an der Oberfläche – Orientierungswert (DIN 4108-2, s. FORMELN.md)
DAILY_SURFACE_RH_HOURS = 12.0  # Stunden je Tag mit Oberflächen-rF ≥ 80 % für die Tageszählung
SPREAD_CLOSE_K = 1.0

ASSUMPTION_TEXT = (
    "ANNAHME: Die Oberflächentemperatur wurde nicht gemessen, sondern als Raumlufttemperatur "
    "minus {offset:g} K angenommen. Die Aussagen gelten nur für eine Oberfläche, die tatsächlich "
    "so viel kälter ist."
)
NO_SURFACE_NOTE = (
    "Eine zuverlässige Kondensationsbewertung ist ohne Oberflächentemperatur nur eingeschränkt "
    "möglich."
)


@dataclass
class LowSpreadPhase(ResultMixin):
    start: pd.Timestamp
    end: pd.Timestamp
    duration_s: float
    min_spread: float | None
    interrupted_by_gap: bool


@dataclass
class SurfaceResult(ResultMixin):
    basis: str  # 'assumed' | 'measured'
    offset_k: float | None  # bei 'assumed'
    assumption: str | None  # Text der Annahme
    covered_hours: float
    hours_at_or_below_dew_point: float  # rechnerisch mögliche Kondensation an dieser Oberfläche
    pct_at_or_below_dew_point: float | None
    hours_surface_rh_critical: float  # Oberflächen-rF ≥ 80 %
    pct_surface_rh_critical: float | None
    days_surface_rh_critical: int  # Tage mit ≥ 12 h Oberflächen-rF ≥ 80 %
    days_surface_rh_critical_max_7d: int  # davon höchstens innerhalb von 7 aufeinanderfolgenden Tagen
    max_surface_rh: float | None  # auf 100 % begrenzt
    min_surface_minus_dew_point: float | None  # K; ≤ 0 = Oberfläche am/unter Taupunkt
    surface_rh_threshold: float = SURFACE_RH_CRITICAL


@dataclass
class CondensationResult(ResultMixin):
    warn_threshold_k: float
    covered_hours: float
    spread_mean: float | None
    spread_median: float | None
    spread_min: float | None
    spread_min_time: pd.Timestamp | None
    spread_p05: float | None
    hours_below_warn: float
    pct_below_warn: float | None
    hours_below_1k: float
    pct_below_1k: float | None
    low_spread_phases: list[LowSpreadPhase]
    surface: SurfaceResult | None
    notes: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


def _align_surface(f: pd.DataFrame, surface: pd.Series, tolerance_s: float) -> np.ndarray:
    """Gemessene Oberflächentemperatur auf die Messzeitpunkte abbilden.

    - Series mit DatetimeIndex: nächstgelegener Wert innerhalb ``tolerance_s`` (sonst NaN).
    - Sonst: positionsgleich zu den Zeilen des übergebenen DataFrames (gleiche Länge).
    """
    if isinstance(surface.index, pd.DatetimeIndex):
        idx = surface.index
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        right = pd.DataFrame({"_t": idx, "_surf": surface.to_numpy(dtype=float)}).sort_values("_t")
        right["_t"] = right["_t"].astype(f[TS].dtype)
        left = pd.DataFrame({"_t": f[TS].to_numpy(), "_ord": np.arange(len(f))})
        left["_t"] = left["_t"].astype(f[TS].dtype)
        merged = pd.merge_asof(left.sort_values("_t"), right, on="_t", direction="nearest",
                               tolerance=pd.Timedelta(seconds=tolerance_s))
        return merged.sort_values("_ord")["_surf"].to_numpy(dtype=float)
    return np.asarray(surface, dtype=float)


def analyze_condensation(
    df: pd.DataFrame | Prepared | None,
    settings: AnalysisSettings | None = None,
    gap_threshold_s: float | None = None,
    *,
    surface_temperature: pd.Series | None = None,
    surface_offset_k: float | None = None,
    expected_s: float | None = None,
    period_start: Any = None,
    period_end: Any = None,
) -> CondensationResult:
    """Taupunktabstand und optionale Oberflächenbetrachtung.

    ``surface_temperature``: gemessene Oberflächentemperatur (°C), entweder als Series mit
    DatetimeIndex oder positionsgleich zu ``df`` (nur wenn ``df`` sortiert und ohne fehlende
    Zeitstempel ist). ``surface_offset_k`` überschreibt ``settings.surface_offset_k``.
    Gemessene Werte haben Vorrang vor der Annahme.
    """
    if surface_temperature is not None and not isinstance(df, Prepared) and df is not None and len(df):
        # Positionsgleiche Serie vor dem Sortieren an den DataFrame hängen
        if not isinstance(surface_temperature.index, pd.DatetimeIndex):
            surface_temperature = pd.Series(np.asarray(surface_temperature, dtype=float),
                                            index=pd.DatetimeIndex(pd.Series(df[TS]).to_numpy()))
            if surface_temperature.index.tz is None:
                surface_temperature.index = surface_temperature.index.tz_localize("UTC")
    prep = prepare(df, settings, expected_s, gap_threshold_s, period_start, period_end)
    s = prep.settings
    offset = s.surface_offset_k if surface_offset_k is None else surface_offset_k
    warn = float(s.dew_point_spread_warn)

    f = metric_view(prep, DEW_POINT_SPREAD)
    spread = f[DEW_POINT_SPREAD].to_numpy(dtype=float)
    w = f[W].to_numpy(dtype=float)
    covered = float(w.sum())
    summ = _summarize_frame(f, DEW_POINT_SPREAD, prep.tz)
    below_warn = float(w[spread < warn].sum())
    below_1 = float(w[spread < SPREAD_CLOSE_K].sum())

    phases: list[LowSpreadPhase] = []
    for r in mask_runs(f, spread < warn, s.episode_merge_gap_minutes * 60.0):
        seg = spread[r.i0 : r.i1 + 1]
        phases.append(LowSpreadPhase(
            start=from_seconds(r.start_s, prep.tz),
            end=from_seconds(r.end_s, prep.tz),
            duration_s=max(r.end_s - r.start_s, 0.0),
            min_spread=float(np.nanmin(seg)) if np.isfinite(seg).any() else None,
            interrupted_by_gap=r.gap_end,
        ))

    notes: list[str] = []
    limitations: list[str] = []
    surface: SurfaceResult | None = None
    surf_t: np.ndarray | None = None
    basis = None
    if surface_temperature is not None and len(f):
        surf_t = _align_surface(f, surface_temperature, max(prep.nominal_s, 60.0))
        basis = "measured"
    elif offset is not None and math.isfinite(offset) and len(f):
        surf_t = f[TEMPERATURE].to_numpy(dtype=float) - float(offset)
        basis = "assumed"

    if surf_t is not None:
        td = f[DEW_POINT].to_numpy(dtype=float)
        ok = np.isfinite(surf_t) & np.isfinite(td)
        sw = np.where(ok, w, 0.0)
        s_cov = float(sw.sum())
        diff = surf_t - td
        surf_rh = np.minimum(np.asarray(physics.relative_humidity(surf_t, td), dtype=float), 100.0)
        cond = ok & (diff <= 0)
        crit = ok & (surf_rh >= SURFACE_RH_CRITICAL)
        # Tage mit ≥ 12 h Oberflächen-rF ≥ 80 % (Lokalzeit)
        days = 0
        days_7 = 0
        if crit.any():
            local_day = f[TS].dt.tz_convert(prep.tz).dt.date
            per_day = pd.Series(np.where(crit, w, 0.0)).groupby(local_day.to_numpy()).sum()
            crit_days = per_day.index[per_day.to_numpy() >= DAILY_SURFACE_RH_HOURS * HOUR]
            days = len(crit_days)
            days_7 = max_days_in_window(list(crit_days))
        surface = SurfaceResult(
            basis=basis or "assumed",
            offset_k=float(offset) if basis == "assumed" else None,
            assumption=ASSUMPTION_TEXT.format(offset=float(offset)) if basis == "assumed" else None,
            covered_hours=s_cov / HOUR,
            hours_at_or_below_dew_point=float(w[cond].sum()) / HOUR,
            pct_at_or_below_dew_point=_pct(float(w[cond].sum()), s_cov),
            hours_surface_rh_critical=float(w[crit].sum()) / HOUR,
            pct_surface_rh_critical=_pct(float(w[crit].sum()), s_cov),
            days_surface_rh_critical=days,
            days_surface_rh_critical_max_7d=days_7,
            max_surface_rh=float(np.nanmax(surf_rh[ok])) if ok.any() else None,
            min_surface_minus_dew_point=float(np.nanmin(diff[ok])) if ok.any() else None,
        )
        if basis == "assumed":
            limitations.append(surface.assumption or "")
        else:
            limitations.append(
                "Die Oberflächenbetrachtung gilt nur für die Stelle, an der die Oberflächentemperatur "
                "gemessen wurde."
            )
        limitations.append(
            "Rechnerisch mögliche Kondensation bedeutet nicht, dass Kondensat tatsächlich "
            "aufgetreten ist (Wärmespeicherung, Luftbewegung und Material spielen eine Rolle)."
        )
    else:
        limitations.append(NO_SURFACE_NOTE)
        notes.append(
            "Der Abstand zwischen Lufttemperatur und Taupunkt zeigt, um wie viel Kelvin eine "
            "Oberfläche kälter als die Raumluft sein müsste, damit dort rechnerisch Tauwasser "
            "entstehen kann."
        )
    if summ.source in ("calculated", "mixed"):
        notes.append("Der Taupunkt wurde ganz oder teilweise aus Temperatur und Luftfeuchtigkeit berechnet.")

    return CondensationResult(
        warn_threshold_k=warn,
        covered_hours=covered / HOUR,
        spread_mean=summ.mean,
        spread_median=summ.median,
        spread_min=summ.min,
        spread_min_time=summ.min_time,
        spread_p05=summ.p05,
        hours_below_warn=below_warn / HOUR,
        pct_below_warn=_pct(below_warn, covered),
        hours_below_1k=below_1 / HOUR,
        pct_below_1k=_pct(below_1, covered),
        low_spread_phases=phases,
        surface=surface,
        notes=notes,
        limitations=limitations,
    )

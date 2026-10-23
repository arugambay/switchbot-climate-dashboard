"""Zeitgewichtete Kennzahlen.

Alle Kennzahlen werden über die Zeit gewichtet, die eine Messung repräsentiert (siehe
``quality.durations``), nicht über die Anzahl der Messungen. Bei unregelmäßigen Intervallen
(z. B. 1 min und 30 min gemischt) würde eine reine Zählung dicht gemessene Phasen
überbewerten.

``prepare()`` bereitet einen kanonischen DataFrame einmal auf (sortiert, effektive Werte mit
Herkunft, Zeitgewichte, Lückenmarken). Die übrigen Analysemodule nutzen es gemeinsam.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.analysis import physics
from climate_dashboard.analysis.quality import (
    ResultMixin,
    _nominal_interval,
    _ts,
    durations,
    gap_flags,
    gap_threshold_s,
    resolve_expected_interval,
    to_seconds,
)
from climate_dashboard.domain.models import (
    DEW_POINT,
    METRIC_UNITS,
    METRICS,
    TEMPERATURE,
    TS,
    AnalysisSettings,
)

DEW_POINT_SPREAD = "dew_point_spread"  # Temperatur − Taupunkt in K
UNITS: dict[str, str] = {**METRIC_UNITS, DEW_POINT_SPREAD: "K"}
W = "_w"  # Spalte: repräsentierte Sekunden
GAP_AFTER = "_gap_after"  # Spalte: nach dieser Messung beginnt eine Lücke
SECS = "_secs"  # Spalte: Zeitstempel als Sekunden seit Epoche


@dataclass
class Prepared:
    """Aufbereitete Messdaten (intern, nicht serialisiert)."""

    frame: pd.DataFrame
    settings: AnalysisSettings
    expected_s: float | None
    gap_threshold_s: float
    nominal_s: float
    period_start: pd.Timestamp | None
    period_end: pd.Timestamp | None

    @property
    def tz(self) -> str:
        return self.settings.timezone

    @property
    def empty(self) -> bool:
        return len(self.frame) == 0

    @property
    def period_s(self) -> float:
        if self.period_start is None or self.period_end is None:
            return 0.0
        return max((self.period_end - self.period_start).total_seconds(), 0.0)

    @property
    def covered_s(self) -> float:
        return float(self.frame[W].sum()) if len(self.frame) else 0.0


def _empty_frame() -> pd.DataFrame:
    cols: dict[str, Any] = {TS: pd.Series(pd.DatetimeIndex([], tz="UTC"))}
    for m in (*METRICS, DEW_POINT_SPREAD):
        cols[m] = pd.Series(dtype=float)
    for m in METRICS:
        cols[physics.source_column(m)] = pd.Series(dtype=object)
    cols[W] = pd.Series(dtype=float)
    cols[GAP_AFTER] = pd.Series(dtype=bool)
    cols[SECS] = pd.Series(dtype=float)
    return pd.DataFrame(cols)


def prepare(
    df: pd.DataFrame | Prepared | None,
    settings: AnalysisSettings | None = None,
    expected_s: float | None = None,
    gap_threshold: float | None = None,
    period_start: Any = None,
    period_end: Any = None,
) -> Prepared:
    """Bereitet Messdaten für die Analyse auf (oder gibt ein bestehendes ``Prepared`` zurück).

    ``period_start``/``period_end`` begrenzen auf ``[start, end)``. Ohne Angabe gilt der
    Datenzeitraum (erste Messung bis letzte Messung + nominales Intervall).
    """
    if isinstance(df, Prepared):
        if period_start is None and period_end is None:
            return df
        return slice_prepared(df, period_start, period_end)
    settings = settings or AnalysisSettings()
    start_t, end_t = _ts(period_start), _ts(period_end)
    if df is None or len(df) == 0 or TS not in df.columns:
        thr = gap_threshold if gap_threshold else gap_threshold_s(expected_s, settings)
        return Prepared(_empty_frame(), settings, expected_s, thr, expected_s or 0.0, start_t, end_t)
    data = df[df[TS].notna()]
    ts = pd.Series(data[TS])
    if ts.dt.tz is None:
        ts = ts.dt.tz_localize("UTC")
    ts = ts.dt.tz_convert("UTC")
    data = data.assign(**{TS: ts})
    data = data.sort_values(TS, kind="stable").reset_index(drop=True)
    eff = physics.effective(data)
    eff[DEW_POINT_SPREAD] = eff[TEMPERATURE] - eff[DEW_POINT]
    expected = resolve_expected_interval(eff[TS], expected_s)
    thr = float(gap_threshold) if gap_threshold else gap_threshold_s(expected, settings)
    secs = to_seconds(eff[TS])
    nominal = float(expected) if expected else _nominal_interval(secs, thr)
    nominal = min(nominal, thr)
    data_start = eff[TS].iloc[0]
    data_end = eff[TS].iloc[-1] + pd.Timedelta(seconds=nominal)
    eff[SECS] = secs
    eff[W] = durations(eff[TS], thr, nominal_s=nominal, period_end=data_end).to_numpy()
    eff[GAP_AFTER] = gap_flags(eff[TS], thr)
    full = Prepared(eff, settings, expected, thr, nominal, data_start, data_end)
    if start_t is None and end_t is None:
        return full
    # Gewichte werden auf dem vollständigen Datensatz berechnet und dann gekappt: die letzte
    # Messung vor ``end`` repräsentiert die Zeit bis zur nächsten Messung, höchstens bis ``end``.
    return slice_prepared(full, start_t if start_t is not None else data_start,
                          end_t if end_t is not None else data_end)


def slice_prepared(prep: Prepared, start: Any = None, end: Any = None) -> Prepared:
    """Ausschnitt ``[start, end)`` mit an das Ende gekappten Zeitgewichten."""
    start_t = _ts(start) if start is not None else prep.period_start
    end_t = _ts(end) if end is not None else prep.period_end
    f = prep.frame
    mask = np.ones(len(f), dtype=bool)
    if start_t is not None:
        mask &= (f[TS] >= start_t).to_numpy()
    if end_t is not None:
        mask &= (f[TS] < end_t).to_numpy()
    sub = f[mask].reset_index(drop=True)
    if end_t is not None and len(sub):
        end_s = (end_t - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds()
        sub = sub.assign(**{W: np.minimum(sub[W].to_numpy(), np.clip(end_s - sub[SECS].to_numpy(), 0, None))})
        # Die letzte Messung des Ausschnitts: Lücke zur nächsten (außerhalb) zählt nicht als Lücke
        # innerhalb des Ausschnitts, wohl aber für Phasen-Abbrüche – Kennzeichnung bleibt erhalten.
    return Prepared(sub, prep.settings, prep.expected_s, prep.gap_threshold_s, prep.nominal_s, start_t, end_t)


def metric_view(prep: Prepared, metric: str) -> pd.DataFrame:
    """Zeilen mit gültigem Wert für ``metric``; Zeitgewichte neu berechnet, damit fehlende Werte
    wie fehlende Messungen (ggf. Lücken) behandelt werden."""
    f = prep.frame
    if metric not in f.columns:
        return f.iloc[0:0]
    valid = f[metric].notna().to_numpy()
    if valid.all():
        return f
    sub = f[valid].reset_index(drop=True)
    if len(sub):
        w = durations(sub[TS], prep.gap_threshold_s, nominal_s=prep.nominal_s, period_end=prep.period_end)
        sub = sub.assign(**{W: w.to_numpy(), GAP_AFTER: gap_flags(sub[TS], prep.gap_threshold_s)})
    return sub


# ---------------------------------------------------------------------------
# Gewichtete Statistik
# ---------------------------------------------------------------------------
def weighted_quantiles(values: np.ndarray, weights: np.ndarray, qs: list[float] | tuple[float, ...]) -> list[float]:
    """Gewichtete Quantile (inverse empirische Verteilungsfunktion), eine Sortierung für alle ``qs``.

    Je Quantil der kleinste Wert, bei dem der kumulierte Zeitanteil ≥ q erreicht. Ohne positive
    Gewichte: ungewichtete Quantile derselben Definition.
    """
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    ok = np.isfinite(v) & np.isfinite(w)
    v, w = v[ok], w[ok]
    if len(v) == 0:
        return [math.nan for _ in qs]
    if w.sum() <= 0:
        w = np.ones_like(v)
    pos = w > 0
    v, w = v[pos], w[pos]
    order = np.argsort(v)
    v, w = v[order], w[order]
    cw = np.cumsum(w)
    out = []
    for q in qs:
        idx = int(np.searchsorted(cw, q * cw[-1] - 1e-9 * cw[-1], side="left"))
        out.append(float(v[min(idx, len(v) - 1)]))
    return out


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """Ein gewichtetes Quantil (siehe ``weighted_quantiles``)."""
    return weighted_quantiles(values, weights, (q,))[0]


def weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    ok = np.isfinite(v) & np.isfinite(w)
    if not ok.any():
        return math.nan
    v, w = v[ok], w[ok]
    total = w.sum()
    if total <= 0:
        return float(v.mean())
    return float((v * w).sum() / total)


@dataclass
class MetricSummary(ResultMixin):
    metric: str
    unit: str
    count: int
    covered_s: float  # Zeit, die gültige Werte repräsentieren
    mean: float | None  # zeitgewichtet
    median: float | None  # zeitgewichtet
    min: float | None
    min_time: pd.Timestamp | None
    max: float | None
    max_time: pd.Timestamp | None
    p05: float | None
    p95: float | None
    std: float | None  # zeitgewichtete Standardabweichung
    time_weighted: bool  # False nur, wenn keine Zeitgewichte verfügbar waren (z. B. 1 Messung)
    source: str | None  # 'measured' | 'calculated' | 'mixed' | None
    calculated_pct: float | None  # Anteil (nach Anzahl) der aus T + rF berechneten Werte


def _summarize_frame(f: pd.DataFrame, metric: str, tz: str) -> MetricSummary:
    unit = UNITS.get(metric, "")
    if metric not in f.columns or len(f) == 0:
        return MetricSummary(metric, unit, 0, 0.0, None, None, None, None, None, None, None, None, None,
                             False, None, None)
    v = f[metric].to_numpy(dtype=float)
    w = f[W].to_numpy(dtype=float)
    total = float(w.sum())
    weighted = total > 0
    mean = weighted_mean(v, w)
    var = weighted_mean((v - mean) ** 2, w) if len(v) > 1 else 0.0
    i_min, i_max = int(np.nanargmin(v)), int(np.nanargmax(v))
    p05, median, p95 = weighted_quantiles(v, w, (0.05, 0.5, 0.95))
    src_col = physics.source_column(metric)
    source = None
    calc_pct = None
    if src_col in f.columns:
        src = f[src_col]
        src_arr = src.to_numpy()
        n_calc = int((src_arr == "calculated").sum())
        n_meas = int((src_arr == "measured").sum())
        if n_calc + n_meas:
            calc_pct = n_calc / (n_calc + n_meas) * 100.0
            source = "calculated" if n_meas == 0 else ("measured" if n_calc == 0 else "mixed")
    elif metric == DEW_POINT_SPREAD:
        source = "calculated"
        calc_pct = 100.0
    return MetricSummary(
        metric=metric,
        unit=unit,
        count=len(v),
        covered_s=total,
        mean=mean,
        median=median,
        min=float(v[i_min]),
        min_time=f[TS].iloc[i_min].tz_convert(tz),
        max=float(v[i_max]),
        max_time=f[TS].iloc[i_max].tz_convert(tz),
        p05=p05,
        p95=p95,
        std=math.sqrt(var) if math.isfinite(var) else None,
        time_weighted=weighted,
        source=source,
        calculated_pct=calc_pct,
    )


def summarize_prepared(prep: Prepared, metrics: tuple[str, ...] | list[str] = METRICS) -> dict[str, MetricSummary]:
    return {m: _summarize_frame(metric_view(prep, m), m, prep.tz) for m in metrics}


def summarize(
    df: pd.DataFrame | Prepared | None,
    metrics: tuple[str, ...] | list[str] = METRICS,
    gap_threshold_s: float | None = None,
    *,
    settings: AnalysisSettings | None = None,
    expected_s: float | None = None,
    period_start: Any = None,
    period_end: Any = None,
) -> dict[str, MetricSummary]:
    """Zeitgewichtete Kennzahlen je Messgröße (effektive Werte, Herkunft gekennzeichnet).

    ``metrics`` darf zusätzlich ``'dew_point_spread'`` (Temperatur − Taupunkt) enthalten.
    """
    prep = prepare(df, settings, expected_s, gap_threshold_s, period_start, period_end)
    return summarize_prepared(prep, metrics)


def summaries_to_dict(summaries: dict[str, MetricSummary]) -> dict[str, Any]:
    return {k: v.to_dict() for k, v in summaries.items()}

"""Relative Luftfeuchtigkeit: Zeitanteile in Bereichen, zusammenhängende Phasen, Tag/Nacht.

Alle Anteile beziehen sich auf die *abgedeckte* Zeit (Zeit mit Messwerten). Lückenzeit wird
getrennt ausgewiesen und zählt weder als erhöht noch als unauffällig.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.analysis.quality import ResultMixin, from_seconds
from climate_dashboard.analysis.stats import (
    DEW_POINT_SPREAD,
    GAP_AFTER,
    SECS,
    Prepared,
    W,
    metric_view,
    prepare,
    weighted_mean,
)
from climate_dashboard.domain.models import HUMIDITY, TEMPERATURE, TS, AnalysisSettings

HOUR = 3600.0
WEEK = 7 * 24 * HOUR


# ---------------------------------------------------------------------------
# Feuchtebereiche
# ---------------------------------------------------------------------------
@dataclass
class Band(ResultMixin):
    key: str  # 'trocken' | 'normal' | 'erhoeht' | 'hoch'
    label: str
    lower: float | None  # einschließlich
    upper: float | None  # ausschließlich
    hours: float
    pct: float | None  # % der abgedeckten Zeit


@dataclass
class BandShares(ResultMixin):
    bands: list[Band]
    covered_hours: float
    period_hours: float
    gap_hours: float  # nicht abgedeckte Zeit im Zeitraum (separat, nicht in den Anteilen)
    hours_at_or_above_elevated: float
    pct_at_or_above_elevated: float | None
    hours_at_or_above_high: float
    pct_at_or_above_high: float | None
    hours_below_low: float
    pct_below_low: float | None
    thresholds: dict[str, float] = field(default_factory=dict)


def _pct(part: float, total: float) -> float | None:
    return part / total * 100.0 if total > 0 else None


def band_shares(
    df: pd.DataFrame | Prepared | None,
    settings: AnalysisSettings | None = None,
    gap_threshold_s: float | None = None,
    *,
    expected_s: float | None = None,
    period_start: Any = None,
    period_end: Any = None,
) -> BandShares:
    """Zeit (Stunden und % der abgedeckten Zeit) in den Feuchtebereichen
    trocken (< low), normal, erhöht (≥ elevated), hoch (≥ high)."""
    prep = prepare(df, settings, expected_s, gap_threshold_s, period_start, period_end)
    s = prep.settings
    f = metric_view(prep, HUMIDITY)
    rh = f[HUMIDITY].to_numpy(dtype=float)
    w = f[W].to_numpy(dtype=float)
    covered = float(w.sum())
    lo, el, hi = s.humidity_low, s.humidity_elevated, s.humidity_high
    edges = [
        ("trocken", f"trocken (< {lo:g} %)", None, lo),
        ("normal", f"normal ({lo:g}–{el:g} %)", lo, el),
        ("erhoeht", f"erhöht ({el:g}–{hi:g} %)", el, hi),
        ("hoch", f"hoch (≥ {hi:g} %)", hi, None),
    ]
    bands = []
    for key, label, lower, upper in edges:
        m = np.ones(len(rh), dtype=bool)
        if lower is not None:
            m &= rh >= lower
        if upper is not None:
            m &= rh < upper
        secs = float(w[m].sum())
        bands.append(Band(key, label, lower, upper, secs / HOUR, _pct(secs, covered)))
    above_el = float(w[rh >= el].sum())
    above_hi = float(w[rh >= hi].sum())
    below_lo = float(w[rh < lo].sum())
    period = prep.period_s
    return BandShares(
        bands=bands,
        covered_hours=covered / HOUR,
        period_hours=period / HOUR,
        gap_hours=max(period - covered, 0.0) / HOUR,
        hours_at_or_above_elevated=above_el / HOUR,
        pct_at_or_above_elevated=_pct(above_el, covered),
        hours_at_or_above_high=above_hi / HOUR,
        pct_at_or_above_high=_pct(above_hi, covered),
        hours_below_low=below_lo / HOUR,
        pct_below_low=_pct(below_lo, covered),
        thresholds={"low": lo, "elevated": el, "high": hi},
    )


# ---------------------------------------------------------------------------
# Phasen (Episoden)
# ---------------------------------------------------------------------------
@dataclass
class _Run:
    i0: int
    i1: int
    start_s: float
    end_s: float
    gap_end: bool
    after_gap: bool


def mask_runs(f: pd.DataFrame, mask: np.ndarray, merge_gap_s: float) -> list[_Run]:
    """Zusammenhängende Zeilenbereiche mit ``mask == True``.

    Eine Phase endet an einer Datenlücke (``GAP_AFTER``). Unterbrechungen (Zeit unter der
    Schwelle) kürzer als ``merge_gap_s`` werden überbrückt, sofern keine Lücke dazwischen liegt.
    Ende einer Phase = Zeitpunkt der letzten Messung + deren repräsentierte Zeit.
    Python-Schleife nur über Segmentgrenzen, nicht über Zeilen.
    """
    n = len(f)
    mask = np.asarray(mask, dtype=bool)
    if n == 0 or not mask.any():
        return []
    secs = f[SECS].to_numpy(dtype=float)
    w = f[W].to_numpy(dtype=float)
    gap_after = f[GAP_AFTER].to_numpy(dtype=bool)
    change = np.ones(n, dtype=bool)
    change[1:] = (mask[1:] != mask[:-1]) | gap_after[:-1]
    starts = np.flatnonzero(change)
    ends = np.append(starts[1:] - 1, n - 1)
    seg_mask = mask[starts]
    seg_start = secs[starts]
    seg_end = secs[ends] + w[ends]
    seg_gap_end = gap_after[ends]
    seg_after_gap = np.zeros(len(starts), dtype=bool)
    seg_after_gap[1:] = gap_after[starts[1:] - 1]

    runs: list[_Run] = []
    cur: _Run | None = None
    pending = 0.0
    pending_ok = True
    for k in range(len(starts)):
        if seg_mask[k]:
            if cur is not None and pending_ok and not cur.gap_end and pending < merge_gap_s:
                cur.i1 = int(ends[k])
                cur.end_s = float(seg_end[k])
                cur.gap_end = bool(seg_gap_end[k])
            else:
                if cur is not None:
                    runs.append(cur)
                cur = _Run(int(starts[k]), int(ends[k]), float(seg_start[k]), float(seg_end[k]),
                           bool(seg_gap_end[k]), bool(seg_after_gap[k]))
            pending = 0.0
            pending_ok = True
        else:
            pending += float(seg_end[k] - seg_start[k])
            if seg_gap_end[k] or seg_after_gap[k]:
                pending_ok = False
    if cur is not None:
        runs.append(cur)
    return runs


def run_ids(n: int, runs: list[_Run]) -> np.ndarray:
    ids = np.full(n, -1, dtype=np.int64)
    for k, r in enumerate(runs):
        ids[r.i0 : r.i1 + 1] = k
    return ids


@dataclass
class Episode(ResultMixin):
    start: pd.Timestamp
    end: pd.Timestamp
    duration_s: float  # Ende − Beginn (inkl. überbrückter kurzer Unterbrechungen)
    above_s: float  # Zeit tatsächlich über der Schwelle
    threshold: float
    max_rh: float | None
    max_rh_time: pd.Timestamp | None
    mean_rh: float | None  # zeitgewichtet über die Phase
    mean_temp: float | None
    min_dew_point_spread: float | None
    n: int
    is_sustained: bool  # Dauer ≥ min_episode_minutes
    interrupted_by_gap: bool  # Phase endet an einer Datenlücke (tatsächliche Dauer unbekannt)
    starts_after_gap: bool  # Phase beginnt direkt nach einer Datenlücke
    at_period_start: bool  # Phase beginnt mit dem Zeitraum (könnte früher begonnen haben)
    at_period_end: bool  # Phase reicht bis zum Ende der Daten/des Zeitraums

    @property
    def duration_hours(self) -> float:
        return self.duration_s / HOUR


def episodes_from_mask(
    prep: Prepared, f: pd.DataFrame, mask: np.ndarray, threshold: float, merge_gap_s: float,
    min_sustained_s: float, value_col: str = HUMIDITY,
) -> list[Episode]:
    runs = mask_runs(f, mask, merge_gap_s)
    if not runs:
        return []
    n = len(f)
    ids = run_ids(n, runs)
    sel = ids >= 0
    w = f[W].to_numpy(dtype=float)
    g = pd.DataFrame({
        "id": ids[sel],
        "w": w[sel],
        "above_w": np.where(mask, w, 0.0)[sel],
        "rh": f[value_col].to_numpy(dtype=float)[sel],
        "rhw": (f[value_col].to_numpy(dtype=float) * w)[sel],
        "tw": (f[TEMPERATURE].to_numpy(dtype=float) * w)[sel],
        "tw_w": np.where(np.isfinite(f[TEMPERATURE].to_numpy(dtype=float)), w, 0.0)[sel],
        "spread": f[DEW_POINT_SPREAD].to_numpy(dtype=float)[sel],
        "row": np.arange(n)[sel],
    })
    g["tw"] = g["tw"].fillna(0.0)
    agg = g.groupby("id").agg(
        w=("w", "sum"), above=("above_w", "sum"), rh_max=("rh", "max"), rhw=("rhw", "sum"),
        tw=("tw", "sum"), tw_w=("tw_w", "sum"), spread_min=("spread", "min"), n=("row", "size"),
        mean_rh_plain=("rh", "mean"),
    )
    agg = agg.reindex(range(len(runs)))
    a_w, a_above, a_max = (agg[c].to_numpy(dtype=float) for c in ("w", "above", "rh_max"))
    a_rhw, a_tw, a_tww = (agg[c].to_numpy(dtype=float) for c in ("rhw", "tw", "tw_w"))
    a_spread, a_plain = agg["spread_min"].to_numpy(dtype=float), agg["mean_rh_plain"].to_numpy(dtype=float)
    a_n = agg["n"].fillna(0).to_numpy(dtype=np.int64)
    # Zeile des Maximums je Phase (erste bei Gleichstand), vektorisiert
    order = np.lexsort((g["row"].to_numpy(), -np.nan_to_num(g["rh"].to_numpy(), nan=-np.inf), g["id"].to_numpy()))
    gid = g["id"].to_numpy()[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = gid[1:] != gid[:-1]
    max_row = dict(zip(gid[first].tolist(), g["row"].to_numpy()[order][first].tolist(), strict=True))
    tz = prep.tz
    ts = f[TS]
    data_first = prep.frame[SECS].iloc[0] if len(prep.frame) else math.nan
    p_start = prep.period_start
    p_end = prep.period_end
    p_start_s = (p_start - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds() if p_start is not None else data_first
    p_end_s = (p_end - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds() if p_end is not None else math.inf
    out: list[Episode] = []
    for k, r in enumerate(runs):
        wsum = float(a_w[k])
        mean_rh = float(a_rhw[k] / wsum) if wsum > 0 else float(a_plain[k])
        mean_t = float(a_tw[k] / a_tww[k]) if a_tww[k] > 0 else None
        duration = max(r.end_s - r.start_s, 0.0)
        imax = max_row.get(k)
        out.append(Episode(
            start=from_seconds(r.start_s, tz),
            end=from_seconds(r.end_s, tz),
            duration_s=duration,
            above_s=float(a_above[k]),
            threshold=threshold,
            max_rh=float(a_max[k]) if np.isfinite(a_max[k]) else None,
            max_rh_time=ts.iloc[int(imax)].tz_convert(tz) if imax is not None else None,
            mean_rh=mean_rh,
            mean_temp=mean_t,
            min_dew_point_spread=float(a_spread[k]) if np.isfinite(a_spread[k]) else None,
            n=int(a_n[k]),
            is_sustained=duration >= min_sustained_s,
            interrupted_by_gap=r.gap_end,
            starts_after_gap=r.after_gap,
            at_period_start=r.i0 == 0 and r.start_s <= p_start_s + prep.gap_threshold_s,
            at_period_end=r.i1 == n - 1 and r.end_s >= p_end_s - prep.gap_threshold_s,
        ))
    return out


def find_episodes(
    df: pd.DataFrame | Prepared | None,
    threshold: float | None = None,
    settings: AnalysisSettings | None = None,
    gap_threshold_s: float | None = None,
    *,
    expected_s: float | None = None,
    period_start: Any = None,
    period_end: Any = None,
) -> list[Episode]:
    """Zusammenhängende Phasen mit rF ≥ ``threshold`` (Standard: ``humidity_elevated``).

    Unterbrechungen kürzer als ``episode_merge_gap_minutes`` werden zusammengefasst. Eine Phase
    endet an einer Datenlücke und wird als ``interrupted_by_gap`` markiert. ``is_sustained``:
    Dauer ≥ ``min_episode_minutes``.
    """
    prep = prepare(df, settings, expected_s, gap_threshold_s, period_start, period_end)
    s = prep.settings
    thr = s.humidity_elevated if threshold is None else float(threshold)
    f = metric_view(prep, HUMIDITY)
    if len(f) == 0:
        return []
    mask = f[HUMIDITY].to_numpy(dtype=float) >= thr
    return episodes_from_mask(prep, f, mask, thr, s.episode_merge_gap_minutes * 60.0,
                              s.min_episode_minutes * 60.0)


@dataclass
class EpisodeSummary(ResultMixin):
    threshold: float
    min_sustained_minutes: int
    count_total: int
    count_short: int  # kurze Ausschläge (< min_episode_minutes)
    count_sustained: int  # anhaltende Phasen
    count_interrupted_by_gap: int
    longest: Episode | None
    longest_duration_s: float
    total_duration_s: float
    sustained_duration_s: float
    sustained_per_week: float | None  # je Woche abgedeckter Zeit
    days_with_sustained: int  # Kalendertage (Lokalzeit), an denen eine anhaltende Phase lag
    covered_days: float | None


def episode_summary(
    episodes: list[Episode],
    settings: AnalysisSettings | None = None,
    covered_s: float | None = None,
    threshold: float | None = None,
) -> EpisodeSummary:
    s = settings or AnalysisSettings()
    sustained = [e for e in episodes if e.is_sustained]
    longest = max(episodes, key=lambda e: e.duration_s) if episodes else None
    days: set = set()
    for e in sustained:
        d = e.start.normalize()
        last = (e.end - pd.Timedelta(microseconds=1)).normalize()
        while d <= last:
            days.add(d.date())
            d = (d + pd.Timedelta(hours=26)).normalize()
    thr = threshold if threshold is not None else (episodes[0].threshold if episodes else s.humidity_elevated)
    return EpisodeSummary(
        threshold=thr,
        min_sustained_minutes=s.min_episode_minutes,
        count_total=len(episodes),
        count_short=len(episodes) - len(sustained),
        count_sustained=len(sustained),
        count_interrupted_by_gap=sum(1 for e in episodes if e.interrupted_by_gap),
        longest=longest,
        longest_duration_s=longest.duration_s if longest else 0.0,
        total_duration_s=float(sum(e.duration_s for e in episodes)),
        sustained_duration_s=float(sum(e.duration_s for e in sustained)),
        sustained_per_week=(len(sustained) / (covered_s / WEEK)) if covered_s and covered_s > 0 else None,
        days_with_sustained=len(days),
        covered_days=covered_s / 86400.0 if covered_s is not None else None,
    )


def max_days_in_window(days: list | np.ndarray, window_days: int = 7) -> int:
    """Größte Anzahl der Kalendertage ``days``, die in ein Fenster von ``window_days``
    aufeinanderfolgenden Tagen fallen (z. B. „3 Tage innerhalb einer Woche“)."""
    if len(days) == 0:
        return 0
    ords = np.unique(np.array([pd.Timestamp(d).toordinal() for d in days]))
    right = np.searchsorted(ords, ords + window_days, side="left")
    return int((right - np.arange(len(ords))).max())


# ---------------------------------------------------------------------------
# Tag / Nacht
# ---------------------------------------------------------------------------
def night_mask(local_ts: pd.Series, settings: AnalysisSettings) -> np.ndarray:
    """True für Messungen in der Nacht (``night_start_hour`` … ``night_end_hour`` Lokalzeit).

    Zuordnung nach dem Zeitstempel der Messung; das repräsentierte Intervall (höchstens die
    Lückengrenze) wird nicht an der Tag/Nacht-Grenze geteilt.
    """
    h = local_ts.dt.hour.to_numpy()
    a, b = settings.night_start_hour, settings.night_end_hour
    if a == b:
        return np.zeros(len(h), dtype=bool)
    if a > b:
        return (h >= a) | (h < b)
    return (h >= a) & (h < b)


@dataclass
class PartShare(ResultMixin):
    label: str  # 'Tag' | 'Nacht'
    covered_hours: float
    mean_rh: float | None
    hours_elevated: float
    pct_elevated: float | None
    hours_high: float
    pct_high: float | None


@dataclass
class DayNightShares(ResultMixin):
    day: PartShare
    night: PartShare
    night_hours: str  # z. B. "22–6 Uhr"


def day_night_shares(
    df: pd.DataFrame | Prepared | None,
    settings: AnalysisSettings | None = None,
    gap_threshold_s: float | None = None,
    *,
    expected_s: float | None = None,
) -> DayNightShares:
    prep = prepare(df, settings, expected_s, gap_threshold_s)
    s = prep.settings
    f = metric_view(prep, HUMIDITY)
    night = night_mask(f[TS].dt.tz_convert(s.timezone), s) if len(f) else np.zeros(0, dtype=bool)
    rh = f[HUMIDITY].to_numpy(dtype=float)
    w = f[W].to_numpy(dtype=float)

    def part(label: str, m: np.ndarray) -> PartShare:
        cov = float(w[m].sum())
        el = float(w[m & (rh >= s.humidity_elevated)].sum())
        hi = float(w[m & (rh >= s.humidity_high)].sum())
        mean = weighted_mean(rh[m], w[m]) if m.any() else math.nan
        return PartShare(label, cov / HOUR, mean, el / HOUR, _pct(el, cov), hi / HOUR, _pct(hi, cov))

    return DayNightShares(
        day=part("Tag", ~night),
        night=part("Nacht", night),
        night_hours=f"{s.night_start_hour}–{s.night_end_hour} Uhr",
    )


# ---------------------------------------------------------------------------
# Gesamtanalyse Feuchte
# ---------------------------------------------------------------------------
@dataclass
class HumidityAnalysis(ResultMixin):
    bands: BandShares
    elevated_episodes: list[Episode]
    elevated_summary: EpisodeSummary
    high_episodes: list[Episode]
    high_summary: EpisodeSummary
    day_night: DayNightShares


def analyze_humidity(
    df: pd.DataFrame | Prepared | None,
    settings: AnalysisSettings | None = None,
    gap_threshold_s: float | None = None,
    *,
    expected_s: float | None = None,
    period_start: Any = None,
    period_end: Any = None,
) -> HumidityAnalysis:
    prep = prepare(df, settings, expected_s, gap_threshold_s, period_start, period_end)
    s = prep.settings
    covered = float(metric_view(prep, HUMIDITY)[W].sum()) if not prep.empty else 0.0
    el = find_episodes(prep, s.humidity_elevated)
    hi = find_episodes(prep, s.humidity_high)
    return HumidityAnalysis(
        bands=band_shares(prep),
        elevated_episodes=el,
        elevated_summary=episode_summary(el, s, covered, s.humidity_elevated),
        high_episodes=hi,
        high_summary=episode_summary(hi, s, covered, s.humidity_high),
        day_night=day_night_shares(prep),
    )

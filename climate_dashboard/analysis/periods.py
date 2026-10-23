"""Zeiträume: Aggregation, Monats-/Saison-/Tag-Nacht-Übersichten und Vergleiche.

Gruppierung immer in Lokalzeit (Grundregel 3). Tage an Zeitumstellungen haben 23 bzw. 25 Stunden;
Abdeckungen beziehen sich auf die tatsächliche Länge des Zeitfensters. Leere Zeitfenster bleiben
leer (NaN) – es wird nichts interpoliert (Grundregel 2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from climate_dashboard.analysis.humidity import (
    HOUR,
    _pct,
    band_shares,
    day_night_shares,
    episode_summary,
    find_episodes,
    night_mask,
)
from climate_dashboard.analysis.quality import ResultMixin, _ts, jsonable
from climate_dashboard.analysis.stats import (
    DEW_POINT_SPREAD,
    SECS,
    MetricSummary,
    Prepared,
    W,
    metric_view,
    prepare,
    slice_prepared,
    summarize_prepared,
    weighted_mean,
)
from climate_dashboard.domain.models import (
    ABS_HUMIDITY,
    DEW_POINT,
    HUMIDITY,
    METRICS,
    TEMPERATURE,
    TS,
    VPD,
    AnalysisSettings,
)

MONTH_COMPLETE_COVERAGE_PCT = 80.0
SEASON_SUFFICIENT_COVERAGE_PCT = 80.0
COMPARE_COVERAGE_MIN_PCT = 80.0
COMPARE_COVERAGE_DIFF_PP = 15.0
COMPARE_INTERVAL_RATIO = 1.5
COMPARE_LENGTH_DIFF = 0.10

MONTH_NAMES = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
               "Oktober", "November", "Dezember"]
SEASONS = {  # Monat → (Schlüssel, Name)
    3: ("spring", "Frühling"), 4: ("spring", "Frühling"), 5: ("spring", "Frühling"),
    6: ("summer", "Sommer"), 7: ("summer", "Sommer"), 8: ("summer", "Sommer"),
    9: ("autumn", "Herbst"), 10: ("autumn", "Herbst"), 11: ("autumn", "Herbst"),
    12: ("winter", "Winter"), 1: ("winter", "Winter"), 2: ("winter", "Winter"),
}
SEASON_START_MONTH = {"spring": 3, "summer": 6, "autumn": 9, "winter": 12}
_EPOCH = pd.Timestamp("1970-01-01", tz="UTC")


def _secs(t: pd.Timestamp) -> float:
    return (t.tz_convert("UTC") - _EPOCH).total_seconds()


def _localize(naive: pd.DatetimeIndex | pd.Timestamp, tz: str) -> Any:
    """Naive Lokalzeit → tz-aware. Mitternacht ist in Europa nie mehrdeutig; für andere Zonen
    gilt bei Mehrdeutigkeit die erste (Sommerzeit-)Variante, nicht existierende Zeiten werden
    vorgeschoben."""
    if isinstance(naive, pd.Timestamp):
        return naive.tz_localize(tz, ambiguous=True, nonexistent="shift_forward")
    return naive.tz_localize(tz, ambiguous=np.ones(len(naive), dtype=bool), nonexistent="shift_forward")


def _naive_local(t: Any, tz: str) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:
        return ts
    return ts.tz_convert(tz).tz_localize(None)


# ---------------------------------------------------------------------------
# Zeiträume
# ---------------------------------------------------------------------------
def _floor_naive(d: pd.Timestamp, kind: str) -> pd.Timestamp:
    d = d.normalize()
    if kind == "day":
        return d
    if kind == "week":
        return d - pd.Timedelta(days=d.weekday())
    if kind == "month":
        return d.replace(day=1)
    if kind == "year":
        return d.replace(month=1, day=1)
    raise ValueError(f"Unbekannte Zeitraumart: {kind}")


def _step_naive(d: pd.Timestamp, kind: str, n: int = 1) -> pd.Timestamp:
    if kind == "day":
        return d + pd.DateOffset(days=n)
    if kind == "week":
        return d + pd.DateOffset(weeks=n)
    if kind == "month":
        return d + pd.DateOffset(months=n)
    if kind == "year":
        return d + pd.DateOffset(years=n)
    raise ValueError(f"Unbekannte Zeitraumart: {kind}")


def period_bounds(kind: str, anchor: Any, tz: str = "Europe/Berlin") -> tuple[pd.Timestamp, pd.Timestamp]:
    """Kalenderzeitraum (``'day'|'week'|'month'|'year'``), der ``anchor`` enthält: ``[start, end)``
    als tz-aware Lokalzeit. Wochen beginnen montags (ISO)."""
    start = _floor_naive(_naive_local(anchor, tz), kind)
    end = _step_naive(start, kind)
    return _localize(start, tz), _localize(end, tz)


def previous_period(kind: str, anchor: Any, tz: str = "Europe/Berlin") -> tuple[pd.Timestamp, pd.Timestamp]:
    """Der unmittelbar vorhergehende Kalenderzeitraum gleicher Art."""
    start = _floor_naive(_naive_local(anchor, tz), kind)
    prev = _step_naive(start, kind, -1)
    return _localize(prev, tz), _localize(start, tz)


def same_period_previous_year(start: Any, end: Any, tz: str = "Europe/Berlin") -> tuple[pd.Timestamp, pd.Timestamp]:
    """Gleicher Kalenderzeitraum ein Jahr früher (z. B. Vorjahresmonat), in Lokalzeit verschoben."""
    s = _naive_local(start, tz) - pd.DateOffset(years=1)
    e = _naive_local(end, tz) - pd.DateOffset(years=1)
    return _localize(s, tz), _localize(e, tz)


def equal_length_window(start: Any, end: Any, tz: str = "Europe/Berlin") -> tuple[pd.Timestamp, pd.Timestamp]:
    """Gleich langer Zeitraum (absolute Dauer) unmittelbar vor ``[start, end)``."""
    s, e = _ts(start), _ts(end)
    length = e - s
    return (s - length).tz_convert(tz), s.tz_convert(tz)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
FREQ_KIND = {"D": "day", "W": "week", "MS": "month"}


def _windows(start: pd.Timestamp, end: pd.Timestamp, freq: str, tz: str) -> pd.DatetimeIndex:
    """Fenstergrenzen (inkl. Ende des letzten Fensters) als tz-aware Lokalzeit."""
    if freq == "h":
        s = start.tz_convert("UTC").floor("h")
        e = end.tz_convert("UTC").ceil("h")
        if e <= s:
            e = s + pd.Timedelta(hours=1)
        return pd.date_range(s, e, freq="h").tz_convert(tz)
    if freq not in FREQ_KIND:
        raise ValueError("freq muss 'h', 'D', 'W' oder 'MS' sein")
    kind = FREQ_KIND[freq]
    s = _floor_naive(_naive_local(start, tz), kind)
    e_naive = _naive_local(end, tz)
    bounds = [s]
    while bounds[-1] < e_naive:
        bounds.append(_step_naive(bounds[-1], kind))
    if len(bounds) == 1:
        bounds.append(_step_naive(s, kind))
    return _localize(pd.DatetimeIndex(bounds), tz)


def aggregate(
    df: pd.DataFrame | Prepared | None,
    freq: str = "h",
    tz: str | None = None,
    *,
    settings: AnalysisSettings | None = None,
    expected_s: float | None = None,
    gap_threshold_s: float | None = None,
    metrics: tuple[str, ...] | list[str] = METRICS,
    period_start: Any = None,
    period_end: Any = None,
) -> pd.DataFrame:
    """Zeitgewichtetes Mittel, Min und Max je Zeitfenster (``'h'|'D'|'W'|'MS'``) in Lokalzeit.

    Spalten: ``start``, ``end`` (Lokalzeit), ``hours`` (tatsächliche Fensterlänge, 23/25 h an
    Umstellungstagen), ``n``, ``covered_s``, ``coverage_pct`` und je Messgröße
    ``<m>_mean``/``<m>_min``/``<m>_max``. Fenster ohne Daten: NaN (keine Interpolation).
    Zeitgewichte werden der Fensterzugehörigkeit des Messzeitpunkts zugeordnet (nicht an
    Fenstergrenzen geteilt); die Abdeckung wird auf 100 % begrenzt.
    """
    if settings is None:
        settings = AnalysisSettings(timezone=tz) if tz else AnalysisSettings()
    prep = prepare(df, settings, expected_s, gap_threshold_s, period_start, period_end)
    tz = tz or prep.tz
    cols = ["start", "end", "hours", "n", "covered_s", "coverage_pct"]
    for m in metrics:
        cols += [f"{m}_mean", f"{m}_min", f"{m}_max"]
    if prep.period_start is None or prep.period_end is None:
        return pd.DataFrame({c: pd.Series(dtype=float) for c in cols})
    bounds = _windows(prep.period_start, prep.period_end, freq, tz)
    starts, ends = bounds[:-1], bounds[1:]
    k = len(starts)
    bsecs = np.array([_secs(b) for b in bounds])
    f = prep.frame
    win = np.searchsorted(bsecs, f[SECS].to_numpy(), side="right") - 1
    inside = (win >= 0) & (win < k)
    win_in = win[inside]
    w = f[W].to_numpy(dtype=float)[inside]
    length = np.diff(bsecs)
    out = pd.DataFrame({
        "start": starts,
        "end": ends,
        "hours": length / HOUR,
        "n": np.bincount(win_in, minlength=k).astype(int),
        "covered_s": np.bincount(win_in, weights=w, minlength=k),
    })
    out["coverage_pct"] = np.minimum(out["covered_s"].to_numpy() / length * 100.0, 100.0)
    for m in metrics:
        v = f[m].to_numpy(dtype=float)[inside] if m in f.columns else np.full(len(win_in), np.nan)
        ok = np.isfinite(v)
        sw = np.bincount(win_in[ok], weights=w[ok], minlength=k)
        svw = np.bincount(win_in[ok], weights=(v * w)[ok], minlength=k)
        cnt = np.bincount(win_in[ok], minlength=k)
        sv = np.bincount(win_in[ok], weights=v[ok], minlength=k)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean = np.where(sw > 0, svw / sw, np.where(cnt > 0, sv / np.maximum(cnt, 1), np.nan))
        grp = pd.Series(v[ok]).groupby(win_in[ok])
        out[f"{m}_mean"] = mean
        out[f"{m}_min"] = grp.min().reindex(range(k)).to_numpy()
        out[f"{m}_max"] = grp.max().reindex(range(k)).to_numpy()
    return out


def aggregate_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Aggregations-DataFrame → JSON-serialisierbare Datensätze (NaN → None)."""
    return jsonable(frame)


# ---------------------------------------------------------------------------
# Monate
# ---------------------------------------------------------------------------
@dataclass
class MonthSummary(ResultMixin):
    month: str  # 'YYYY-MM'
    label: str  # 'März 2026'
    start: pd.Timestamp
    end: pd.Timestamp
    period_hours: float
    data_from: pd.Timestamp | None
    data_to: pd.Timestamp | None
    n: int
    covered_hours: float
    time_coverage_pct: float
    completeness_pct: float | None
    complete: bool
    incomplete_reasons: list[str]
    kpis: dict[str, MetricSummary]
    hours_elevated: float
    pct_elevated: float | None
    hours_high: float
    pct_high: float | None
    sustained_episodes: int
    longest_episode_hours: float
    day_mean_temperature: float | None
    night_mean_temperature: float | None
    day_mean_humidity: float | None
    night_mean_humidity: float | None


def _day_night_means(prep: Prepared, metric: str) -> tuple[float, float]:
    f = metric_view(prep, metric)
    if len(f) == 0:
        return math.nan, math.nan
    night = night_mask(f[TS].dt.tz_convert(prep.tz), prep.settings)
    v, w = f[metric].to_numpy(dtype=float), f[W].to_numpy(dtype=float)
    day_m = weighted_mean(v[~night], w[~night]) if (~night).any() else math.nan
    night_m = weighted_mean(v[night], w[night]) if night.any() else math.nan
    return day_m, night_m


def _data_extent(prep: Prepared) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if prep.empty:
        return None, None
    return prep.frame[TS].iloc[0], prep.frame[TS].iloc[-1]


def _range_summary(full: Prepared, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    sub = slice_prepared(full, start, end)
    length = (end - start).total_seconds()
    covered = sub.covered_s
    hum = band_shares(sub) if not sub.empty else None
    eps = find_episodes(sub) if not sub.empty else []
    summ = episode_summary(eps, full.settings, covered)
    return {
        "sub": sub,
        "length": length,
        "covered": covered,
        "coverage": min(covered / length * 100.0, 100.0) if length > 0 else 0.0,
        "completeness": (min(len(sub.frame) / (length / full.expected_s) * 100.0, 100.0)
                         if full.expected_s and length > 0 else None),
        "bands": hum,
        "episodes": summ,
    }


def monthly_overview(
    df: pd.DataFrame | Prepared | None,
    settings: AnalysisSettings | None = None,
    expected_s: float | None = None,
    *,
    gap_threshold_s: float | None = None,
    metrics: tuple[str, ...] = (TEMPERATURE, HUMIDITY, DEW_POINT, ABS_HUMIDITY),
) -> list[MonthSummary]:
    """Übersicht je Kalendermonat (Lokalzeit) im Datenzeitraum.

    ``complete``: Zeitabdeckung ≥ 80 % UND der Monat liegt vollständig im Datenzeitraum.
    """
    full = prepare(df, settings, expected_s, gap_threshold_s)
    if full.empty:
        return []
    tz = full.tz
    first, last = _data_extent(full)
    bounds = _windows(first, last + pd.Timedelta(seconds=1), "MS", tz)
    out: list[MonthSummary] = []
    for start, end in zip(bounds[:-1], bounds[1:], strict=True):
        r = _range_summary(full, start, end)
        sub: Prepared = r["sub"]
        reasons: list[str] = []
        if first > start + pd.Timedelta(seconds=full.gap_threshold_s):
            reasons.append(f"Daten erst ab {first.tz_convert(tz):%d.%m.%Y} – Monatsanfang fehlt.")
        if last < end - pd.Timedelta(seconds=full.gap_threshold_s):
            reasons.append(f"Daten nur bis {last.tz_convert(tz):%d.%m.%Y} – Monatsende fehlt.")
        if r["coverage"] < MONTH_COMPLETE_COVERAGE_PCT:
            reasons.append(
                f"Zeitabdeckung {r['coverage']:.0f} % (unter {MONTH_COMPLETE_COVERAGE_PCT:.0f} %)."
            )
        bands = r["bands"]
        eps = r["episodes"]
        dt, nt = _day_night_means(sub, TEMPERATURE)
        dh, nh = _day_night_means(sub, HUMIDITY)
        local = start.tz_convert(tz)
        out.append(MonthSummary(
            month=f"{local:%Y-%m}",
            label=f"{MONTH_NAMES[local.month - 1]} {local.year}",
            start=local,
            end=end.tz_convert(tz),
            period_hours=r["length"] / HOUR,
            data_from=sub.frame[TS].iloc[0].tz_convert(tz) if not sub.empty else None,
            data_to=sub.frame[TS].iloc[-1].tz_convert(tz) if not sub.empty else None,
            n=len(sub.frame),
            covered_hours=r["covered"] / HOUR,
            time_coverage_pct=r["coverage"],
            completeness_pct=r["completeness"],
            complete=not reasons,
            incomplete_reasons=reasons,
            kpis=summarize_prepared(sub, metrics),
            hours_elevated=bands.hours_at_or_above_elevated if bands else 0.0,
            pct_elevated=bands.pct_at_or_above_elevated if bands else None,
            hours_high=bands.hours_at_or_above_high if bands else 0.0,
            pct_high=bands.pct_at_or_above_high if bands else None,
            sustained_episodes=eps.count_sustained,
            longest_episode_hours=eps.longest_duration_s / HOUR,
            day_mean_temperature=dt,
            night_mean_temperature=nt,
            day_mean_humidity=dh,
            night_mean_humidity=nh,
        ))
    return out


# ---------------------------------------------------------------------------
# Jahreszeiten
# ---------------------------------------------------------------------------
@dataclass
class SeasonSummary(ResultMixin):
    key: str  # z. B. 'winter-2025' (Winter 2025/26) oder 'spring-2026'
    season: str  # 'spring' | 'summer' | 'autumn' | 'winter'
    label: str  # 'Winter 2025/26', 'Frühling 2026'
    start: pd.Timestamp
    end: pd.Timestamp
    period_hours: float
    covered_hours: float
    time_coverage_pct: float  # bezogen auf die GANZE Jahreszeit
    partial: bool  # Datenzeitraum deckt nur einen Teil der Jahreszeit ab
    sufficient: bool
    reasons: list[str]
    kpis: dict[str, MetricSummary]
    pct_elevated: float | None
    hours_elevated: float
    sustained_episodes: int


def season_of(month: int, year: int) -> tuple[str, str, int]:
    """(Schlüssel, Name, Startjahr) der meteorologischen Jahreszeit; Jan/Feb gehören zum Winter,
    der im Dezember des Vorjahres beginnt."""
    key, name = SEASONS[month]
    start_year = year - 1 if month in (1, 2) else year
    return key, name, start_year


def season_bounds(season: str, start_year: int, tz: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    m = SEASON_START_MONTH[season]
    s = pd.Timestamp(year=start_year, month=m, day=1)
    return _localize(s, tz), _localize(s + pd.DateOffset(months=3), tz)


def seasonal_overview(
    df: pd.DataFrame | Prepared | None,
    settings: AnalysisSettings | None = None,
    expected_s: float | None = None,
    *,
    gap_threshold_s: float | None = None,
) -> list[SeasonSummary]:
    """Meteorologische Jahreszeiten (Frühling MAM, Sommer JJA, Herbst SON, Winter DJF).

    ``sufficient``: Zeitabdeckung bezogen auf die ganze Jahreszeit ≥ 80 %. Sonst Begründung
    (nur Teil der Jahreszeit im Datenzeitraum bzw. zu viele Lücken).
    """
    full = prepare(df, settings, expected_s, gap_threshold_s)
    if full.empty:
        return []
    tz = full.tz
    first, last = _data_extent(full)
    seen: list[tuple[str, str, int]] = []
    m = _naive_local(first, tz).replace(day=1).normalize()
    last_naive = _naive_local(last, tz)
    while m <= last_naive:
        s = season_of(m.month, m.year)
        if s not in seen:
            seen.append(s)
        m = m + pd.DateOffset(months=1)
    out: list[SeasonSummary] = []
    for key, name, year in seen:
        start, end = season_bounds(key, year, tz)
        r = _range_summary(full, start, end)
        sub: Prepared = r["sub"]
        label = f"{name} {year}/{(year + 1) % 100:02d}" if key == "winter" else f"{name} {year}"
        partial = first > start + pd.Timedelta(seconds=full.gap_threshold_s) or \
            last < end - pd.Timedelta(seconds=full.gap_threshold_s)
        reasons: list[str] = []
        if partial:
            reasons.append("Der Datenzeitraum umfasst nur einen Teil dieser Jahreszeit.")
        if r["coverage"] < SEASON_SUFFICIENT_COVERAGE_PCT:
            reasons.append(
                f"Nur {r['coverage']:.0f} % der Jahreszeit sind durch Messungen abgedeckt "
                f"(mindestens {SEASON_SUFFICIENT_COVERAGE_PCT:.0f} % nötig)."
            )
        bands = r["bands"]
        out.append(SeasonSummary(
            key=f"{key}-{year}",
            season=key,
            label=label,
            start=start,
            end=end,
            period_hours=r["length"] / HOUR,
            covered_hours=r["covered"] / HOUR,
            time_coverage_pct=r["coverage"],
            partial=partial,
            sufficient=r["coverage"] >= SEASON_SUFFICIENT_COVERAGE_PCT,
            reasons=reasons,
            kpis=summarize_prepared(sub, (TEMPERATURE, HUMIDITY, DEW_POINT)),
            pct_elevated=bands.pct_at_or_above_elevated if bands else None,
            hours_elevated=bands.hours_at_or_above_elevated if bands else 0.0,
            sustained_episodes=r["episodes"].count_sustained,
        ))
    return out


# ---------------------------------------------------------------------------
# Tag / Nacht
# ---------------------------------------------------------------------------
@dataclass
class PartStats(ResultMixin):
    label: str
    covered_hours: float
    means: dict[str, float | None]  # zeitgewichtete Mittel je Messgröße
    pct_elevated: float | None
    pct_high: float | None


@dataclass
class DayNightResult(ResultMixin):
    night_hours: str
    day: PartStats
    night: PartStats
    night_minus_day: dict[str, float | None]  # Temperatur/Taupunkt in K, rF in Prozentpunkten


def day_night(
    df: pd.DataFrame | Prepared | None,
    settings: AnalysisSettings | None = None,
    *,
    expected_s: float | None = None,
    gap_threshold_s: float | None = None,
    period_start: Any = None,
    period_end: Any = None,
) -> DayNightResult:
    """Tag/Nacht-Vergleich; Nacht = ``night_start_hour`` … ``night_end_hour`` Lokalzeit."""
    prep = prepare(df, settings, expected_s, gap_threshold_s, period_start, period_end)
    shares = day_night_shares(prep)
    metrics = (TEMPERATURE, HUMIDITY, DEW_POINT, ABS_HUMIDITY)
    means = {m: _day_night_means(prep, m) for m in metrics}
    day_cov = night_cov = 0.0
    if not prep.empty:
        nm = night_mask(prep.frame[TS].dt.tz_convert(prep.tz), prep.settings)
        w = prep.frame[W].to_numpy(dtype=float)
        day_cov, night_cov = float(w[~nm].sum()), float(w[nm].sum())

    def clean(x: float) -> float | None:
        return x if math.isfinite(x) else None

    return DayNightResult(
        night_hours=shares.night_hours,
        day=PartStats("Tag", day_cov / HOUR, {m: clean(v[0]) for m, v in means.items()},
                      shares.day.pct_elevated, shares.day.pct_high),
        night=PartStats("Nacht", night_cov / HOUR, {m: clean(v[1]) for m, v in means.items()},
                        shares.night.pct_elevated, shares.night.pct_high),
        night_minus_day={m: clean(v[1] - v[0]) for m, v in means.items()},
    )


# ---------------------------------------------------------------------------
# Vergleich zweier Zeiträume
# ---------------------------------------------------------------------------
@dataclass
class ComparisonItem(ResultMixin):
    key: str
    label: str
    unit: str
    a: float | None
    b: float | None
    delta: float | None  # b − a
    delta_unit: str
    delta_pct: float | None  # (b − a) / |a| · 100, nur wo sinnvoll
    note: str | None = None


@dataclass
class PeriodInfo(ResultMixin):
    label: str
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    days: float
    n: int
    covered_hours: float
    time_coverage_pct: float | None
    expected_interval_s: float | None


@dataclass
class ComparisonResult(ResultMixin):
    labels: tuple[str, str]
    a: PeriodInfo
    b: PeriodInfo
    items: list[ComparisonItem] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    def item(self, key: str) -> ComparisonItem | None:
        return next((i for i in self.items if i.key == key), None)


NOTE_CELSIUS = ("Prozentuale Änderungen sind bei °C nicht sinnvoll (kein absoluter Nullpunkt); "
                "angegeben ist die Differenz in Kelvin.")
NOTE_PP = ("Differenz in Prozentpunkten; eine prozentuale Änderung einer Prozentangabe wäre "
           "missverständlich.")


def _period_values(prep: Prepared) -> dict[str, float | None]:
    summ = summarize_prepared(prep, (TEMPERATURE, HUMIDITY, DEW_POINT, ABS_HUMIDITY, VPD, DEW_POINT_SPREAD))
    bands = band_shares(prep)
    eps = find_episodes(prep)
    covered = float(metric_view(prep, HUMIDITY)[W].sum()) if not prep.empty else 0.0
    es = episode_summary(eps, prep.settings, covered)
    f = metric_view(prep, DEW_POINT_SPREAD)
    w = f[W].to_numpy(dtype=float)
    below = float(w[f[DEW_POINT_SPREAD].to_numpy(dtype=float) < prep.settings.dew_point_spread_warn].sum())
    return {
        "temperature_mean": summ[TEMPERATURE].mean,
        "temperature_min": summ[TEMPERATURE].min,
        "temperature_max": summ[TEMPERATURE].max,
        "humidity_mean": summ[HUMIDITY].mean,
        "humidity_max": summ[HUMIDITY].max,
        "dew_point_mean": summ[DEW_POINT].mean,
        "abs_humidity_mean": summ[ABS_HUMIDITY].mean,
        "vpd_mean": summ[VPD].mean,
        "pct_elevated": bands.pct_at_or_above_elevated,
        "pct_high": bands.pct_at_or_above_high,
        "hours_elevated": bands.hours_at_or_above_elevated,
        "sustained_episodes": float(es.count_sustained),
        "sustained_per_week": es.sustained_per_week,
        "longest_episode_hours": es.longest_duration_s / HOUR,
        "pct_spread_below_warn": _pct(below, float(w.sum())),
        "time_coverage_pct": (min(prep.covered_s / prep.period_s * 100.0, 100.0) if prep.period_s > 0 else None),
    }


# key → (Bezeichnung, Einheit, Einheit der Differenz, Prozent sinnvoll?, Hinweis)
_COMPARE_SPEC: list[tuple[str, str, str, str, bool, str | None]] = [
    ("temperature_mean", "Mittlere Temperatur", "°C", "K", False, NOTE_CELSIUS),
    ("temperature_min", "Tiefste Temperatur", "°C", "K", False, NOTE_CELSIUS),
    ("temperature_max", "Höchste Temperatur", "°C", "K", False, NOTE_CELSIUS),
    ("humidity_mean", "Mittlere relative Luftfeuchtigkeit", "%", "Prozentpunkte", False, NOTE_PP),
    ("humidity_max", "Höchste relative Luftfeuchtigkeit", "%", "Prozentpunkte", False, NOTE_PP),
    ("dew_point_mean", "Mittlerer Taupunkt", "°C", "K", False, NOTE_CELSIUS),
    ("abs_humidity_mean", "Mittlere absolute Luftfeuchtigkeit", "g/m³", "g/m³", True, None),
    ("vpd_mean", "Mittleres Dampfdruckdefizit", "kPa", "kPa", True, None),
    ("pct_elevated", "Zeitanteil mit erhöhter Feuchte", "%", "Prozentpunkte", False, NOTE_PP),
    ("pct_high", "Zeitanteil mit hoher Feuchte", "%", "Prozentpunkte", False, NOTE_PP),
    ("hours_elevated", "Stunden mit erhöhter Feuchte", "h", "h", True, None),
    ("sustained_episodes", "Anhaltende Feuchtephasen", "Anzahl", "Anzahl", True, None),
    ("sustained_per_week", "Anhaltende Feuchtephasen je Woche", "je Woche", "je Woche", True, None),
    ("longest_episode_hours", "Längste Feuchtephase", "h", "h", True, None),
    ("pct_spread_below_warn", "Zeitanteil mit geringem Taupunktabstand", "%", "Prozentpunkte", False, NOTE_PP),
    ("time_coverage_pct", "Zeitabdeckung", "%", "Prozentpunkte", False, NOTE_PP),
]


def _info(prep: Prepared, label: str, values: dict[str, float | None]) -> PeriodInfo:
    tz = prep.tz
    return PeriodInfo(
        label=label,
        start=prep.period_start.tz_convert(tz) if prep.period_start is not None else None,
        end=prep.period_end.tz_convert(tz) if prep.period_end is not None else None,
        days=prep.period_s / 86400.0,
        n=len(prep.frame),
        covered_hours=prep.covered_s / HOUR,
        time_coverage_pct=values["time_coverage_pct"],
        expected_interval_s=prep.expected_s,
    )


def compare_periods(
    df_a: pd.DataFrame | Prepared | None,
    df_b: pd.DataFrame | Prepared | None,
    settings: AnalysisSettings | None = None,
    expected_s_a: float | None = None,
    expected_s_b: float | None = None,
    labels: tuple[str, str] = ("Zeitraum A", "Zeitraum B"),
    *,
    period_a: tuple[Any, Any] | None = None,
    period_b: tuple[Any, Any] | None = None,
) -> ComparisonResult:
    """Vergleicht zwei Zeiträume (B relativ zu A: Δ = B − A).

    Temperaturen/Taupunkte: nur absolute Differenz in K. Prozentgrößen: Differenz in
    Prozentpunkten. Relative Änderung (%) nur für verhältnisskalierte Größen (abs. Feuchte,
    VPD, Stunden, Anzahlen) und nur wenn A ≠ 0.
    """
    pa = prepare(df_a, settings, expected_s_a, None, *(period_a or (None, None)))
    pb = prepare(df_b, settings, expected_s_b, None, *(period_b or (None, None)))
    va, vb = _period_values(pa), _period_values(pb)
    items: list[ComparisonItem] = []
    for key, label, unit, dunit, pct_ok, note in _COMPARE_SPEC:
        a, b = va.get(key), vb.get(key)
        a = a if a is not None and math.isfinite(a) else None
        b = b if b is not None and math.isfinite(b) else None
        delta = b - a if a is not None and b is not None else None
        dpct = None
        if pct_ok and delta is not None and a not in (None, 0.0):
            dpct = delta / abs(a) * 100.0
        items.append(ComparisonItem(key, label, unit, a, b, delta, dunit, dpct, note))

    info_a, info_b = _info(pa, labels[0], va), _info(pb, labels[1], vb)
    caveats: list[str] = []
    ca, cb = info_a.time_coverage_pct, info_b.time_coverage_pct
    if ca is None or cb is None or info_a.n < 2 or info_b.n < 2:
        caveats.append("Mindestens einer der Zeiträume enthält zu wenige Messungen für einen belastbaren Vergleich.")
    elif min(ca, cb) < COMPARE_COVERAGE_MIN_PCT or abs(ca - cb) > COMPARE_COVERAGE_DIFF_PP:
        caveats.append(
            f"Die Datenqualität der Zeiträume unterscheidet sich (Zeitabdeckung {ca:.0f} % bzw. "
            f"{cb:.0f} %). Unterschiede können teilweise auf fehlende Daten zurückgehen."
        )
    ea, eb = info_a.expected_interval_s, info_b.expected_interval_s
    if ea and eb and max(ea, eb) / min(ea, eb) > COMPARE_INTERVAL_RATIO:
        caveats.append(
            f"Die Messintervalle unterscheiden sich ({ea / 60:.0f} min bzw. {eb / 60:.0f} min). "
            "Bei längeren Intervallen werden kurze Spitzen eher verpasst; Maxima und kurze Phasen "
            "sind daher nur eingeschränkt vergleichbar."
        )
    if info_a.days > 0 and info_b.days > 0 and \
            abs(info_a.days - info_b.days) / max(info_a.days, info_b.days) > COMPARE_LENGTH_DIFF:
        caveats.append(
            f"Die Zeiträume sind unterschiedlich lang ({info_a.days:.1f} bzw. {info_b.days:.1f} Tage). "
            "Stunden und Anzahlen sind nicht direkt vergleichbar – Zeitanteile (%) und Werte je Woche "
            "sind aussagekräftiger."
        )
    caveats.append(
        "Unterschiede zwischen Zeiträumen können auch durch Wetter, Jahreszeit und Nutzung des "
        "Raums entstehen; die Daten zeigen keine Ursachen."
    )
    return ComparisonResult(labels=tuple(labels), a=info_a, b=info_b, items=items, caveats=caveats)


def month_key(d: date | pd.Timestamp) -> str:
    return f"{d.year:04d}-{d.month:02d}"

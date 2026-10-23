"""Plotly-Diagrammfabriken im Designsystem.

Eingaben
--------
* Zeitreihen nehmen den **kanonischen Messdaten-DataFrame** (Spalte ``ts`` tz-aware UTC,
  Messgrößen float, NaN = fehlt). Die Umrechnung in lokale Zeit (``tz``) passiert hier.
* Aggregat-Diagramme nehmen kleine DataFrames/Dicts; die erwarteten Spalten stehen jeweils
  im Docstring. Spaltennamen sind bewusst tolerant (z. B. ``coverage`` als Anteil 0…1 oder
  Prozent 0…100).

Grundsätze
----------
* **Keine Interpolation.** Abstände größer als die Lückengrenze werden durch eine
  eingefügte NaN-Zeile unterbrochen (``connectgaps=False``), die Linie reißt sichtbar ab.
* **Downsampling nur für die Anzeige** (``downsample_for_display``): je Zeitfenster bleiben
  Minimum und Maximum jeder Messgröße erhalten, damit Spitzen nicht verschwinden.
  Kennzahlen werden immer aus den Originaldaten berechnet – nie aus dem Rückgabewert.
* **Mehrere Einheiten → gestapelte Teildiagramme mit geteilter x-Achse** statt sekundärer
  y-Achse. Begründung: Zwei y-Achsen in einem Feld verleiten zu Scheinkorrelationen
  (Skalen sind willkürlich gegeneinander verschiebbar), Gitterlinien passen nur zu einer
  Achse und auf dem Smartphone ist die rechte Achse kaum lesbar. Gestapelte Felder teilen
  Zoom/Pan, jede Achse trägt eindeutig ihre Einheit; Temperatur und Taupunkt (beide °C)
  liegen gemeinsam in einem Feld und sind so direkt vergleichbar.
* **Deutsche Darstellung:** ``separators=",."`` (Template), Datums-Ticks ausschließlich
  numerisch (``%d.%m.``) bzw. mit expliziten deutschen ``ticktext`` – Plotlys ``%b`` würde
  englische Monatsnamen liefern.
* **Zeitzone:** Plotly kennt keine Zeitzonen; Zeitstempel werden in lokale, naive Zeit
  umgerechnet. Bei der Zeitumstellung im Herbst erscheint die doppelte Stunde daher
  übereinanderliegend (bekannte, dokumentierte Einschränkung).

Ausgabe in Streamlit: ``components.render_chart(fig)`` (setzt ``theme=None``).
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from climate_dashboard.domain.models import (
    ABS_HUMIDITY,
    DEW_POINT,
    HUMIDITY,
    METRIC_UNITS,
    METRICS,
    TEMPERATURE,
    TS,
    VPD,
)
from climate_dashboard.ui import theme
from climate_dashboard.ui.formatting import (
    DEFAULT_TZ,
    MONTH_ABBR,
    NARROW_NBSP,
    WEEKDAY_ABBR,
    fmt_date,
    fmt_datetime,
    fmt_duration,
    fmt_month,
    fmt_number,
    fmt_percent,
    metric_decimals,
    metric_label,
    metric_unit,
    season_label,
)

AXIS_LABELS: dict[str, str] = {
    TEMPERATURE: "Temperatur",
    HUMIDITY: "Rel. Feuchte",
    DEW_POINT: "Taupunkt",
    VPD: "VPD",
    ABS_HUMIDITY: "Abs. Feuchte",
    "dew_point_spread": "Abstand zum Taupunkt",
}
# Mindest-Spanne der y-Achse, damit Sensorrauschen nicht dramatisch wirkt
MIN_SPAN: dict[str, float] = {TEMPERATURE: 3.0, DEW_POINT: 3.0, HUMIDITY: 12.0, VPD: 0.3, ABS_HUMIDITY: 2.0}
UNIT_ORDER: tuple[str, ...] = ("°C", "%", "g/m³", "kPa")

DEFAULT_MAX_POINTS = 5000
DEFAULT_GAP_FACTOR = 3.0
DEFAULT_MIN_GAP_MINUTES = 180  # wie AnalysisSettings.min_gap_minutes

DATE_HOVER = "%d.%m.%Y, %H:%M"
# Tick-Formate abhängig vom Zoom – rein numerisch (keine englischen Monatsnamen)
DATE_TICKFORMATSTOPS: list[dict[str, Any]] = [
    {"dtickrange": [None, 60_000], "value": "%H:%M:%S"},
    {"dtickrange": [60_000, 86_400_000], "value": "%H:%M<br>%d.%m."},
    {"dtickrange": [86_400_000, "M1"], "value": "%d.%m."},
    {"dtickrange": ["M1", "M12"], "value": "%m/%Y"},
    {"dtickrange": ["M12", None], "value": "%Y"},
]


# ---------------------------------------------------------------------------
# Zeit-Hilfen
# ---------------------------------------------------------------------------
def _to_local_naive(values: Any, tz: str | None) -> pd.Series:
    s = pd.to_datetime(pd.Series(values), utc=True)
    return s.dt.tz_convert(tz or DEFAULT_TZ).dt.tz_localize(None)


def _ts_local(value: Any, tz: str | None) -> pd.Timestamp:
    t = pd.Timestamp(value)
    t = t.tz_localize("UTC") if t.tzinfo is None else t
    return t.tz_convert(tz or DEFAULT_TZ).tz_localize(None)


def _seconds_since(ts: pd.Series) -> np.ndarray:
    if len(ts) == 0:
        return np.array([], dtype=float)
    return (ts - ts.iloc[0]).dt.total_seconds().to_numpy(dtype=float)


def estimate_gap_threshold(
    ts: pd.Series,
    *,
    expected_interval_s: float | None = None,
    gap_factor: float = DEFAULT_GAP_FACTOR,
    min_gap_minutes: float = DEFAULT_MIN_GAP_MINUTES,
) -> float:
    """Lückengrenze in Sekunden: ``max(gap_factor × Intervall, min_gap_minutes)``
    (gleiche Regel wie in der Analyse, siehe ARCHITECTURE.md). Ohne erwartetes Intervall
    wird der Median der Abstände verwendet."""
    interval = expected_interval_s
    if interval is None:
        secs = np.diff(
            _seconds_since(pd.to_datetime(pd.Series(ts), utc=True).sort_values().reset_index(drop=True))
        )
        secs = secs[secs > 0]
        interval = float(np.median(secs)) if len(secs) else 0.0
    return max(gap_factor * float(interval), min_gap_minutes * 60.0)


def find_gaps(ts: pd.Series, threshold_s: float) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Alle Abstände > ``threshold_s`` als (letzter Zeitpunkt vor, erster nach der Lücke)."""
    s = pd.to_datetime(pd.Series(ts), utc=True).sort_values().reset_index(drop=True)
    if len(s) < 2:
        return []
    diffs = np.diff(_seconds_since(s))
    idx = np.nonzero(diffs > threshold_s)[0]
    return [(s.iloc[i], s.iloc[i + 1]) for i in idx]


def insert_gap_breaks(df: pd.DataFrame, gaps: Iterable[tuple[Any, Any]], ts_col: str = TS) -> pd.DataFrame:
    """Fügt in jede Lücke eine Zeile mit NaN-Werten ein (Mitte der Lücke). Plotly
    unterbricht die Linie dort (``connectgaps=False``). Es werden keine Werte erfunden."""
    gaps = list(gaps)
    if not gaps:
        return df
    mids = [pd.Timestamp(a) + (pd.Timestamp(b) - pd.Timestamp(a)) / 2 for a, b in gaps]
    breaks = pd.DataFrame({ts_col: pd.to_datetime(pd.Series(mids), utc=True)})
    for col in df.columns:
        if col != ts_col:
            breaks[col] = np.nan
    breaks = breaks.astype(
        {c: "float64" for c in breaks.columns if c != ts_col and pd.api.types.is_numeric_dtype(df[c])}
    )
    out = pd.concat([df, breaks[df.columns]], ignore_index=True)
    out[ts_col] = pd.to_datetime(out[ts_col], utc=True)
    return out.sort_values(ts_col, kind="stable").reset_index(drop=True)


def downsample_for_display(
    df: pd.DataFrame,
    max_points: int = DEFAULT_MAX_POINTS,
    *,
    metrics: Sequence[str] | None = None,
    ts_col: str = TS,
) -> pd.DataFrame:
    """Reduziert eine Zeitreihe für die **Anzeige** auf höchstens ``max_points`` Zeilen.

    Verfahren (Min/Max-Bucketing): Der Zeitraum wird in gleich lange Zeitfenster geteilt
    (``max_points // (2 × Anzahl Messgrößen)`` Fenster). Je Fenster und Messgröße bleiben die
    Zeilen mit dem kleinsten und dem größten Wert erhalten – Spitzen, globale Extremwerte und
    Lücken (leere Fenster) bleiben damit sichtbar. Es werden ausschließlich vorhandene
    Originalzeilen zurückgegeben (keine Mittelwerte, keine neuen Werte).

    Der Eingabe-DataFrame wird nicht verändert; ``attrs["downsampled_from"]`` enthält die
    ursprüngliche Zeilenzahl, falls reduziert wurde. Nicht für Kennzahlen verwenden.
    """
    if df is None or len(df) <= max_points:
        return df
    metrics = [m for m in (metrics or METRICS) if m in df.columns]
    if not metrics:
        step = int(np.ceil(len(df) / max_points))
        return df.iloc[::step].copy()
    data = df.sort_values(ts_col, kind="stable").reset_index(drop=True)
    secs = _seconds_since(pd.to_datetime(data[ts_col], utc=True))
    n_bins = max(1, max_points // (2 * len(metrics)))
    span = secs[-1] - secs[0]
    bins = (
        np.zeros(len(secs), dtype=np.int64)
        if span <= 0
        else np.minimum((secs / span * n_bins).astype(np.int64), n_bins - 1)
    )
    keep = np.zeros(len(data), dtype=bool)
    for m in metrics:
        vals = data[m].to_numpy(dtype=float)
        idx = np.nonzero(~np.isnan(vals))[0]
        if len(idx) == 0:
            continue
        b, v = bins[idx], vals[idx]
        order = np.lexsort((v, b))  # nach Fenster, dann Wert
        sb = b[order]
        change = sb[1:] != sb[:-1]
        first = np.r_[True, change]
        last = np.r_[change, True]
        keep[idx[order[first]]] = True
        keep[idx[order[last]]] = True
    out = data.loc[keep].reset_index(drop=True)
    out.attrs = {**df.attrs, "downsampled_from": len(df)}
    return out


# ---------------------------------------------------------------------------
# Layout-Hilfen
# ---------------------------------------------------------------------------
def _base_figure(
    fig: go.Figure | None = None, *, title: str | None = None, height: int | None = None
) -> go.Figure:
    theme.apply_plotly_template(set_default=False)
    fig = fig if fig is not None else go.Figure()
    top = 56 if title else 36
    fig.update_layout(
        template=f"plotly_white+{theme.PLOTLY_TEMPLATE_NAME}",
        separators=",.",  # explizit, damit es auch ohne registriertes Template (Export) gilt
        title={"text": title} if title else None,
        margin={"t": top + 16, "l": 8, "r": 16, "b": 8},
    )
    if height:
        fig.update_layout(height=height)
    return fig


def _date_axis(fig: go.Figure, **selector: Any) -> None:
    fig.update_xaxes(
        type="date",
        tickformatstops=DATE_TICKFORMATSTOPS,
        hoverformat=DATE_HOVER,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikethickness=1,
        spikecolor=theme.COLORS["border_strong"],
        spikedash="solid",
        **selector,
    )


def empty_figure(
    text: str = "Keine Daten im gewählten Zeitraum.", *, title: str | None = None, height: int = 260
) -> go.Figure:
    fig = _base_figure(title=title, height=height)
    fig.add_annotation(
        text=text, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
        font={"size": 13, "color": theme.COLORS["text_subtle"]},
    )  # fmt: skip
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def _hover_value(metric: str, decimals: int | None = None, field: str = "y") -> str:
    d = metric_decimals(metric) if decimals is None else decimals
    unit = metric_unit(metric) if metric in METRIC_UNITS else ("K" if metric == "dew_point_spread" else "")
    return f"%{{{field}:,.{d}f}}{NARROW_NBSP}{unit}" if unit else f"%{{{field}:,.{d}f}}"


def axis_title(metrics: Sequence[str]) -> str:
    """``["temperature", "dew_point"]`` → ``"Temperatur / Taupunkt (°C)"``."""
    names = " / ".join(AXIS_LABELS.get(m, metric_label(m)) for m in metrics)
    unit = metric_unit(metrics[0]) if metrics else ""
    return f"{names} ({unit})" if unit else names


def _y_range(values: np.ndarray, metric: str, extra: Iterable[float] = ()) -> list[float] | None:
    vals = values[~np.isnan(values)] if len(values) else values
    pool: list[float] = []
    if len(vals):
        pool = [float(np.min(vals)), float(np.max(vals))]
    pool += [float(x) for x in extra]
    if not pool:
        return None
    lo, hi = min(pool), max(pool)
    span = max(hi - lo, MIN_SPAN.get(metric, 0.0))
    mid = (lo + hi) / 2
    lo, hi = min(lo, mid - span / 2), max(hi, mid + span / 2)
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    if metric == HUMIDITY:
        lo, hi = max(0.0, lo), min(100.0, hi)
    if metric in (VPD, ABS_HUMIDITY):
        lo = max(0.0, lo)
    return [lo, hi]


def default_thresholds(settings: Any = None) -> dict[str, list[tuple[float, str]]]:
    """Schwellenlinien aus ``AnalysisSettings`` (oder Standardwerten) für ``timeseries_chart``."""
    elevated = getattr(settings, "humidity_elevated", 60.0)
    high = getattr(settings, "humidity_high", 70.0)
    return {
        HUMIDITY: [
            (elevated, f"erhöht ab {fmt_number(elevated)}{NARROW_NBSP}%"),
            (high, f"hoch ab {fmt_number(high)}{NARROW_NBSP}%"),
        ]
    }


def _add_bands(
    fig: go.Figure, bands: Sequence[Mapping[str, Any]] | None, tz: str, label_yref: str = "y domain"
) -> None:
    for band in bands or []:
        lvl = theme.normalize_level(band.get("level", "hinweis"))
        x0, x1 = _ts_local(band["start"], tz), _ts_local(band["end"], tz)
        color = theme.STATUS[lvl]["solid"]
        fig.add_vrect(
            x0=x0,
            x1=x1,
            fillcolor=theme.hex_to_rgba(color, 0.12),
            line_width=0,
            layer="below",
            row="all",
            col=1,
        )
        if band.get("label"):
            fig.add_annotation(
                x=x0, xref="x", y=1, yref=label_yref, yanchor="top", xanchor="left", showarrow=False,
                text=str(band["label"]).replace("<", "&lt;"),
                font={"size": 10, "color": theme.STATUS[lvl]["fg"]}, bgcolor="rgba(255,255,255,0.7)",
            )  # fmt: skip


# ---------------------------------------------------------------------------
# Zeitreihe
# ---------------------------------------------------------------------------
def _group_by_unit(metrics: Sequence[str]) -> list[list[str]]:
    groups: dict[str, list[str]] = {}
    for m in metrics:
        groups.setdefault(metric_unit(m), []).append(m)
    order = {u: i for i, u in enumerate(UNIT_ORDER)}
    first_seen = {u: i for i, u in enumerate(groups)}
    return [groups[u] for u in sorted(groups, key=lambda u: (order.get(u, 99), first_seen[u]))]


def timeseries_chart(
    df: pd.DataFrame,
    metrics: Sequence[str] = (TEMPERATURE, HUMIDITY),
    tz: str = DEFAULT_TZ,
    title: str | None = None,
    *,
    bands: Sequence[Mapping[str, Any]] | None = None,
    thresholds: Mapping[str, Sequence[tuple[float, str]]] | None = None,
    show_gaps: bool = True,
    gap_threshold_s: float | None = None,
    expected_interval_s: float | None = None,
    max_points: int = DEFAULT_MAX_POINTS,
    rangeslider: bool = False,
    range_selector: bool = False,
    panel_height: int = 220,
) -> go.Figure:
    """Zeitverlauf einer oder mehrerer Messgrößen.

    * Messgrößen mit gleicher Einheit teilen ein Feld; verschiedene Einheiten → gestapelte
      Felder mit gemeinsamer x-Achse (Zoom/Pan synchron).
    * ``bands``: ``[{"start", "end", "label", "level"}]`` – farbige Hinterlegung auffälliger Phasen.
    * ``thresholds``: ``{metric: [(wert, "Beschriftung"), …]}`` – gestrichelte Schwellenlinien.
    * ``show_gaps``: Abstände > Lückengrenze unterbrechen die Linie (keine Interpolation).
    * Lange Reihen werden für die Anzeige Min/Max-erhaltend auf ``max_points`` reduziert.
    """
    metrics = [m for m in metrics if df is not None and m in df.columns]
    if df is None or df.empty or not metrics or df[metrics].isna().all().all():
        return empty_figure(title=title)

    data = df[[TS, *metrics]].copy()
    data[TS] = pd.to_datetime(data[TS], utc=True)
    data = data.sort_values(TS, kind="stable").reset_index(drop=True)
    gaps: list = []
    if show_gaps:
        threshold = gap_threshold_s or estimate_gap_threshold(
            data[TS], expected_interval_s=expected_interval_s
        )
        gaps = find_gaps(data[TS], threshold)
    shown = downsample_for_display(data, max_points, metrics=metrics)
    downsampled_from = shown.attrs.get("downsampled_from")
    shown = insert_gap_breaks(shown, gaps)
    x = _to_local_naive(shown[TS], tz)

    groups = _group_by_unit(metrics)
    rows = len(groups)
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.06 if rows > 1 else 0.0)
    for r, group in enumerate(groups, start=1):
        extra: list[float] = []
        for m in group:
            y = shown[m].to_numpy(dtype=float)
            fig.add_trace(
                go.Scatter(
                    x=x, y=y, mode="lines", name=metric_label(m), connectgaps=False,
                    line={"color": theme.METRIC_COLORS.get(m), "width": 1.6, "dash": theme.METRIC_DASH.get(m, "solid")},
                    hovertemplate=f"{_hover_value(m)}<extra>{metric_label(m)}</extra>",
                ),
                row=r, col=1,
            )  # fmt: skip
            for value, label in (thresholds or {}).get(m, []):
                extra.append(float(value))
                fig.add_hline(
                    y=value, row=r, col=1, line={"color": theme.NEUTRAL[400], "width": 1, "dash": "dot"},
                    annotation_text=str(label).replace("<", "&lt;"), annotation_position="top right",
                    annotation_font={"size": 10, "color": theme.COLORS["text_subtle"]},
                )  # fmt: skip
        allvals = np.concatenate([shown[m].to_numpy(dtype=float) for m in group])
        rng = _y_range(allvals, group[0], extra)
        fig.update_yaxes(title_text=axis_title(group), range=rng, row=r, col=1)

    _add_bands(fig, bands, tz)
    _date_axis(fig)
    height = 70 + panel_height * rows + (60 if rangeslider else 0) + (24 if title else 0)
    _base_figure(fig, title=title, height=height)
    if rangeslider:
        fig.update_xaxes(rangeslider={"visible": True, "thickness": 0.08}, row=rows, col=1)
    if range_selector:
        fig.update_xaxes(
            rangeselector={
                "buttons": [
                    {"count": 1, "label": "24 Std.", "step": "day", "stepmode": "backward"},
                    {"count": 7, "label": "7 Tage", "step": "day", "stepmode": "backward"},
                    {"count": 1, "label": "1 Monat", "step": "month", "stepmode": "backward"},
                    {"step": "all", "label": "Alles"},
                ],
                "x": 1, "xanchor": "right", "y": 1.02, "yanchor": "bottom",
                "bgcolor": theme.COLORS["surface_muted"], "activecolor": theme.COLORS["primary_soft"],
                "font": {"size": 11},
            },
            row=1, col=1,
        )  # fmt: skip
    if downsampled_from:
        # Für Hinweise wie „Anzeige vereinfacht (x von y Punkten)“ in der Oberfläche
        fig.update_layout(meta={"downsampled_from": int(downsampled_from), "points_shown": int(len(x))})
    return fig


# ---------------------------------------------------------------------------
# Feuchtebereiche
# ---------------------------------------------------------------------------
def band_share_bar(
    shares: Mapping[str, float] | pd.DataFrame,
    *,
    labels: Mapping[str, str] | None = None,
    title: str | None = None,
    value_is_fraction: bool = True,
    group_col: str = "group",
    band_col: str = "band",
    value_col: str = "share",
    duration_col: str = "duration_s",
) -> go.Figure:
    """Zeitanteile in Feuchtebereichen als gestapelter horizontaler Balken (Summe 100 %).

    ``shares``: ``{band: anteil}`` (ein Balken) **oder** DataFrame im Langformat mit
    ``group`` (z. B. Raum/Zeitraum), ``band``, ``share`` und optional ``duration_s``.
    Bekannte Bereichsschlüssel (``dry``/``normal``/``elevated``/``high``/``gap`` bzw. deutsch)
    erhalten feste Farben; Beschriftungen über ``labels`` (z. B. mit Schwellenwerten).
    """
    if isinstance(shares, Mapping):
        df = pd.DataFrame({group_col: "", band_col: list(shares.keys()), value_col: list(shares.values())})
    else:
        df = shares.copy()
        if group_col not in df.columns:
            df[group_col] = ""
    if df.empty:
        return empty_figure(title=title, height=160)
    df[value_col] = df[value_col].astype(float) * (100.0 if value_is_fraction else 1.0)
    labels = dict(labels or {})
    known_order = ["dry", "normal", "elevated", "high", "gap"]
    bands = list(dict.fromkeys(df[band_col]))
    bands.sort(
        key=lambda b: (
            known_order.index(theme.normalize_band(b)) if theme.normalize_band(b) in known_order else 99
        )
    )
    groups = [html.escape(str(g)) for g in dict.fromkeys(df[group_col])]
    df = df.assign(**{group_col: df[group_col].map(lambda g: html.escape(str(g)))})

    fig = go.Figure()
    for i, band in enumerate(bands):
        key = theme.normalize_band(band)
        color = theme.HUMIDITY_BAND_COLORS.get(
            key or "", theme.FALLBACK_SERIES[i % len(theme.FALLBACK_SERIES)]
        )
        name = html.escape(str(labels.get(band) or theme.HUMIDITY_BAND_LABELS.get(key or "", str(band))))
        sub = df[df[band_col] == band].set_index(group_col).reindex(groups)
        vals = sub[value_col].to_numpy(dtype=float)
        dur = sub[duration_col].to_numpy() if duration_col in sub.columns else [None] * len(groups)
        hover = [
            f"{name}: {fmt_percent(v, 1)}"
            + (f" ({fmt_duration(d)})" if d is not None and not pd.isna(d) else "")
            for v, d in zip(vals, dur, strict=True)
        ]
        text = [fmt_percent(v) if not np.isnan(v) and v >= 6 else "" for v in vals]
        dark = key in ("high", "elevated")
        fig.add_trace(
            go.Bar(
                y=groups, x=vals, orientation="h", name=name, marker={"color": color, "line": {"width": 0}},
                text=text, textposition="inside", insidetextanchor="middle",
                textfont={"color": "#FFFFFF" if dark else theme.COLORS["text"], "size": 11},
                customdata=hover, hovertemplate="%{customdata}<extra></extra>",
                marker_pattern_shape="/" if key == "gap" else "",
            )
        )  # fmt: skip
    single = len(groups) == 1 and groups[0] == ""
    fig.update_layout(barmode="stack", hovermode="closest", bargap=0.35, legend={"traceorder": "normal"})
    fig.update_xaxes(
        range=[0, 100], ticksuffix=f"{NARROW_NBSP}%", showgrid=False, title_text="Anteil der Zeit"
    )
    fig.update_yaxes(visible=not single, autorange="reversed", showgrid=False, showline=False, ticks="")
    return _base_figure(
        fig, title=title, height=(130 if single else 90 + 48 * len(groups)) + (24 if title else 0)
    )


# ---------------------------------------------------------------------------
# Monats-/Saisonübersicht
# ---------------------------------------------------------------------------
def _coverage_pct(df: pd.DataFrame) -> pd.Series | None:
    for col in ("coverage_pct", "coverage", "time_coverage"):
        if col in df.columns:
            s = df[col].astype(float)
            return s * 100 if s.max(skipna=True) <= 1.0 else s
    return None


def _incomplete_mask(df: pd.DataFrame, min_coverage_pct: float = 90.0) -> np.ndarray:
    for col in ("complete", "is_complete"):
        if col in df.columns:
            return ~df[col].fillna(False).astype(bool).to_numpy()
    if "incomplete" in df.columns:
        return df["incomplete"].fillna(True).astype(bool).to_numpy()
    cov = _coverage_pct(df)
    if cov is not None:
        return (cov.fillna(0) < min_coverage_pct).to_numpy()
    return np.zeros(len(df), dtype=bool)


def _range_chart(
    categories: list[str], df: pd.DataFrame, metric: str, *, title: str | None, x_title: str | None
) -> go.Figure:
    color = theme.METRIC_COLORS.get(metric, theme.COLORS["primary"])
    incomplete = _incomplete_mask(df)
    cov = _coverage_pct(df)
    mean = df["mean"].to_numpy(dtype=float)
    has_range = {"min", "max"} <= set(df.columns)
    lo = df["min"].to_numpy(dtype=float) if has_range else mean
    hi = df["max"].to_numpy(dtype=float) if has_range else mean
    notes = []
    for i in range(len(df)):
        note = ""
        if incomplete[i]:
            note = (
                "<br><i>Unvollständig"
                + (f" – Abdeckung {fmt_percent(cov.iloc[i])}" if cov is not None else "")
                + "</i>"
            )
        elif cov is not None:
            note = f"<br>Abdeckung {fmt_percent(cov.iloc[i])}"
        notes.append(note)
    custom = np.array([[categories[i], lo[i], hi[i], notes[i]] for i in range(len(df))], dtype=object)

    fig = go.Figure()
    if has_range:
        for flag, name in ((False, "Spanne Min–Max"), (True, "Spanne (unvollständig)")):
            sel = incomplete == flag
            if not sel.any():
                continue
            fig.add_trace(
                go.Bar(
                    x=np.array(categories)[sel], y=(hi - lo)[sel], base=lo[sel], name=name,
                    marker={"color": theme.hex_to_rgba(color, 0.12 if flag else 0.22), "line": {"width": 1 if flag else 0,
                            "color": theme.hex_to_rgba(color, 0.45)},
                            "pattern": {"shape": "/" if flag else "", "fgcolor": theme.hex_to_rgba(color, 0.45), "size": 6}},
                    hoverinfo="skip", width=0.55,
                )
            )  # fmt: skip
    d = metric_decimals(metric)
    unit = metric_unit(metric)
    fig.add_trace(
        go.Scatter(
            x=categories, y=mean, mode="lines+markers", name="Mittelwert", customdata=custom, connectgaps=False,
            line={"color": color, "width": 1.5},
            marker={"size": 9, "color": [theme.COLORS["surface"] if f else color for f in incomplete],
                    "line": {"color": color, "width": 2}, "symbol": "circle"},
            hovertemplate=(
                f"<b>%{{customdata[0]}}</b><br>Mittel: %{{y:,.{d}f}}{NARROW_NBSP}{unit}"
                + (f"<br>Min: %{{customdata[1]:,.{d}f}}{NARROW_NBSP}{unit} · Max: %{{customdata[2]:,.{d}f}}{NARROW_NBSP}{unit}"
                   if has_range else "")
                + "%{customdata[3]}<extra></extra>"
            ),
        )
    )  # fmt: skip
    fig.update_layout(hovermode="closest", barmode="overlay")
    fig.update_xaxes(type="category", title_text=x_title, showgrid=False)
    fig.update_yaxes(title_text=axis_title([metric]), range=_y_range(np.concatenate([lo, hi, mean]), metric))
    return _base_figure(fig, title=title, height=340 + (24 if title else 0))


def monthly_chart(df: pd.DataFrame, metric: str = HUMIDITY, *, title: str | None = None) -> go.Figure:
    """Monatsmittel mit Min/Max-Spanne.

    ``df``-Spalten: ``month`` (``"2026-03"``, Timestamp oder Period), ``mean``, optional ``min``,
    ``max``, ``complete`` (bool) bzw. ``coverage``/``coverage_pct``. Unvollständige Monate
    (``complete=False`` oder Abdeckung < 90 %) sind schraffiert, der Mittelwertpunkt hohl,
    der Hover nennt die Abdeckung.
    """
    if df is None or df.empty:
        return empty_figure(title=title)
    col = "month" if "month" in df.columns else "period"
    data = df.copy().reset_index(drop=True)
    months = [pd.Period(str(m)[:7], freq="M") if not isinstance(m, pd.Period) else m for m in data[col]]
    years = {p.year for p in months}
    one_year = len(years) == 1
    cats = [MONTH_ABBR[p.month - 1] if one_year else fmt_month(p.to_timestamp(), short=True) for p in months]
    fig = _range_chart(cats, data, metric, title=title, x_title=str(next(iter(years))) if one_year else None)
    # Hover soll den vollen Monatsnamen zeigen
    full = [fmt_month(p.to_timestamp()) for p in months]
    fig.data[-1].customdata = np.array(
        [[full[i], *fig.data[-1].customdata[i][1:]] for i in range(len(full))], dtype=object
    )
    return fig


def seasonal_chart(df: pd.DataFrame, metric: str = HUMIDITY, *, title: str | None = None) -> go.Figure:
    """Jahreszeiten-Übersicht (meteorologisch). Spalten: ``season`` (``winter``/``Sommer``/…),
    optional ``year``, ``mean``, ``min``, ``max``, ``complete``/``coverage`` (wie ``monthly_chart``)."""
    if df is None or df.empty:
        return empty_figure(title=title)
    data = df.copy().reset_index(drop=True)
    years = data["year"] if "year" in data.columns else [None] * len(data)
    cats = [
        season_label(s, int(y) if y is not None and not pd.isna(y) else None)
        for s, y in zip(data["season"], years, strict=True)
    ]
    return _range_chart(cats, data, metric, title=title, x_title=None)


# ---------------------------------------------------------------------------
# Tag/Nacht
# ---------------------------------------------------------------------------
def _phase_key(value: Any) -> str:
    v = str(value).strip().lower()
    return "night" if v in ("night", "nacht", "n") else "day"


def day_night_chart(
    df: pd.DataFrame,
    metric: str = HUMIDITY,
    *,
    title: str | None = None,
    day_label: str = "Tag",
    night_label: str = "Nacht",
    group_col: str = "group",
) -> go.Figure:
    """Tag/Nacht-Vergleich als Hantel-Diagramm (kein Null-Basis-Balken, Unterschied direkt
    ablesbar). Spalten: ``phase`` (``day``/``night`` bzw. ``Tag``/``Nacht``), ``mean`` und
    optional ``group`` (z. B. Monat als ``"2026-03"``/Timestamp oder freier Text)."""
    if df is None or df.empty:
        return empty_figure(title=title)
    data = df.copy()
    data["_phase"] = data["phase"].map(_phase_key)
    if group_col not in data.columns:
        data[group_col] = "Gesamt"

    def label(g: Any) -> str:
        if isinstance(g, pd.Timestamp | pd.Period):
            return fmt_month(g if isinstance(g, pd.Timestamp) else g.to_timestamp(), short=True)
        if isinstance(g, str) and len(g) == 7 and g[4] == "-" and g[:4].isdigit():
            return fmt_month(g, short=True)
        return str(g)

    groups = list(dict.fromkeys(data[group_col]))
    cats = [label(g) for g in groups]
    piv = data.pivot_table(index=group_col, columns="_phase", values="mean", aggfunc="first").reindex(groups)
    day = piv.get("day", pd.Series(np.nan, index=piv.index)).to_numpy(dtype=float)
    night = piv.get("night", pd.Series(np.nan, index=piv.index)).to_numpy(dtype=float)

    xs, ys = [], []
    for c, a, b in zip(cats, day, night, strict=True):
        xs += [c, c, None]
        ys += [a, b, None]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line={"color": theme.NEUTRAL[300], "width": 3},
                             hoverinfo="skip", showlegend=False))  # fmt: skip
    for vals, name, key, symbol in (
        (day, day_label, "day", "circle"),
        (night, night_label, "night", "diamond"),
    ):
        fig.add_trace(
            go.Scatter(
                x=cats, y=vals, mode="markers", name=name,
                marker={"size": 12, "color": theme.DAY_NIGHT_COLORS[key], "symbol": symbol,
                        "line": {"color": "#FFFFFF", "width": 1.5}},
                hovertemplate=f"{name}: {_hover_value(metric)}<extra></extra>",
            )
        )  # fmt: skip
    fig.update_layout(hovermode="x unified")
    fig.update_xaxes(type="category", showgrid=False)
    fig.update_yaxes(title_text=axis_title([metric]), range=_y_range(np.concatenate([day, night]), metric))
    return _base_figure(fig, title=title, height=320 + (24 if title else 0))


# ---------------------------------------------------------------------------
# Vergleich zweier Zeiträume
# ---------------------------------------------------------------------------
def _relative_days(ts: pd.Series, tz: str, start: Any | None) -> tuple[np.ndarray, pd.Series]:
    local = _to_local_naive(ts, tz)
    origin = _ts_local(start, tz).normalize() if start is not None else local.min().normalize()
    return ((local - origin).dt.total_seconds() / 86400.0 + 1.0).to_numpy(), local


def comparison_chart(
    a: pd.DataFrame,
    b: pd.DataFrame,
    metric: str = HUMIDITY,
    *,
    labels: tuple[str, str] = ("Zeitraum A", "Zeitraum B"),
    tz: str = DEFAULT_TZ,
    mode: str = "overlay",
    starts: tuple[Any, Any] | None = None,
    title: str | None = None,
    max_points: int = DEFAULT_MAX_POINTS,
) -> go.Figure:
    """Zwei Zeiträume im Vergleich.

    * ``mode="overlay"``: beide Verläufe auf relativer Zeitachse „Tag 1 … n“ übereinander
      (Lücken bleiben sichtbar). ``starts`` legt den Beginn je Zeitraum fest (Standard:
      Mitternacht des ersten Messpunkts).
    * ``mode="daily"``: Tagesmittel nebeneinander als Balken. Tagesmittel werden aus
      Stundenmitteln gebildet (nähert die Zeitgewichtung bei unregelmäßigen Intervallen an).
    """
    frames = []
    for i, d in enumerate((a, b)):
        if d is None or d.empty or metric not in d.columns or d[metric].isna().all():
            frames.append(None)
            continue
        x = d[[TS, metric]].copy()
        x[TS] = pd.to_datetime(x[TS], utc=True)
        frames.append((x.sort_values(TS).reset_index(drop=True), (starts or (None, None))[i]))
    if all(f is None for f in frames):
        return empty_figure(title=title)

    # Beschriftungen können frei gewählte Namen enthalten – Plotly rendert ein HTML-Subset.
    labels = tuple(html.escape(str(label)) for label in labels)
    fig = go.Figure()
    unit = metric_unit(metric)
    d = metric_decimals(metric)
    for i, item in enumerate(frames):
        if item is None:
            continue
        data, start = item
        color = theme.COMPARE_COLORS[i]
        if mode == "daily":
            local = _to_local_naive(data[TS], tz)
            hourly = data.set_index(local)[metric].resample("1h").mean()
            daily = hourly.resample("1D").mean()
            origin = _ts_local(start, tz).normalize() if start is not None else daily.index.min()
            day_no = ((daily.index - origin) / pd.Timedelta(days=1)).astype(int) + 1
            fig.add_trace(
                go.Bar(
                    x=list(day_no), y=daily.to_numpy(dtype=float), name=labels[i],
                    marker={"color": color if i == 0 else theme.hex_to_rgba(color, 0.85)},
                    customdata=[fmt_date(t.tz_localize(tz), tz, style="weekday") for t in daily.index],
                    hovertemplate=f"{labels[i]} – %{{customdata}}: %{{y:,.{d}f}}{NARROW_NBSP}{unit}<extra></extra>",
                )
            )  # fmt: skip
        else:
            threshold = estimate_gap_threshold(data[TS])
            gaps = find_gaps(data[TS], threshold)
            shown = insert_gap_breaks(downsample_for_display(data, max_points // 2, metrics=[metric]), gaps)
            rel, local = _relative_days(shown[TS], tz, start)
            fig.add_trace(
                go.Scatter(
                    x=rel, y=shown[metric].to_numpy(dtype=float), mode="lines", name=labels[i], connectgaps=False,
                    line={"color": color, "width": 1.6, "dash": "solid" if i == 0 else "dash"},
                    customdata=[f"{WEEKDAY_ABBR[t.weekday()]} {t:%d.%m.%Y, %H:%M}" if not pd.isna(t) else "" for t in local],
                    hovertemplate=f"{labels[i]}: %{{y:,.{d}f}}{NARROW_NBSP}{unit} · %{{customdata}}<extra></extra>",
                )
            )  # fmt: skip
    if mode == "daily":
        fig.update_layout(barmode="group", hovermode="x unified")
        fig.update_xaxes(type="linear", tickprefix="Tag ", title_text=None, showgrid=False, dtick=1 if fig.data and
                         len(fig.data[0].x) <= 14 else None)  # fmt: skip
        vals = np.concatenate([np.asarray(t.y, dtype=float) for t in fig.data])
        fig.update_yaxes(title_text=axis_title([metric]), range=_y_range(vals, metric))
    else:
        fig.update_layout(hovermode="x unified")
        fig.update_xaxes(
            type="linear", tickprefix="Tag ", title_text=None, hoverformat=".1f", rangemode="tozero"
        )
        vals = np.concatenate([np.asarray(t.y, dtype=float) for t in fig.data])
        fig.update_yaxes(title_text=axis_title([metric]), range=_y_range(vals, metric))
    return _base_figure(fig, title=title, height=360 + (24 if title else 0))


# ---------------------------------------------------------------------------
# Heatmap Stunde × Tag
# ---------------------------------------------------------------------------
_SEQ_SCALES: dict[str, list[list[Any]]] = {
    HUMIDITY: [[0.0, "#F3F7FB"], [0.35, "#BFD6EC"], [0.7, "#4F8BC9"], [1.0, "#0B3D7A"]],
    TEMPERATURE: [[0.0, "#FBF4EE"], [0.4, "#F2C29B"], [0.75, "#E07B39"], [1.0, "#8F2D0B"]],
    DEW_POINT: [[0.0, "#F1F8F5"], [0.5, "#79C2A6"], [1.0, "#0B5E45"]],
}


def heatmap_hour_by_day(
    df: pd.DataFrame, metric: str = HUMIDITY, tz: str = DEFAULT_TZ, *, title: str | None = None
) -> go.Figure:
    """Stundenmittel als Raster: x = Kalendertag, y = Uhrzeit (lokal). Stunden ohne Messwert
    bleiben leer (Hintergrund sichtbar) – es wird nichts aufgefüllt."""
    if df is None or df.empty or metric not in df.columns or df[metric].isna().all():
        return empty_figure(title=title)
    local = _to_local_naive(df[TS], tz)
    s = pd.Series(df[metric].to_numpy(dtype=float), index=local)
    frame = pd.DataFrame(
        {"v": s.to_numpy(), "date": local.dt.normalize().to_numpy(), "hour": local.dt.hour.to_numpy()}
    )
    grid = frame.groupby(["hour", "date"])["v"].mean().unstack("date")
    days = pd.date_range(frame["date"].min(), frame["date"].max(), freq="D")
    grid = grid.reindex(index=range(24), columns=days)
    z = grid.to_numpy(dtype=float)
    custom = np.array(
        [
            [f"{WEEKDAY_ABBR[d.weekday()]} {d:%d.%m.%Y}, {h:02d}–{(h + 1) % 24:02d} Uhr" for d in days]
            for h in range(24)
        ],
        dtype=object,
    )
    unit = metric_unit(metric)
    fig = go.Figure(
        go.Heatmap(
            x=days, y=list(range(24)), z=z, customdata=custom, hoverongaps=False, xgap=1, ygap=1,
            colorscale=_SEQ_SCALES.get(metric, _SEQ_SCALES[HUMIDITY]),
            colorbar={"title": {"text": unit, "side": "top"}, "thickness": 10, "len": 0.9, "outlinewidth": 0,
                      "ticksuffix": "", "tickformat": f",.{metric_decimals(metric)}f"},
            hovertemplate=f"%{{customdata}}<br><b>{_hover_value(metric, field='z')}</b><extra></extra>",
        )
    )  # fmt: skip
    fig.update_layout(hovermode="closest", plot_bgcolor=theme.COLORS["surface_muted"])
    fig.update_xaxes(type="date", tickformat="%d.%m.", showgrid=False, ticks="", showline=False)
    fig.update_yaxes(
        autorange="reversed", tickvals=[0, 3, 6, 9, 12, 15, 18, 21],
        ticktext=[f"{h:02d}:00" for h in range(0, 24, 3)], title_text="Uhrzeit", showgrid=False, ticks="", showline=False,
    )  # fmt: skip
    return _base_figure(fig, title=title or None, height=420 + (24 if title else 0))


# ---------------------------------------------------------------------------
# Phasen-Zeitleiste
# ---------------------------------------------------------------------------
def episode_timeline(
    episodes: pd.DataFrame | Sequence[Mapping[str, Any]],
    *,
    tz: str = DEFAULT_TZ,
    metric: str = HUMIDITY,
    period: tuple[Any, Any] | None = None,
    title: str | None = None,
    row_col: str = "label",
) -> go.Figure:
    """Gantt-artige Darstellung zusammenhängender Phasen.

    Spalten/Keys: ``start``, ``end`` (Zeitpunkte, UTC oder tz-aware), optional ``level``
    (Farbe), ``label`` (Zeile, z. B. „≥ 70 % rF“), ``max`` (Höchstwert in der Phase).
    Sehr kurze Phasen werden mit Mindestbreite gezeichnet, damit sie sichtbar bleiben; der
    Hover zeigt immer die tatsächliche Dauer.
    """
    df = episodes.copy() if isinstance(episodes, pd.DataFrame) else pd.DataFrame(list(episodes))
    if df.empty:
        return empty_figure("Keine Phasen im gewählten Zeitraum.", title=title, height=200)
    start = _to_local_naive(df["start"], tz)
    end = _to_local_naive(df["end"], tz)
    dur_s = (end - start).dt.total_seconds()
    lo = _ts_local(period[0], tz) if period else start.min()
    hi = _ts_local(period[1], tz) if period else end.max()
    span_ms = max((hi - lo).total_seconds() * 1000, 1.0)
    width_ms = np.maximum(dur_s.to_numpy() * 1000, span_ms * 0.004)
    rows = df[row_col].astype(str).map(html.escape) if row_col in df.columns else pd.Series(["Phasen"] * len(df))
    levels = (
        df["level"].map(theme.normalize_level) if "level" in df.columns else pd.Series(["hinweis"] * len(df))
    )
    maxes = df["max"] if "max" in df.columns else pd.Series([None] * len(df))
    custom = np.array(
        [
            [
                fmt_datetime(s.tz_localize(tz), tz),
                fmt_datetime(e.tz_localize(tz), tz),
                fmt_duration(d),
                ""
                if m is None or pd.isna(m)
                else f"<br>Höchstwert: {fmt_number(m, metric_decimals(metric))}{NARROW_NBSP}{metric_unit(metric)}",
            ]
            for s, e, d, m in zip(start, end, dur_s, maxes, strict=True)
        ],
        dtype=object,
    )
    fig = go.Figure()
    for lvl in dict.fromkeys(levels):
        sel = (levels == lvl).to_numpy()
        fig.add_trace(
            go.Bar(
                y=rows[sel], x=width_ms[sel], base=start[sel], orientation="h", name=theme.level_label(lvl),
                marker={"color": theme.STATUS[lvl]["solid"], "line": {"width": 0}}, customdata=custom[sel],
                hovertemplate="<b>%{y}</b><br>%{customdata[0]} – %{customdata[1]}<br>Dauer: %{customdata[2]}"
                "%{customdata[3]}<extra></extra>",
                showlegend=levels.nunique() > 1,
            )
        )  # fmt: skip
    n_rows = rows.nunique()
    fig.update_layout(barmode="overlay", hovermode="closest", bargap=0.45)
    _date_axis(fig)
    fig.update_xaxes(range=[lo, hi], showspikes=False)
    fig.update_yaxes(type="category", showgrid=False, autorange="reversed", ticks="")
    return _base_figure(fig, title=title, height=110 + 44 * n_rows + (24 if title else 0))


# ---------------------------------------------------------------------------
# Taupunkt-Abstand
# ---------------------------------------------------------------------------
def dew_point_spread_chart(
    df: pd.DataFrame,
    tz: str = DEFAULT_TZ,
    *,
    warn_spread: float = 3.0,
    surface_offset: float | None = None,
    title: str | None = None,
    gap_threshold_s: float | None = None,
    max_points: int = DEFAULT_MAX_POINTS,
) -> go.Figure:
    """Oben Temperatur und Taupunkt (°C), unten der Abstand Temperatur − Taupunkt (K) als
    Fläche mit Warnschwelle; Zeiten unterhalb der Schwelle sind farblich hervorgehoben.

    Der Taupunkt stammt aus ``dew_point`` (gemessen/importiert) oder – falls dort leer – aus
    ``calc_dew_point``. ``surface_offset`` (Annahme, K): zusätzliche Linie für den Abstand an
    einer um diesen Betrag kälteren Oberfläche. Das ist eine Modellannahme, keine Messung.
    """
    if df is None or df.empty or TEMPERATURE not in df.columns:
        return empty_figure(title=title)
    data = df[[TS, TEMPERATURE]].copy()
    dp = df[DEW_POINT] if DEW_POINT in df.columns else pd.Series(np.nan, index=df.index)
    if "calc_dew_point" in df.columns:
        dp = dp.fillna(df["calc_dew_point"])
    data[DEW_POINT] = dp.astype(float)
    data["spread"] = data[TEMPERATURE] - data[DEW_POINT]
    if data["spread"].isna().all():
        return empty_figure("Kein Taupunkt verfügbar.", title=title)
    data[TS] = pd.to_datetime(data[TS], utc=True)
    data = data.sort_values(TS).reset_index(drop=True)
    threshold = gap_threshold_s or estimate_gap_threshold(data[TS])
    gaps = find_gaps(data[TS], threshold)
    shown = insert_gap_breaks(
        downsample_for_display(data, max_points, metrics=[TEMPERATURE, DEW_POINT, "spread"]), gaps
    )
    x = _to_local_naive(shown[TS], tz)
    spread = shown["spread"].to_numpy(dtype=float)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07, row_heights=[0.55, 0.45])
    for m in (TEMPERATURE, DEW_POINT):
        fig.add_trace(
            go.Scatter(
                x=x, y=shown[m], mode="lines", name=metric_label(m), connectgaps=False,
                line={"color": theme.METRIC_COLORS[m], "width": 1.6, "dash": theme.METRIC_DASH[m]},
                hovertemplate=f"{_hover_value(m)}<extra>{metric_label(m)}</extra>",
            ),
            row=1, col=1,
        )  # fmt: skip
    sc = theme.METRIC_COLORS["dew_point_spread"]
    fig.add_trace(
        go.Scatter(
            x=x, y=spread, mode="lines", name="Abstand Temperatur – Taupunkt", connectgaps=False, fill="tozeroy",
            fillcolor=theme.hex_to_rgba(sc, 0.12), line={"color": sc, "width": 1.4},
            hovertemplate=f"{_hover_value('dew_point_spread')}<extra>Abstand</extra>",
        ),
        row=2, col=1,
    )  # fmt: skip
    below = np.where(spread <= warn_spread, spread, np.nan)
    if np.isfinite(below).any():
        fig.add_trace(
            go.Scatter(
                x=x, y=below, mode="lines", name=f"unter Warnschwelle ({fmt_number(warn_spread, 1)}{NARROW_NBSP}K)",
                connectgaps=False, line={"color": theme.STATUS["hinweis"]["solid"], "width": 3}, hoverinfo="skip",
            ),
            row=2, col=1,
        )  # fmt: skip
    extra = [warn_spread, 0.0]
    if surface_offset is not None:
        surf = spread - float(surface_offset)
        extra.append(float(np.nanmin(surf)) if np.isfinite(surf).any() else 0.0)
        fig.add_trace(
            go.Scatter(
                x=x, y=surf, mode="lines", connectgaps=False,
                name=f"an kalter Oberfläche (Annahme: {fmt_number(-abs(surface_offset), 1)}{NARROW_NBSP}K)",
                line={"color": theme.NEUTRAL[500], "width": 1.2, "dash": "dot"},
                hovertemplate=f"{_hover_value('dew_point_spread')}<extra>Oberfläche (Annahme)</extra>",
            ),
            row=2, col=1,
        )  # fmt: skip
    fig.add_hline(
        y=warn_spread, row=2, col=1, line={"color": theme.STATUS["hinweis"]["solid"], "width": 1, "dash": "dash"},
        annotation_text=f"Warnschwelle {fmt_number(warn_spread, 1)}{NARROW_NBSP}K", annotation_position="top right",
        annotation_font={"size": 10, "color": theme.STATUS["hinweis"]["fg"]},
    )  # fmt: skip
    temps = np.concatenate([shown[TEMPERATURE].to_numpy(dtype=float), shown[DEW_POINT].to_numpy(dtype=float)])
    fig.update_yaxes(
        title_text=axis_title([TEMPERATURE, DEW_POINT]), range=_y_range(temps, TEMPERATURE), row=1, col=1
    )
    srange = _y_range(np.r_[spread, extra], "dew_point_spread")
    if srange:
        srange[0] = min(srange[0], 0.0)
    fig.update_yaxes(title_text="Abstand (K)", range=srange, row=2, col=1)
    _date_axis(fig)
    return _base_figure(fig, title=title, height=520 + (24 if title else 0))


# ---------------------------------------------------------------------------
# Adapter: Analyse-Ergebnisse → Eingaben der Diagrammfabriken
# ---------------------------------------------------------------------------
# Die Fabriken oben nehmen bewusst einfache DataFrames/Dicts. Die Adapter unten übersetzen die
# Ergebnisobjekte aus ``climate_dashboard.analysis`` (oder deren ``to_dict()``-Form aus einem
# Berichts-Snapshot) in diese Formate. Sie erfinden keine Werte: Fehlendes bleibt NaN.
def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _num(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return f


def _kpi(summary_holder: Any, metric: str) -> tuple[float, float, float]:
    kpis = _get(summary_holder, "kpis") or {}
    s = kpis.get(metric) if isinstance(kpis, Mapping) else None
    return _num(_get(s, "mean")), _num(_get(s, "min")), _num(_get(s, "max"))


def monthly_frame(months: Sequence[Any], metric: str = HUMIDITY) -> pd.DataFrame:
    """``analysis.periods.monthly_overview`` → Eingabe für ``monthly_chart``.

    Spalten: ``month``, ``mean``, ``min``, ``max``, ``complete``, ``coverage_pct``.
    """
    rows = []
    for m in months or []:
        mean, lo, hi = _kpi(m, metric)
        rows.append({
            "month": str(_get(m, "month")),
            "mean": mean, "min": lo, "max": hi,
            "complete": bool(_get(m, "complete", False)),
            "coverage_pct": _num(_get(m, "time_coverage_pct")),
        })  # fmt: skip
    return pd.DataFrame(rows, columns=["month", "mean", "min", "max", "complete", "coverage_pct"])


def seasonal_frame(seasons: Sequence[Any], metric: str = HUMIDITY) -> pd.DataFrame:
    """``analysis.periods.seasonal_overview`` → Eingabe für ``seasonal_chart``.

    ``year`` folgt der Konvention von ``formatting.season_label`` (Winter: Jahr, in dem er
    endet). ``complete`` = ausreichende Abdeckung UND ganze Jahreszeit im Datenzeitraum.
    """
    rows = []
    for s in seasons or []:
        mean, lo, hi = _kpi(s, metric)
        season = str(_get(s, "season"))
        start = _get(s, "start")
        try:
            start_year = int(str(_get(s, "key", "")).rsplit("-", 1)[-1])
        except ValueError:
            start_year = pd.Timestamp(start).year if start is not None else None
        year = (start_year + 1) if (season == "winter" and start_year is not None) else start_year
        rows.append({
            "season": season, "year": year, "mean": mean, "min": lo, "max": hi,
            "complete": bool(_get(s, "sufficient", False)) and not bool(_get(s, "partial", False)),
            "coverage_pct": _num(_get(s, "time_coverage_pct")),
        })  # fmt: skip
    return pd.DataFrame(rows, columns=["season", "year", "mean", "min", "max", "complete", "coverage_pct"])


def day_night_frame(result: Any, metric: str = HUMIDITY, *, group: str = "Gesamt") -> pd.DataFrame:
    """``analysis.periods.day_night`` (``DayNightResult``) → Eingabe für ``day_night_chart``."""
    rows = []
    for phase in ("day", "night"):
        part = _get(result, phase)
        means = _get(part, "means") or {}
        rows.append({"group": group, "phase": phase, "mean": _num(means.get(metric))})
    return pd.DataFrame(rows, columns=["group", "phase", "mean"])


_DAY_NIGHT_MONTH_FIELDS = {
    TEMPERATURE: ("day_mean_temperature", "night_mean_temperature"),
    HUMIDITY: ("day_mean_humidity", "night_mean_humidity"),
}


def day_night_monthly_frame(months: Sequence[Any], metric: str = HUMIDITY) -> pd.DataFrame:
    """Tag/Nacht-Mittel je Monat aus ``monthly_overview`` (nur Temperatur und rF verfügbar)."""
    fields = _DAY_NIGHT_MONTH_FIELDS.get(metric)
    rows = []
    for m in months or []:
        if fields is None:
            break
        rows.append({"group": str(_get(m, "month")), "phase": "day", "mean": _num(_get(m, fields[0]))})
        rows.append({"group": str(_get(m, "month")), "phase": "night", "mean": _num(_get(m, fields[1]))})
    return pd.DataFrame(rows, columns=["group", "phase", "mean"])


def band_shares_input(shares: Any) -> tuple[dict[str, float], dict[str, str]]:
    """``analysis.humidity.band_shares`` (``BandShares``) → ``(anteile, beschriftungen)`` für
    ``band_share_bar`` (Anteile als Bruch 0…1 der *abgedeckten* Zeit)."""
    values: dict[str, float] = {}
    labels: dict[str, str] = {}
    for band in _get(shares, "bands") or []:
        key = str(_get(band, "key"))
        pct = _get(band, "pct")
        values[key] = (_num(pct) / 100.0) if pct is not None else 0.0
        labels[key] = str(_get(band, "label") or key)
    return values, labels


def _episode_level(ep: Any, high: float | None) -> str:
    max_rh = _get(ep, "max_rh")
    if high is not None and max_rh is not None and _num(max_rh) >= high:
        return "auffaellig"
    return "hinweis"


def episodes_frame(
    episodes: Sequence[Any], *, label: str | None = None, high_threshold: float | None = None
) -> pd.DataFrame:
    """``analysis.humidity.find_episodes`` → Eingabe für ``episode_timeline``.

    Zeile (``label``) = „anhaltend“/„kurz“ (bzw. vorgegebener Text); ``level`` = auffällig,
    wenn der Höchstwert ≥ ``high_threshold`` liegt, sonst Hinweis.
    """
    rows = []
    for ep in episodes or []:
        sustained = bool(_get(ep, "is_sustained", False))
        rows.append({
            "start": _get(ep, "start"), "end": _get(ep, "end"),
            "label": label or ("anhaltende Phasen" if sustained else "kurze Ausschläge"),
            "level": _episode_level(ep, high_threshold),
            "max": _get(ep, "max_rh"),
        })  # fmt: skip
    return pd.DataFrame(rows, columns=["start", "end", "label", "level", "max"])


def episode_bands(
    episodes: Sequence[Any], *, high_threshold: float | None = None, sustained_only: bool = True,
    max_bands: int = 60,
) -> list[dict[str, Any]]:
    """Feuchtephasen als ``bands`` für ``timeseries_chart`` (längste zuerst, höchstens
    ``max_bands``, damit das Diagramm lesbar bleibt). Ohne Beschriftung – die Farbe genügt."""
    eps = [e for e in episodes or [] if not sustained_only or _get(e, "is_sustained", False)]
    eps = sorted(eps, key=lambda e: -_num(_get(e, "duration_s", 0)))[:max_bands]
    return [
        {"start": _get(e, "start"), "end": _get(e, "end"), "level": _episode_level(e, high_threshold)}
        for e in eps
    ]


def aggregate_frame(agg: pd.DataFrame, metrics: Sequence[str] = METRICS) -> pd.DataFrame:
    """``analysis.periods.aggregate`` → kanonischer Zeitreihen-DataFrame (``ts`` = Fensterbeginn,
    Werte = zeitgewichtete Mittel). Leere Fenster bleiben NaN (Linie reißt ab)."""
    if agg is None or len(agg) == 0:
        return pd.DataFrame({TS: pd.Series(pd.DatetimeIndex([], tz="UTC")), **{m: [] for m in metrics}})
    out = pd.DataFrame({TS: pd.to_datetime(agg["start"], utc=True)})
    for m in metrics:
        col = f"{m}_mean"
        out[m] = agg[col].astype(float).to_numpy() if col in agg.columns else np.nan
    return out

"""PDF-Export eines Berichts-Snapshots (fpdf2 + matplotlib, ohne Browser/kaleido).

* Rendert ausschließlich aus dem Snapshot (keine Rohdaten).
* Schrift: DejaVu Sans aus dem matplotlib-Paket (frei lizenziert, Unicode inkl. Umlaute,
  ≥, −, °) – es werden keine Systemschriften vorausgesetzt. Läuft so auch auf Streamlit
  Community Cloud.
* Diagramme: matplotlib über ``Figure``/``FigureCanvasAgg`` (kein pyplot-Zustand, threadsicher).
* Seitenumbrüche: Tabellen und Diagramme werden vorab vermessen und – sofern sie auf eine Seite
  passen – nicht zerschnitten; Überschriften bleiben mit dem folgenden Inhalt zusammen.
  Sehr lange Tabellen werden mit wiederholter Kopfzeile fortgesetzt.
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from fpdf import FPDF
from fpdf.fonts import FontFace

from climate_dashboard.reports.builder import SECTIONS, series_times
from climate_dashboard.ui.formatting import (
    MISSING,
    fmt_date_range,
    fmt_datetime,
    fmt_duration,
    fmt_month,
    fmt_number,
    fmt_percent,
)

# --- Druckfreundliche Farben (RGB) ------------------------------------------------------------
TEXT = (15, 23, 42)
MUTED = (71, 85, 105)
SUBTLE = (100, 116, 139)
BORDER = (203, 213, 225)
FILL_HEAD = (241, 245, 249)
FILL_ROW = (248, 250, 252)
PRIMARY = (37, 99, 235)
LEVEL_COLORS = {  # (Text, Fläche, Akzent)
    "ok": ((39, 103, 73), (238, 247, 241), (63, 143, 100)),
    "hinweis": ((146, 86, 11), (254, 247, 234), (217, 154, 43)),
    "auffaellig": ((162, 59, 50), (252, 240, 239), (194, 85, 75)),
    "info": ((29, 78, 216), (239, 244, 255), (59, 111, 224)),
    "neutral": ((71, 85, 105), (241, 245, 249), (148, 163, 184)),
}
LEVEL_LABELS = {"ok": "Unauffällig", "hinweis": "Hinweis", "auffaellig": "Auffällig", "info": "Info",
                "neutral": "Hinweis"}
METRIC_COLORS = {"temperature": "#D55E00", "humidity": "#0072B2", "dew_point": "#009E73"}

FONT = "DejaVu"
FOOTER_NOTE = ("Schreibgeschützter Klimabericht auf Grundlage von Raumluftmessungen – "
               "keine Begutachtung vor Ort.")


def _font_dir() -> Path:
    import matplotlib

    return Path(matplotlib.get_data_path()) / "fonts" / "ttf"


def _clean(text: Any) -> str:
    """Text für das PDF: schmale geschützte Leerzeichen → geschütztes Leerzeichen."""
    if text is None:
        return MISSING
    return str(text).replace(" ", " ").replace(" ", " ")


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, int | float):
        return None
    return float(x) if math.isfinite(float(x)) else None


def _g(d: Any, *path: str, default: Any = None) -> Any:
    cur = d
    for p in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(p)
    return default if cur is None else cur


def _unit(value: Any, decimals: int, unit: str) -> str:
    return _clean(f"{fmt_number(value, decimals)} {unit}") if _num(value) is not None else MISSING


# ---------------------------------------------------------------------------
# PDF-Klasse
# ---------------------------------------------------------------------------
class _ReportPDF(FPDF):
    def __init__(self, title: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._doc_title = title
        fd = _font_dir()
        self.add_font(FONT, "", str(fd / "DejaVuSans.ttf"))
        self.add_font(FONT, "B", str(fd / "DejaVuSans-Bold.ttf"))
        self.add_font(FONT, "I", str(fd / "DejaVuSans-Oblique.ttf"))
        self.set_margins(18, 16, 18)
        self.set_auto_page_break(True, margin=20)
        self.set_title(_clean(title))
        self.set_creator("SwitchBot Climate Dashboard")
        self.set_lang("de-DE")
        self.set_text_color(*TEXT)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_draw_color(*BORDER)
        self.line(self.l_margin, self.get_y() - 1.5, self.w - self.r_margin, self.get_y() - 1.5)
        self.set_font(FONT, "", 7.5)
        self.set_text_color(*SUBTLE)
        self.cell(self.epw * 0.75, 4, _clean(FOOTER_NOTE), align="L")
        self.cell(self.epw * 0.25, 4, f"Seite {self.page_no()} von {{nb}}", align="R")
        self.set_text_color(*TEXT)

    # --- Platz / Umbrüche -----------------------------------------------------------------
    @property
    def remaining(self) -> float:
        return self.page_break_trigger - self.get_y()

    def ensure_space(self, height: float) -> None:
        if height > self.remaining and self.get_y() > self.t_margin + 1:
            self.add_page()

    # --- Textbausteine --------------------------------------------------------------------
    def h1(self, text: str) -> None:
        self.ensure_space(40)
        self.ln(4)
        self.set_font(FONT, "B", 13.5)
        self.set_text_color(*TEXT)
        self.multi_cell(0, 7, _clean(text), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(*BORDER)
        self.line(self.l_margin, self.get_y() + 0.5, self.w - self.r_margin, self.get_y() + 0.5)
        self.ln(3)

    def h2(self, text: str) -> None:
        self.ensure_space(30)
        self.ln(1.5)
        self.set_font(FONT, "B", 10.5)
        self.multi_cell(0, 5.5, _clean(text), new_x="LMARGIN", new_y="NEXT")
        self.ln(0.8)

    def para(self, text: str, *, size: float = 9.5, style: str = "", color=TEXT, lh: float = 4.8) -> None:
        self.set_font(FONT, style, size)
        self.set_text_color(*color)
        self.multi_cell(0, lh, _clean(text), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*TEXT)
        self.ln(1.2)

    def bullet(self, text: str, *, size: float = 9.2, color=TEXT) -> None:
        self.set_font(FONT, "", size)
        self.set_text_color(*color)
        x = self.l_margin
        self.set_x(x)
        self.cell(4, 4.6, "•")
        self.multi_cell(self.epw - 4, 4.6, _clean(text), new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(*TEXT)
        self.ln(0.6)

    def box(self, level: str, title: str, body: str | Sequence[str], *, label: str | None = None) -> None:
        fg, bg, accent = LEVEL_COLORS.get(level, LEVEL_COLORS["neutral"])
        lines = [body] if isinstance(body, str) else list(body)

        def draw() -> None:
            y0 = self.get_y()
            pad = 3.5
            w = self.epw
            # Höhe vermessen
            self.set_font(FONT, "B", 10.5)
            h = pad
            h += len(self.multi_cell(w - 2 * pad - 2, 5.4, _clean(title), dry_run=True, output="LINES")) * 5.4
            self.set_font(FONT, "", 9.3)
            for ln in lines:
                h += len(self.multi_cell(w - 2 * pad - 2, 4.7, _clean(ln), dry_run=True, output="LINES")) * 4.7 + 1
            if label:
                h += 5
            h += pad
            if y0 + h > self.page_break_trigger and y0 > self.t_margin + 1:
                self.add_page()
                y0 = self.get_y()
            self.set_fill_color(*bg)
            self.set_draw_color(*bg)
            self.rect(self.l_margin, y0, w, h, style="F")
            self.set_fill_color(*accent)
            self.rect(self.l_margin, y0, 1.6, h, style="F")
            self.set_xy(self.l_margin + pad + 2, y0 + pad)
            if label:
                self.set_font(FONT, "B", 7.5)
                self.set_text_color(*fg)
                self.cell(0, 4, _clean(label.upper()), new_x="LMARGIN", new_y="NEXT")
                self.set_x(self.l_margin + pad + 2)
                self.ln(1)
                self.set_x(self.l_margin + pad + 2)
            self.set_font(FONT, "B", 10.5)
            self.set_text_color(*fg)
            self.multi_cell(w - 2 * pad - 2, 5.4, _clean(title), new_x="LEFT", new_y="NEXT")
            self.set_text_color(*TEXT)
            self.set_font(FONT, "", 9.3)
            for ln in lines:
                self.multi_cell(w - 2 * pad - 2, 4.7, _clean(ln), new_x="LEFT", new_y="NEXT")
                self.ln(1)
            self.set_y(y0 + h)
            self.ln(3)

        draw()

    def kv(self, rows: Sequence[tuple[str, str]], *, label_w: float = 45) -> None:
        for k, v in rows:
            self.set_font(FONT, "", 9)
            self.set_text_color(*MUTED)
            y = self.get_y()
            self.multi_cell(label_w, 4.8, _clean(k), new_x="RIGHT", new_y="TOP")
            self.set_text_color(*TEXT)
            self.set_xy(self.l_margin + label_w, y)
            self.multi_cell(self.epw - label_w, 4.8, _clean(v), new_x="LMARGIN", new_y="NEXT")
            self.set_y(max(self.get_y(), y + 4.8))
        self.ln(1.5)

    def data_table(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        widths: Sequence[float],
        *,
        align: Sequence[str] | None = None,
        size: float = 8.3,
        row_fills: Sequence[tuple[int, int, int] | None] | None = None,
    ) -> None:
        """Tabelle mit relativen Spaltenbreiten (Summe = Seitenbreite). Passt sie auf eine Seite,
        wird sie nicht zerschnitten; sonst Fortsetzung mit wiederholter Kopfzeile."""
        if not rows:
            return
        align = list(align or ["LEFT"] * len(headers))

        def draw(pdf: FPDF) -> None:
            pdf.set_font(FONT, "", size)
            pdf.set_draw_color(*BORDER)
            # Ohne explizite Füllfarbe übernähmen Zeilen die zuletzt gesetzte (z. B. Kurzfazit-Akzent).
            pdf.set_fill_color(255, 255, 255)
            head = FontFace(emphasis="BOLD", fill_color=FILL_HEAD, color=TEXT)
            with pdf.table(
                col_widths=tuple(widths), text_align=tuple(align), line_height=size * 0.5,
                headings_style=head, borders_layout="HORIZONTAL_LINES", padding=(1.2, 1.4),
                first_row_as_headings=True, width=pdf.epw, repeat_headings=1,
            ) as table:
                hr = table.row()
                for h in headers:
                    hr.cell(_clean(h))
                for i, r in enumerate(rows):
                    fill = row_fills[i] if row_fills and i < len(row_fills) else None
                    style = FontFace(fill_color=fill or (255, 255, 255))
                    row = table.row(style=style)
                    for c in r:
                        row.cell(_clean(c))
            pdf.ln(2.5)

        with self.offset_rendering() as dummy:
            draw(dummy)
        fits_on_empty = True
        if dummy.page_break_triggered:
            # Höhe grob: Zeilen × Zeilenhöhe ≤ Seitenhöhe?
            approx = (len(rows) + 1) * (size * 0.5 + 2.4)
            fits_on_empty = approx < (self.page_break_trigger - self.t_margin)
            if fits_on_empty and self.get_y() > self.t_margin + 1:
                self.add_page()
            elif not fits_on_empty:
                self.ensure_space(30)
        draw(self)

    def figure(self, png: bytes, height_mm: float, caption: str | None = None) -> None:
        need = height_mm + (6 if caption else 2)
        self.ensure_space(need)
        self.image(io.BytesIO(png), x=self.l_margin, w=self.epw, h=height_mm)
        if caption:
            self.set_font(FONT, "I", 7.8)
            self.set_text_color(*SUBTLE)
            self.multi_cell(0, 3.8, _clean(caption), new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(*TEXT)
        self.ln(3)


# ---------------------------------------------------------------------------
# Diagramme (matplotlib, ohne pyplot)
# ---------------------------------------------------------------------------
def _mpl_fig(width_mm: float, height_mm: float, rows: int = 1):
    from matplotlib.figure import Figure

    fig = Figure(figsize=(width_mm / 25.4, height_mm / 25.4), dpi=200)
    axes = fig.subplots(rows, 1, sharex=True, squeeze=False)[:, 0]
    for ax in axes:
        ax.set_facecolor("white")
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color("#CBD5E1")
        ax.tick_params(labelsize=7, colors="#475569", length=2.5)
        ax.grid(True, axis="y", color="#E2E8F0", linewidth=0.6)
        ax.set_axisbelow(True)
    return fig, axes


def _de_formatter(decimals: int = 0):
    from matplotlib.ticker import FuncFormatter

    return FuncFormatter(lambda v, _pos: fmt_number(v, decimals))


def _date_axis(ax, x: pd.Series) -> None:
    import matplotlib.dates as mdates
    from matplotlib.ticker import FuncFormatter

    if len(x) == 0:
        return
    span_days = (x.max() - x.min()).total_seconds() / 86400.0
    if span_days <= 3:
        locator = mdates.HourLocator(byhour=range(0, 24, 6))
        fmt = lambda d: f"{d:%H:%M}\n{d:%d.%m.}"  # noqa: E731
    elif span_days <= 75:
        locator = mdates.AutoDateLocator(minticks=4, maxticks=9)
        fmt = lambda d: f"{d:%d.%m.}"  # noqa: E731
    else:
        locator = mdates.MonthLocator(interval=max(1, int(span_days // 300) + 1))
        fmt = lambda d: fmt_month(d, short=True)  # noqa: E731
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: fmt(mdates.num2date(v))))


def _png(fig) -> bytes:
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    FigureCanvasAgg(fig)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=200, facecolor="white")
    return buf.getvalue()


def _series_frame(series: dict[str, Any] | None, tz: str) -> pd.DataFrame | None:
    if not series or not series.get("n"):
        return None
    times = series_times(series).tz_convert(tz).tz_localize(None)
    df = pd.DataFrame({"t": times})
    for k in ("temperature", "humidity", "humidity_min", "humidity_max", "dew_point"):
        vals = series.get(k) or []
        arr = np.array([np.nan if v is None else float(v) for v in vals], dtype=float)
        if len(arr) != len(df):
            arr = np.resize(arr, len(df)) if len(arr) else np.full(len(df), np.nan)
        df[k] = arr
    return df


def _timeseries_png(df: pd.DataFrame, kind: str, thresholds: dict[str, Any], width_mm: float) -> bytes:
    fig, (ax,) = _mpl_fig(width_mm, 62)
    x = df["t"]
    if kind == "temperature":
        ax.plot(x, df["temperature"], color=METRIC_COLORS["temperature"], linewidth=0.9, label="Temperatur")
        ax.set_ylabel("°C", fontsize=7.5, color="#475569")
        ax.yaxis.set_major_formatter(_de_formatter(0))
    elif kind == "humidity":
        lo, hi = df["humidity_min"], df["humidity_max"]
        ax.fill_between(x, lo, hi, color=METRIC_COLORS["humidity"], alpha=0.14, linewidth=0,
                        label="Spanne Min–Max")
        ax.plot(x, df["humidity"], color=METRIC_COLORS["humidity"], linewidth=0.9, label="rel. Luftfeuchtigkeit")
        for key, label, style in (("humidity_elevated", "erhöht ab", (0, (3, 2))),
                                  ("humidity_high", "hoch ab", (0, (1, 1.5)))):
            v = _num(thresholds.get(key))
            if v is not None:
                ax.axhline(v, color="#94A3B8", linewidth=0.8, linestyle=style)
                ax.annotate(f"{label} {fmt_number(v)} %", xy=(1, v), xycoords=("axes fraction", "data"),
                            xytext=(-2, 2), textcoords="offset points", ha="right", va="bottom",
                            fontsize=6.5, color="#64748B")
        ax.set_ylabel("% rF", fontsize=7.5, color="#475569")
        ax.yaxis.set_major_formatter(_de_formatter(0))
        ymin = np.nanmin(lo.to_numpy()) if np.isfinite(lo.to_numpy()).any() else 30
        ymax = np.nanmax(hi.to_numpy()) if np.isfinite(hi.to_numpy()).any() else 70
        ax.set_ylim(max(0, min(ymin, 40) - 5), min(100, max(ymax, (_num(thresholds.get("humidity_high")) or 70)) + 5))
    else:
        ax.plot(x, df["temperature"], color=METRIC_COLORS["temperature"], linewidth=0.9, label="Raumtemperatur")
        ax.plot(x, df["dew_point"], color=METRIC_COLORS["dew_point"], linewidth=0.9, linestyle=(0, (4, 2)),
                label="Taupunkt")
        ax.set_ylabel("°C", fontsize=7.5, color="#475569")
        ax.yaxis.set_major_formatter(_de_formatter(0))
    ax.legend(fontsize=6.5, frameon=False, loc="upper left", ncol=3)
    _date_axis(ax, x)
    fig.tight_layout(pad=0.4)
    return _png(fig)


def _monthly_png(months: list[dict[str, Any]], width_mm: float) -> bytes:
    fig, (ax,) = _mpl_fig(width_mm, 58)
    labels = [fmt_month(m.get("month"), short=True) for m in months]
    means = np.array([_num(_g(m, "kpis", "humidity", "mean")) or np.nan for m in months], dtype=float)
    mins = np.array([_num(_g(m, "kpis", "humidity", "min")) or np.nan for m in months], dtype=float)
    maxs = np.array([_num(_g(m, "kpis", "humidity", "max")) or np.nan for m in months], dtype=float)
    complete = [bool(m.get("complete")) for m in months]
    xs = np.arange(len(months))
    color = METRIC_COLORS["humidity"]
    for i in range(len(months)):
        if not np.isfinite(means[i]):
            continue
        ax.bar(xs[i], means[i], width=0.6, color=color if complete[i] else "white",
               alpha=0.75 if complete[i] else 1.0, edgecolor=color, linewidth=0.8,
               hatch=None if complete[i] else "////")
        if np.isfinite(mins[i]) and np.isfinite(maxs[i]):
            ax.plot([xs[i], xs[i]], [mins[i], maxs[i]], color="#334155", linewidth=0.8)
    ax.set_xticks(xs, labels, fontsize=6.5, rotation=0 if len(months) <= 12 else 45)
    ax.set_ylabel("% rF", fontsize=7.5, color="#475569")
    ax.yaxis.set_major_formatter(_de_formatter(0))
    ax.set_ylim(0, 100)
    from matplotlib.patches import Patch

    ax.legend(handles=[Patch(facecolor=color, alpha=0.75, label="Monatsmittel (vollständig)"),
                       Patch(facecolor="white", edgecolor=color, hatch="////", label="unvollständiger Monat")],
              fontsize=6.5, frameon=False, loc="upper left", ncol=2)
    fig.tight_layout(pad=0.4)
    return _png(fig)


# ---------------------------------------------------------------------------
# Abschnitte
# ---------------------------------------------------------------------------
def _included(snapshot: dict[str, Any], section: str) -> bool:
    sections = _g(snapshot, "options", "include_sections")
    return section in sections if isinstance(sections, list) else True


def _title_block(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    meta = snap.get("meta") or {}
    tz = meta.get("timezone") or "Europe/Berlin"
    pdf.set_font(FONT, "B", 8)
    pdf.set_text_color(*PRIMARY)
    pdf.cell(0, 5, "KLIMABERICHT", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(*TEXT)
    pdf.set_font(FONT, "B", 19)
    pdf.multi_cell(0, 8.5, _clean(meta.get("title") or "Klimabericht"), new_x="LMARGIN", new_y="NEXT")
    if meta.get("room_name"):
        pdf.set_font(FONT, "", 12.5)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(0, 6.5, _clean(f"Raum: {meta['room_name']}"), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*TEXT)
    pdf.ln(2.5)
    q = snap.get("quality") or {}
    rows = [
        ("Zeitraum", fmt_date_range(meta.get("period_start"), meta.get("period_end"), tz, end_exclusive=True)),
        ("Erstellt am", fmt_datetime(meta.get("created_at"), tz) if meta.get("created_at") else MISSING),
    ]
    if meta.get("version") is not None:
        rows.append(("Version", str(meta.get("version"))))
    if _num(q.get("time_coverage_pct")) is not None:
        rows.append(("Datenabdeckung", f"{fmt_percent(q.get('time_coverage_pct'))} des Zeitraums "
                                       f"({q.get('rating') or MISSING})"))
    sensors = meta.get("sensor_info")
    if sensors:
        rows.append(("Sensor(en)", "; ".join(
            s.get("name", "") + (f" ({s['device_info']})" if s.get("device_info") else "") for s in sensors)))
    pdf.kv(rows, label_w=36)
    pdf.set_draw_color(*BORDER)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(4)
    if meta.get("note"):
        pdf.box("info", "Anmerkung des Erstellers", meta["note"])


def _kpi_section(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    kpis = snap.get("kpis") or {}
    if not kpis or not _included(snap, "kpis"):
        return
    tz = _g(snap, "meta", "timezone", default="Europe/Berlin")
    pdf.h1("Kennzahlen")
    spec = [("temperature", "Temperatur", "°C", 1), ("humidity", "Relative Luftfeuchtigkeit", "%", 0),
            ("dew_point", "Taupunkt", "°C", 1), ("dew_point_spread", "Abstand Raumluft – Taupunkt", "K", 1),
            ("abs_humidity", "Absolute Luftfeuchtigkeit", "g/m³", 1)]
    rows = []
    for key, label, unit, d in spec:
        k = kpis.get(key) or {}
        if _num(k.get("mean")) is None:
            continue
        src = {"measured": "gemessen", "calculated": "berechnet", "mixed": "teils berechnet"}.get(
            k.get("source"), "–")
        rows.append([
            label, _unit(k.get("mean"), d, unit),
            _unit(k.get("min"), d, unit) + (f"\n{fmt_datetime(k.get('min_time'), tz)}" if k.get("min_time") else ""),
            _unit(k.get("max"), d, unit) + (f"\n{fmt_datetime(k.get('max_time'), tz)}" if k.get("max_time") else ""),
            src,
        ])
    if rows:
        pdf.data_table(["Messgröße", "Mittelwert", "Niedrigster Wert", "Höchster Wert", "Herkunft"], rows,
                       (30, 16, 22, 22, 14), align=("LEFT", "RIGHT", "RIGHT", "RIGHT", "LEFT"))
        pdf.para("Mittelwerte sind zeitgewichtet: Jede Messung zählt für die Zeit bis zur nächsten Messung. "
                 "„Berechnet“ heißt: aus Temperatur und Luftfeuchtigkeit ermittelt.", size=7.8, color=SUBTLE,
                 lh=3.9)


def _timeseries_section(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    series = snap.get("series")
    if not series or not _included(snap, "timeseries"):
        return
    tz = _g(snap, "meta", "timezone", default="Europe/Berlin")
    df = _series_frame(series, tz)
    if df is None:
        return
    th = _g(snap, "meta", "thresholds", default={})
    pdf.h1("Zeitverlauf")
    pdf.para(f"Die Diagramme zeigen {series.get('label') or 'stündliche'} Mittelwerte. Unterbrochene Linien "
             "bedeuten Datenlücken – dort wurden keine Werte ergänzt.", size=8.5, color=MUTED, lh=4.3)
    if series.get("note"):
        pdf.para(series["note"], size=8, color=SUBTLE, lh=4)
    pdf.h2("Temperatur")
    pdf.figure(_timeseries_png(df, "temperature", th, pdf.epw), 62)
    pdf.h2("Relative Luftfeuchtigkeit")
    pdf.figure(_timeseries_png(df, "humidity", th, pdf.epw), 62,
               caption="Linie: Mittelwert · Fläche: niedrigster bis höchster Wert im Zeitfenster · gestrichelt: "
                       "Orientierungswerte „erhöht“ und „hoch“.")
    pdf.h2("Taupunkt")
    pdf.figure(_timeseries_png(df, "dew_point", th, pdf.epw), 62,
               caption="Je näher die gestrichelte Taupunkt-Linie an der Raumtemperatur liegt, desto weniger "
                       "Abkühlung einer Oberfläche genügt, damit dort rechnerisch Tauwasser entstehen kann.")


def _humidity_section(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    hum = snap.get("humidity")
    if not hum or not _included(snap, "humidity"):
        return
    tz = _g(snap, "meta", "timezone", default="Europe/Berlin")
    pdf.h1("Luftfeuchtigkeit und Feuchtephasen")
    bands = _g(hum, "bands", "bands", default=[])
    rows = [[b.get("label", ""), _clean(f"{fmt_number(b.get('hours'), 0)} Std."), fmt_percent(b.get("pct"), 1)]
            for b in bands]
    if rows:
        pdf.h2("Zeitanteile in Feuchtebereichen")
        pdf.data_table(["Bereich", "Dauer", "Anteil der gemessenen Zeit"], rows, (40, 20, 30),
                       align=("LEFT", "RIGHT", "RIGHT"))
        gap_h = _num(_g(hum, "bands", "gap_hours"))
        if gap_h:
            pdf.para(f"Nicht enthalten: {fmt_number(gap_h, 0)} Std. ohne Messwerte (Datenlücken).", size=8,
                     color=SUBTLE, lh=4)
    es = hum.get("elevated_summary") or {}
    th = _g(snap, "meta", "thresholds", default={})
    el = _num(th.get("humidity_elevated")) or 60
    pdf.h2(_clean(f"Phasen mit {fmt_number(el)} % rF oder mehr"))
    total = int(es.get("count_total") or 0)
    if total == 0:
        pdf.para(f"Im Zeitraum gab es keine Phase mit {fmt_number(el)} % relativer Luftfeuchtigkeit oder mehr.")
        return
    pdf.kv([
        ("Phasen insgesamt", str(total)),
        ("davon anhaltend", _clean(f"{es.get('count_sustained', 0)} (mindestens "
                                   f"{fmt_duration((es.get('min_sustained_minutes') or 120) * 60)})")),
        ("davon kurz", str(es.get("count_short", 0))),
        ("Längste Phase", fmt_duration(es.get("longest_duration_s"))),
        ("Je Woche (anhaltend)", fmt_number(es.get("sustained_per_week"), 1)),
    ], label_w=50)
    eps = hum.get("episodes") or []
    if eps:
        rows = []
        for e in eps:
            note = []
            if e.get("interrupted_by_gap"):
                note.append("endet an Datenlücke")
            if e.get("at_period_start"):
                note.append("begann evtl. früher")
            rows.append([fmt_datetime(e.get("start"), tz), fmt_datetime(e.get("end"), tz),
                         fmt_duration(e.get("duration_s")), fmt_percent(e.get("max_rh")),
                         "anhaltend" if e.get("is_sustained") else "kurz", ", ".join(note) or "–"])
        listed = hum.get("episodes_listed") or len(eps)
        total_eps = hum.get("episodes_total") or len(eps)
        caption = ("Alle Phasen." if listed >= total_eps
                   else f"Die {listed} längsten von {total_eps} Phasen, zeitlich sortiert.")
        pdf.data_table(["Beginn", "Ende", "Dauer", "Höchstwert", "Art", "Hinweis"], rows,
                       (24, 24, 17, 13, 13, 22), align=("LEFT", "LEFT", "RIGHT", "RIGHT", "LEFT", "LEFT"),
                       size=7.8)
        pdf.para(caption, size=7.8, color=SUBTLE, lh=3.9)


def _condensation_section(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    cond = snap.get("condensation")
    if not cond or not _included(snap, "condensation"):
        return
    pdf.h1("Taupunkt und Oberflächen")
    pdf.para("Der Taupunkt ist die Temperatur, ab der Feuchtigkeit aus der Luft an Oberflächen kondensieren "
             "kann. Der Abstand zwischen Raumtemperatur und Taupunkt gibt an, um wie viel Grad eine Oberfläche "
             "kälter als die Raumluft sein müsste, damit dort rechnerisch Tauwasser entsteht.", size=9)
    warn = cond.get("warn_threshold_k")
    pdf.kv([
        ("Mittlerer Abstand", _unit(cond.get("spread_mean"), 1, "K")),
        ("Kleinster Abstand", _unit(cond.get("spread_min"), 1, "K")),
        (_clean(f"Zeit mit Abstand < {fmt_number(warn, 1)} K"),
         _clean(f"{fmt_percent(cond.get('pct_below_warn'), 1)} ({fmt_number(cond.get('hours_below_warn'), 0)} Std.)")),
        ("Zeit mit Abstand < 1 K", _clean(f"{fmt_percent(cond.get('pct_below_1k'), 1)}")),
    ], label_w=50)
    surface = cond.get("surface")
    if surface:
        title = ("Annahme: Oberfläche " + fmt_number(surface.get("offset_k"), 1) + " K kälter als die Raumluft"
                 if surface.get("basis") == "assumed" else "Gemessene Oberflächentemperatur")
        body = [
            f"Rechnerisch am oder unter dem Taupunkt: {fmt_number(surface.get('hours_at_or_below_dew_point'), 1)} "
            f"Std. ({fmt_percent(surface.get('pct_at_or_below_dew_point'), 1)} der Zeit).",
            f"Rechnerische Oberflächenfeuchte ≥ 80 %: {fmt_number(surface.get('hours_surface_rh_critical'), 1)} "
            f"Std.; Tage mit ≥ 12 Std.: {surface.get('days_surface_rh_critical', 0)}.",
        ]
        if surface.get("assumption"):
            body.append(surface["assumption"])
        pdf.box("info", title, body, label="Annahme" if surface.get("basis") == "assumed" else None)
    for lim in cond.get("limitations") or []:
        if surface and lim == surface.get("assumption"):
            continue
        pdf.bullet(lim, size=8.5, color=MUTED)


def _status_text(m: dict[str, Any]) -> str:
    if m.get("insufficient"):
        return "zu wenig Daten"
    return "vollständig" if m.get("complete") else "unvollständig"


def _monthly_section(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    months = snap.get("monthly")
    if not months or not _included(snap, "monthly"):
        return
    th = _g(snap, "meta", "thresholds", default={})
    el = _num(th.get("humidity_elevated")) or 60
    pdf.h1("Monatsübersicht")
    rows, fills = [], []
    for m in months:
        insufficient = m.get("insufficient")
        rows.append([
            m.get("label") or fmt_month(m.get("month")),
            MISSING if insufficient else _unit(_g(m, "kpis", "temperature", "mean"), 1, "°C"),
            MISSING if insufficient else fmt_percent(_g(m, "kpis", "humidity", "mean")),
            MISSING if insufficient else fmt_percent(_g(m, "kpis", "humidity", "max")),
            MISSING if insufficient else fmt_percent(m.get("pct_elevated")),
            MISSING if insufficient else str(m.get("sustained_episodes", 0)),
            fmt_percent(m.get("time_coverage_pct")),
            _status_text(m),
        ])
        fills.append(None if m.get("complete") else (254, 247, 234))
    pdf.data_table(["Monat", "Ø Temp.", "Ø rF", "Max. rF", _clean(f"Zeit ≥ {fmt_number(el)} %"),
                    "Anh. Phasen", "Abdeckung", "Status"], rows,
                   (24, 14, 11, 12, 15, 13, 14, 17),
                   align=("LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT"), size=7.8,
                   row_fills=fills)
    pdf.para("Unvollständige Monate (hinterlegt) liegen nur teilweise im Zeitraum oder haben weniger als 80 % "
             "Zeitabdeckung; ihre Werte sind nur eingeschränkt vergleichbar. Bei „zu wenig Daten“ werden keine "
             "Werte angegeben.", size=7.8, color=SUBTLE, lh=3.9)
    if any(_num(_g(m, "kpis", "humidity", "mean")) is not None for m in months):
        pdf.figure(_monthly_png(months, pdf.epw), 58,
                   caption="Balken: Monatsmittel der relativen Luftfeuchtigkeit · Strich: niedrigster bis höchster "
                           "Wert · schraffiert: unvollständiger Monat.")


def _seasonal_section(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    seasons = snap.get("seasonal")
    if not seasons or not _included(snap, "seasonal"):
        return
    pdf.h1("Jahreszeiten")
    rows, fills = [], []
    for s in seasons:
        rows.append([
            s.get("label", ""),
            _unit(_g(s, "kpis", "temperature", "mean"), 1, "°C"),
            fmt_percent(_g(s, "kpis", "humidity", "mean")),
            fmt_percent(s.get("pct_elevated")),
            str(s.get("sustained_episodes", 0)),
            fmt_percent(s.get("time_coverage_pct")),
            "ausreichend" if s.get("sufficient") else ("Teil der Jahreszeit" if s.get("partial") else "lückenhaft"),
        ])
        fills.append(None if s.get("sufficient") else (254, 247, 234))
    pdf.data_table(["Jahreszeit", "Ø Temp.", "Ø rF", "Zeit erhöht", "Anh. Phasen", "Abdeckung", "Datenbasis"],
                   rows, (24, 14, 11, 14, 13, 14, 20),
                   align=("LEFT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "RIGHT", "LEFT"), size=7.8, row_fills=fills)
    pdf.para("Meteorologische Jahreszeiten (Winter = Dezember bis Februar). Abdeckung bezogen auf die ganze "
             "Jahreszeit; unter 80 % sind die Werte nicht repräsentativ.", size=7.8, color=SUBTLE, lh=3.9)


def _day_night_section(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    dn = snap.get("day_night")
    if not dn or not _included(snap, "day_night"):
        return
    pdf.h1("Tag und Nacht")
    day, night = dn.get("day") or {}, dn.get("night") or {}
    diff = dn.get("night_minus_day") or {}
    rows = [
        ["Ø Temperatur", _unit(_g(day, "means", "temperature"), 1, "°C"),
         _unit(_g(night, "means", "temperature"), 1, "°C"), _signed(diff.get("temperature"), 1, "K")],
        ["Ø rel. Luftfeuchtigkeit", fmt_percent(_g(day, "means", "humidity")),
         fmt_percent(_g(night, "means", "humidity")), _signed(diff.get("humidity"), 0, "Prozentpunkte")],
        ["Ø Taupunkt", _unit(_g(day, "means", "dew_point"), 1, "°C"), _unit(_g(night, "means", "dew_point"), 1, "°C"),
         _signed(diff.get("dew_point"), 1, "K")],
        ["Zeitanteil erhöht", fmt_percent(day.get("pct_elevated")), fmt_percent(night.get("pct_elevated")), "–"],
    ]
    pdf.data_table(["", "Tag", _clean(f"Nacht ({dn.get('night_hours', '')})"), "Unterschied Nacht – Tag"], rows,
                   (30, 18, 22, 24), align=("LEFT", "RIGHT", "RIGHT", "RIGHT"))


def _signed(x: Any, d: int, unit: str) -> str:
    return _clean(f"{fmt_number(x, d, sign=True)} {unit}") if _num(x) is not None else MISSING


def _assessment_section(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    a = snap.get("assessment")
    if not a or not _included(snap, "assessment"):
        return
    pdf.h1("Feuchte- und Risikobewertung")
    pdf.para("Die Einschätzung beruht auf festen, offengelegten Regeln (keine Punktzahl). Sie unterscheidet "
             "Messwerte, daraus berechnete Kennzahlen und mögliche Risiken. Sie ist keine Schadensfeststellung.",
             size=8.5, color=MUTED, lh=4.3)
    overall = a.get("overall") or {}
    level = {"unauffaellig": "ok", "feuchte": "hinweis", "kondensation": "hinweis", "schimmel": "auffaellig"}.get(
        overall.get("key"), "neutral")
    reasons = list(overall.get("reasons") or [])
    pdf.box(level, overall.get("label") or "Einschätzung", reasons or [""], label="Gesamteinschätzung")
    findings = a.get("findings") or []
    if findings:
        pdf.h2("Einzelbefunde")
        for f in findings:
            lvl = f.get("level") or "neutral"
            fg = LEVEL_COLORS.get(lvl, LEVEL_COLORS["neutral"])[0]
            pdf.ensure_space(12)
            pdf.set_font(FONT, "B", 7.5)
            pdf.set_text_color(*fg)
            pdf.cell(0, 4, _clean(LEVEL_LABELS.get(lvl, "Hinweis").upper()), new_x="LMARGIN", new_y="NEXT")
            pdf.para(f.get("text") or "", size=9, lh=4.6)
    hints = a.get("ventilation_hints") or []
    if hints:
        pdf.h2("Hinweise zum Lüften")
        for h in hints:
            pdf.box("neutral", h.get("observation") or "",
                    [h.get("possible_explanation") or "", "Empfehlung: " + (h.get("recommendation") or ""),
                     "Unbekannt: " + (h.get("unknown") or "")])
    lims = a.get("limitations") or []
    if lims:
        pdf.h2("Einschränkungen")
        for lim in lims:
            pdf.bullet(lim, size=8.8, color=MUTED)


def _quality_section(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    q = snap.get("quality")
    if not q or not _included(snap, "data_quality"):
        return
    tz = _g(snap, "meta", "timezone", default="Europe/Berlin")
    pdf.h1("Datenqualität und Lücken")
    rows = [
        ("Einstufung", q.get("rating") or MISSING),
        ("Messungen", fmt_number(q.get("n"))),
        ("Zeitabdeckung", fmt_percent(q.get("time_coverage_pct"), 1)),
        ("Messvollständigkeit", fmt_percent(q.get("completeness_pct"), 1)),
        ("Erwartetes Messintervall", fmt_duration(q.get("expected_interval_s"))),
        ("Datenlücken", f"{q.get('gap_count', 0)} (zusammen {fmt_duration(q.get('gap_total_s'))})"),
    ]
    pdf.kv(rows, label_w=50)
    for r in q.get("rating_reasons") or []:
        pdf.bullet(r, size=8.5, color=MUTED)
    gaps = q.get("gaps") or []
    if gaps:
        pdf.h2("Relevante Lücken")
        rows2 = [[fmt_datetime(g.get("start"), tz), fmt_datetime(g.get("end"), tz), fmt_duration(g.get("duration_s"))]
                 for g in gaps]
        pdf.data_table(["Von", "Bis", "Dauer"], rows2, (35, 35, 25), align=("LEFT", "LEFT", "RIGHT"))
        if (q.get("gap_count") or 0) > len(gaps):
            pdf.para(f"Gezeigt sind die {len(gaps)} längsten von {q.get('gap_count')} Lücken.", size=7.8,
                     color=SUBTLE, lh=3.9)
    pdf.para("Über Datenlücken werden keine Werte ergänzt. Lückenzeit zählt weder als auffällig noch als "
             "unauffällig; Zeitanteile beziehen sich auf die Zeit mit Messwerten.", size=7.8, color=SUBTLE, lh=3.9)


def _conclusion_section(pdf: _ReportPDF, snap: dict[str, Any]) -> None:
    c = snap.get("conclusion")
    if not c:
        return
    pdf.h1("Fazit")
    pdf.box(c.get("level") or "neutral", c.get("headline") or "Fazit", c.get("text") or "")
    for lim in c.get("limitations") or []:
        pdf.bullet(lim, size=8.8, color=MUTED)


# ---------------------------------------------------------------------------
# Einstieg
# ---------------------------------------------------------------------------
def render_pdf(snapshot: dict[str, Any] | None) -> bytes:
    """Erzeugt das PDF (A4, deutsch) aus einem Berichts-Snapshot. Auch ein leerer oder
    minimaler Snapshot ergibt ein gültiges PDF."""
    snap = snapshot or {}
    meta = snap.get("meta") or {}
    pdf = _ReportPDF(meta.get("title") or "Klimabericht")
    pdf.add_page()
    _title_block(pdf, snap)

    c = snap.get("conclusion")
    if c:
        pdf.box(c.get("level") or "neutral", c.get("headline") or "", c.get("text") or "", label="Kurzfazit")
    summary = snap.get("summary") or []
    if summary:
        pdf.h1("Zusammenfassung")
        pdf.para(" ".join(summary), size=9.6, lh=5)
    if not snap.get("kpis") and not summary:
        pdf.para("Dieser Bericht enthält keine auswertbaren Daten.", color=MUTED)

    for section in (_kpi_section, _timeseries_section, _humidity_section, _condensation_section,
                    _monthly_section, _seasonal_section, _day_night_section, _assessment_section,
                    _quality_section, _conclusion_section):
        section(pdf, snap)

    pdf.ln(2)
    pdf.para("Erstellt mit SwitchBot Climate Dashboard. Die Auswertung beschreibt die gemessene Raumluft und "
             "ersetzt keine Begutachtung vor Ort. Berechnungsgrundlagen: Magnus-Formel (Taupunkt), "
             "zeitgewichtete Mittelwerte, Orientierungswerte ohne rechtliche Bindung.", size=7.5, color=SUBTLE,
             lh=3.8)
    out = pdf.output()
    return bytes(out)


__all__ = ["render_pdf", "SECTIONS"]

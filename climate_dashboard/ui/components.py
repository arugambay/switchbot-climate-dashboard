"""Wiederverwendbare UI-Komponenten.

Jede Komponente gibt es in zwei Formen:

* ``<name>_html(...) -> str`` – reine Funktion, erzeugt HTML (testbar, auch für Export).
* ``<name>(...)`` – rendert das HTML mit ``st.html`` (ggf. plus Streamlit-Widgets).

Sicherheit: **Alle** übergebenen Texte werden mit ``html.escape`` maskiert (Raumnamen,
Sensornamen, Berichtstitel usw. sind frei wählbar). Nur intern erzeugtes Markup wird
unmaskiert eingesetzt. Wer bewusst vorformatiertes HTML übergeben will, nutzt ``Markup``.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from climate_dashboard.ui import theme
from climate_dashboard.ui.formatting import (
    MISSING,
    NARROW_NBSP,
    fmt_number,
    fmt_percent,
    fmt_value,
    is_missing,
)


@dataclass(frozen=True)
class Markup:
    """Kennzeichnet bereits sicheres HTML, das nicht erneut maskiert werden soll."""

    html: str

    def __str__(self) -> str:
        return self.html


def esc(value: Any) -> str:
    """Maskiert beliebige Werte für HTML. ``None``/NaN → ``–``; ``Markup`` bleibt unverändert."""
    if isinstance(value, Markup):
        return value.html
    if is_missing(value):
        return MISSING
    return html.escape(str(value), quote=True)


def _wrap(inner: str) -> str:
    return f'<div class="cd">{inner}</div>'


def _render(markup: str) -> None:
    import streamlit as st

    st.html(markup)


# ---------------------------------------------------------------------------
# Seiten- und Abschnittsköpfe
# ---------------------------------------------------------------------------
def page_header_html(title: str, subtitle: str | None = None, *, eyebrow: str | None = None) -> str:
    parts = ['<header class="cd-page-header"><div>']
    if eyebrow:
        parts.append(f'<p class="cd-page-header__eyebrow">{esc(eyebrow)}</p>')
    parts.append(f'<h1 class="cd-page-header__title">{esc(title)}</h1>')
    if subtitle:
        parts.append(f'<p class="cd-page-header__subtitle">{esc(subtitle)}</p>')
    parts.append("</div></header>")
    return _wrap("".join(parts))


def page_header(
    title: str,
    subtitle: str | None = None,
    actions: Callable[[], None] | None = None,
    *,
    eyebrow: str | None = None,
) -> None:
    """Seitentitel mit optionalem Untertitel. ``actions`` ist eine Funktion, die rechts
    daneben Streamlit-Widgets (z. B. Buttons) rendert; auf schmalen Bildschirmen darunter."""
    import streamlit as st

    if actions is None:
        _render(page_header_html(title, subtitle, eyebrow=eyebrow))
        return
    left, right = st.columns([1.5, 1], vertical_alignment="bottom")
    with left:
        _render(page_header_html(title, subtitle, eyebrow=eyebrow))
    with right:
        with st.container(horizontal=True, horizontal_alignment="right", gap="xsmall"):
            actions()


def section_header_html(title: str, subtitle: str | None = None) -> str:
    sub = f'<p class="cd-section__subtitle">{esc(subtitle)}</p>' if subtitle else ""
    return _wrap(f'<div class="cd-section"><h2 class="cd-section__title">{esc(title)}</h2>{sub}</div>')


def section_header(title: str, subtitle: str | None = None) -> None:
    _render(section_header_html(title, subtitle))


def context_bar_html(items: Sequence[tuple[str, Any]], *, primary: int = 0) -> str:
    """Kontextleiste (z. B. Raum · Sensor · letzte Messung): Paare aus Versal-Label und Wert.
    Das Element mit Index ``primary`` wird größer dargestellt."""
    parts = []
    for i, (label, value) in enumerate(items):
        if i:
            parts.append('<span class="cd-context__sep" aria-hidden="true"></span>')
        size = " cd-context__value--lg" if i == primary else ""
        parts.append(
            f'<div class="cd-context__item"><span class="cd-label">{esc(label)}</span>'
            f'<span class="cd-context__value{size}">{esc(value)}</span></div>'
        )
    return _wrap(f'<div class="cd-context">{"".join(parts)}</div>')


def context_bar(items: Sequence[tuple[str, Any]], *, primary: int = 0) -> None:
    _render(context_bar_html(items, primary=primary))


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def status_badge_html(level: Any, label: str | None = None) -> str:
    lvl = theme.normalize_level(level)
    text = label if label is not None else theme.level_label(lvl)
    return f'<span class="cd-badge cd-badge--{lvl}"><span class="cd-badge__dot"></span>{esc(text)}</span>'


def status_badge(level: Any, label: str | None = None) -> None:
    _render(_wrap(status_badge_html(level, label)))


def callout_html(level: Any, title: str, text: str | None = None) -> str:
    lvl = theme.normalize_level(level)
    icon = theme.STATUS_ICONS[lvl]
    body = f'<p class="cd-callout__text">{esc(text)}</p>' if text else ""
    return _wrap(
        f'<div class="cd-callout cd-callout--{lvl}" role="note">'
        f'<span class="cd-callout__icon" aria-hidden="true">{icon}</span>'
        f'<div><p class="cd-callout__title">{esc(title)}</p>{body}</div></div>'
    )


def callout(level: Any, title: str, text: str | None = None) -> None:
    """Hinweis-/Einschränkungsbox. ``level``: ok | hinweis | auffaellig | info | neutral."""
    _render(callout_html(level, title, text))


# ---------------------------------------------------------------------------
# Kennzahlen
# ---------------------------------------------------------------------------
def metric_card_html(
    label: str,
    value: Any,
    unit: str | None = None,
    sublabel: str | None = None,
    delta: str | None = None,
    status: Any = None,
    *,
    decimals: int = 1,
    card: bool = True,
    accent: str | None = None,
    emphasis: bool = False,
    extra: Markup | None = None,
) -> str:
    """Kennzahl-Karte. ``value`` darf bereits formatiert sein (str) oder eine Zahl
    (wird mit ``decimals`` deutsch formatiert). ``delta`` ist ein vorformatierter Text
    (z. B. ``fmt_delta(...)``) – Veränderungen sind beim Raumklima nicht per se gut/schlecht
    und werden daher neutral dargestellt.

    ``accent``: Messgröße (z. B. ``"temperature"``) → runder Farb-Chip in der Farbe, die auch
    die Diagramme verwenden. ``emphasis``: größere Ziffern für die wichtigsten Kennzahlen einer
    Seite. ``extra``: zusätzliches, intern erzeugtes Markup (z. B. ``meter_html``) unter dem Wert."""
    if isinstance(value, str | Markup):
        value_text = esc(value)
    else:
        value_text = esc(fmt_number(value, decimals))
    unit_html = ""
    if unit and value_text != MISSING:
        unit_html = f'<span class="cd-metric__unit">{NARROW_NBSP}{esc(unit)}</span>'
    lvl = theme.normalize_level(status) if status is not None else None
    badge = status_badge_html(lvl) if lvl and lvl != "neutral" else ""
    classes = (
        "cd-metric"
        + (" cd-card" if card else "")
        + (f" cd-metric--{lvl}" if lvl else "")
        + (" cd-metric--lg" if emphasis else "")
    )
    dot = chip = ""
    color = theme.METRIC_COLORS.get(accent or "")
    style = f' style="--cd-dot:{color}"' if color else ""
    if color:
        dot = '<span class="cd-metric__dot" aria-hidden="true"></span>'
        chip = '<span class="cd-metric__chip" aria-hidden="true"></span>'
    # Rechts oben: Farb-Chip der Messgröße, sonst das Status-Badge; mit beiden wandert das Badge
    # unter den Wert.
    corner = chip or badge
    parts = [
        f'<div class="{classes}"{style}>',
        f'<div class="cd-metric__label"><span>{dot}{esc(label)}</span>{corner}</div>',
        f'<div class="cd-metric__value">{value_text}{unit_html}</div>',
    ]
    if chip and badge:
        parts.append(f'<div class="cd-metric__status">{badge}</div>')
    if extra is not None:
        parts.append(str(extra))
    if delta:
        parts.append(f'<div class="cd-metric__delta">{esc(delta)}</div>')
    if sublabel:
        parts.append(f'<div class="cd-metric__sub">{esc(sublabel)}</div>')
    parts.append("</div>")
    return "".join(parts)


def metric_card(
    label: str,
    value: Any,
    unit: str | None = None,
    sublabel: str | None = None,
    delta: str | None = None,
    status: Any = None,
    *,
    decimals: int = 1,
    accent: str | None = None,
    emphasis: bool = False,
) -> None:
    _render(
        _wrap(
            metric_card_html(
                label, value, unit, sublabel, delta, status, decimals=decimals, accent=accent, emphasis=emphasis
            )
        )
    )


def meter_html(
    value: float | None,
    lo: float | None,
    hi: float | None,
    *,
    accent: str | None = None,
    thresholds: Sequence[float] = (),
    lo_label: str | None = None,
    hi_label: str | None = None,
    bars: int = 44,
) -> Markup:
    """Streifen-Skala: zeigt, wo ``value`` innerhalb von ``lo``…``hi`` liegt. Streifen bis zum
    Wert sind in der Messgrößen-Farbe gefüllt (heller werdend zum Anfang), der Wert selbst ist
    markiert, ``thresholds`` (z. B. Feuchte-Schwellen) erscheinen als dunklere Streifen.
    Rein darstellend – es wird kein neuer Kennwert berechnet."""
    color = theme.METRIC_COLORS.get(accent or "", theme.COLORS["text_subtle"])
    if value is None or lo is None or hi is None or is_missing(value) or is_missing(lo) or is_missing(hi) or hi <= lo:
        return Markup("")

    def index(x: float) -> int:
        return int(round((min(max(float(x), lo), hi) - lo) / (hi - lo) * (bars - 1)))

    now = index(value)
    marks = {index(t) for t in thresholds if lo < t < hi}
    spans = []
    for i in range(bars):
        if i == now:
            cls = "cd-meter__bar cd-meter__bar--now"
            style = ""
        elif i in marks:
            cls, style = "cd-meter__bar cd-meter__bar--threshold", ""
        elif i < now:
            # Füllung wird zum aktuellen Wert hin kräftiger
            alpha = 0.28 + 0.72 * (i + 1) / max(now, 1)
            cls, style = "cd-meter__bar cd-meter__bar--on", f' style="opacity:{alpha:.2f}"'
        else:
            cls, style = "cd-meter__bar", ""
        spans.append(f'<span class="{cls}"{style}></span>')
    scale = ""
    if lo_label or hi_label:
        scale = f'<div class="cd-meter__scale"><span>{esc(lo_label or "")}</span><span>{esc(hi_label or "")}</span></div>'
    return Markup(
        f'<div class="cd-meter" style="--cd-dot:{color}" role="img" aria-label="Skala">'
        f'<div class="cd-meter__bars">{"".join(spans)}</div>{scale}</div>'
    )


_RING_GLYPHS = {"ok": "✓", "hinweis": "!", "auffaellig": "!", "info": "i", "neutral": "–"}


def status_hero_html(
    level: Any,
    title: str,
    text: str | None = None,
    *,
    kicker: str | None = None,
    chips: Sequence[tuple[str, Any]] = (),
) -> str:
    """Hauptkarte für den Raumzustand: Stufe als Ring mit Symbol, Titel in der Statusfarbe,
    Begründung und Kontext-Chips (z. B. Sensor, letzte Messung). Bewusst ohne Zahl im Ring –
    es gibt keinen Score."""
    lvl = theme.normalize_level(level)
    tokens = theme.STATUS[lvl]
    glyph = _RING_GLYPHS.get(lvl, "–")
    # Reines CSS (st.html entfernt SVG): Ring mit Statusfarbe und Symbol in der Mitte.
    ring = f'<div class="cd-ring" aria-hidden="true"><span class="cd-ring__glyph">{glyph}</span></div>'
    chip_html = "".join(f'<span class="cd-chip">{esc(k)} <b>{esc(v)}</b></span>' for k, v in chips)
    kicker_html = f'<p class="cd-hero__kicker">{esc(kicker)}</p>' if kicker else ""
    text_html = f'<p class="cd-hero__text">{esc(text)}</p>' if text else ""
    meta_html = f'<div class="cd-hero__meta">{chip_html}</div>' if chip_html else ""
    return _wrap(
        f'<section class="cd-hero" lang="de" style="--cd-hero-solid:{tokens["solid"]};--cd-hero-fg:{tokens["fg"]}" '
        f'aria-label="Raumzustand"><div>{kicker_html}<h2 class="cd-hero__title">{esc(title)}</h2>'
        f"{text_html}{meta_html}</div>{ring}</section>"
    )


def stat_panel_html(rows: Sequence[Mapping[str, Any]], *, accent: str | None = None) -> str:
    """Kompakte Kennzahlen-Karte: mehrere Zeilen aus Bezeichnung (+ Unterzeile) links und Wert
    (+ Einheit) rechts – für zusammengehörige Werte statt vieler Einzelkarten.
    ``rows``: Dicts mit ``label``, ``value``, optional ``unit`` und ``sublabel``."""
    color = theme.METRIC_COLORS.get(accent or "")
    style = f' style="--cd-dot:{color}"' if color else ""
    items = []
    for r in rows:
        value = esc(r.get("value"))
        unit = f'<span class="cd-stat__unit">{NARROW_NBSP}{esc(r["unit"])}</span>' if r.get("unit") and value != MISSING else ""
        sub = f'<span class="cd-stat__sub">{esc(r["sublabel"])}</span>' if r.get("sublabel") else ""
        items.append(
            f'<li class="cd-stat"><div class="cd-stat__text"><span class="cd-stat__label">{esc(r.get("label"))}</span>'
            f'{sub}</div><div class="cd-stat__value">{value}{unit}</div></li>'
        )
    return _wrap(f'<ul class="cd-stats"{style}>{"".join(items)}</ul>')


def metric_grid_html(cards: Sequence[Mapping[str, Any]], *, min_width: int = 190) -> str:
    """Responsives Raster (CSS-Grid, auto-fit) – bricht auf dem Smartphone einspaltig um.
    ``cards``: Dicts mit den Parametern von ``metric_card``."""
    inner = "".join(metric_card_html(**dict(c)) for c in cards)
    return _wrap(f'<div class="cd-metric-grid" style="--cd-grid-min:{int(min_width)}px">{inner}</div>')


def metric_grid(cards: Sequence[Mapping[str, Any]], *, min_width: int = 190) -> None:
    _render(metric_grid_html(cards, min_width=min_width))


# ---------------------------------------------------------------------------
# Leerzustand
# ---------------------------------------------------------------------------
def empty_state_html(title: str, text: str | None = None, *, icon: str = "◌") -> str:
    body = f'<p class="cd-empty__text">{esc(text)}</p>' if text else ""
    return _wrap(
        f'<div class="cd-empty"><div class="cd-empty__icon" aria-hidden="true">{esc(icon)}</div>'
        f'<p class="cd-empty__title">{esc(title)}</p>{body}</div>'
    )


def empty_state(
    title: str,
    text: str | None = None,
    action_label: str | None = None,
    *,
    icon: str = "◌",
    key: str | None = None,
) -> bool:
    """Leerzustand mit optionaler Aktion. Gibt ``True`` zurück, wenn die Aktion geklickt wurde."""
    import streamlit as st

    _render(empty_state_html(title, text, icon=icon))
    if not action_label:
        return False
    with st.container(horizontal=True, horizontal_alignment="center"):
        return st.button(action_label, type="primary", key=key or f"empty_{title}_{action_label}")


# ---------------------------------------------------------------------------
# Datenqualität
# ---------------------------------------------------------------------------
def coverage_level(coverage_pct: float | None, ok: float = 90.0, hint: float = 70.0) -> str:
    if coverage_pct is None or is_missing(coverage_pct):
        return "neutral"
    if coverage_pct >= ok:
        return "ok"
    if coverage_pct >= hint:
        return "hinweis"
    return "auffaellig"


def data_quality_bar_html(
    coverage_pct: float | None,
    *,
    label: str = "Zeitabdeckung",
    note: str | None = None,
    thresholds: tuple[float, float] = (90.0, 70.0),
) -> str:
    """Balken für Datenabdeckung in Prozent (0–100). Farbe: ≥ 90 % ok, ≥ 70 % Hinweis, sonst auffällig."""
    pct = (
        None
        if coverage_pct is None or is_missing(coverage_pct)
        else max(0.0, min(100.0, float(coverage_pct)))
    )
    lvl = coverage_level(pct, *thresholds)
    width = 0.0 if pct is None else pct
    note_html = f'<div class="cd-quality__note">{esc(note)}</div>' if note else ""
    return _wrap(
        f'<div class="cd-quality" role="meter" aria-valuemin="0" aria-valuemax="100" '
        f'aria-valuenow="{0 if pct is None else round(pct, 1)}" aria-label="{esc(label)}">'
        f'<div class="cd-quality__head"><span class="cd-quality__label">{esc(label)}</span>'
        f'<span class="cd-quality__value">{esc(fmt_percent(pct, 0 if pct in (None, 100.0) else 1))}</span></div>'
        f'<div class="cd-quality__track"><div class="cd-quality__fill cd-quality__fill--{lvl}" '
        f'style="width:{width:.1f}%"></div></div>{note_html}</div>'
    )


def data_quality_bar(
    coverage_pct: float | None, *, label: str = "Zeitabdeckung", note: str | None = None
) -> None:
    _render(data_quality_bar_html(coverage_pct, label=label, note=note))


# ---------------------------------------------------------------------------
# Listen
# ---------------------------------------------------------------------------
def key_value_list_html(items: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> str:
    """Definitionsliste. Werte werden mit ``fmt_value`` formatiert (Zahlen deutsch, None → –)."""
    pairs = items.items() if isinstance(items, Mapping) else items
    rows = "".join(
        f"<dt>{esc(k)}</dt><dd>{v.html if isinstance(v, Markup) else esc(fmt_value(v))}</dd>"
        for k, v in pairs
    )
    return _wrap(f'<dl class="cd-kv">{rows}</dl>')


def key_value_list(items: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> None:
    _render(key_value_list_html(items))


_CATEGORY_LABELS = {
    "humidity": "Luftfeuchtigkeit",
    "temperature": "Temperatur",
    "condensation": "Taupunkt",
    "dew_point": "Taupunkt",
    "quality": "Datenqualität",
    "data_quality": "Datenqualität",
    "ventilation": "Lüftung",
    "limitation": "Einschränkung",
    "limitations": "Einschränkungen",
    # deutsche Kategorien aus analysis.assessment
    "feuchte": "Luftfeuchtigkeit",
    "dauer": "Dauer erhöhter Feuchte",
    "taupunkt": "Taupunkt",
    "kondensation": "Kondensation",
    "datenqualitaet": "Datenqualität",
    "veraenderung": "Veränderung",
    "trocken": "Trockene Luft",
    "temperatur": "Temperatur",
}


def _finding_get(finding: Any, key: str) -> Any:
    if isinstance(finding, Mapping):
        return finding.get(key)
    return getattr(finding, key, None)


def _evidence_html(evidence: Any) -> str:
    if evidence is None or evidence == "" or evidence == [] or evidence == {}:
        return ""
    if isinstance(evidence, Mapping):
        text = " · ".join(f"{k}: {fmt_value(v)}" for k, v in evidence.items())
    elif isinstance(evidence, list | tuple):
        text = " · ".join(fmt_value(v) for v in evidence)
    else:
        text = str(evidence)
    return f'<p class="cd-finding__evidence">{esc(text)}</p>'


def finding_list_html(findings: Iterable[Any]) -> str:
    """Befunde (Dicts oder Objekte mit ``level``, ``category``, ``text``, ``evidence``)."""
    items = []
    for fnd in findings:
        lvl = theme.normalize_level(_finding_get(fnd, "level"))
        cat = _finding_get(fnd, "category")
        cat_label = _CATEGORY_LABELS.get(str(cat).lower(), cat) if cat else None
        cat_html = f'<div class="cd-finding__category">{esc(cat_label)}</div>' if cat_label else ""
        items.append(
            f'<li class="cd-finding cd-finding--{lvl}">'
            f'<span class="cd-finding__icon" aria-hidden="true">{theme.STATUS_ICONS.get(lvl, "•")}</span>'
            f'<div>{cat_html}<p class="cd-finding__text">{esc(_finding_get(fnd, "text") or "")}</p>'
            f"{_evidence_html(_finding_get(fnd, 'evidence'))}</div>"
            f'<div class="cd-finding__badge">{status_badge_html(lvl)}</div></li>'
        )
    if not items:
        return ""
    return _wrap(f'<ul class="cd-findings">{"".join(items)}</ul>')


def finding_list(findings: Iterable[Any], *, empty_text: str = "Keine Befunde für diesen Zeitraum.") -> None:
    markup = finding_list_html(findings)
    _render(markup or callout_html("neutral", empty_text))


# ---------------------------------------------------------------------------
# Schrittanzeige (Import-Assistent)
# ---------------------------------------------------------------------------
def step_indicator_html(steps: Sequence[str], current: int) -> str:
    """``current`` ist 0-basiert. Auf schmalen Bildschirmen wird nur der aktuelle Schritt beschriftet."""
    items = []
    for i, label in enumerate(steps):
        state = "done" if i < current else "current" if i == current else "todo"
        aria = ' aria-current="step"' if state == "current" else ""
        num = "✓" if state == "done" else f"{i + 1:02d}"
        items.append(
            f'<li class="cd-step cd-step--{state}"{aria}><span class="cd-step__num" aria-hidden="true">{num}</span>'
            f'<div class="cd-step__text"><span class="cd-step__kicker">Schritt {i + 1:02d}</span>'
            f'<span class="cd-step__label">{esc(label)}</span></div></li>'
        )
    return _wrap(f'<ol class="cd-steps" aria-label="Fortschritt">{"".join(items)}</ol>')


def step_indicator(steps: Sequence[str], current: int) -> None:
    _render(step_indicator_html(steps, current))


# ---------------------------------------------------------------------------
# Laden / Fortschritt
# ---------------------------------------------------------------------------
def skeleton_html(height: int = 120, *, rows: int = 1) -> str:
    blocks = "".join(
        f'<div class="cd-skeleton" style="height:{int(height)}px;margin-bottom:8px"></div>'
        for _ in range(rows)
    )
    return _wrap(f'<div aria-busy="true" aria-label="Wird geladen">{blocks}</div>')


def skeleton(height: int = 120, *, rows: int = 1):
    """Platzhalter während des Ladens. Rückgabe ist ein ``st.empty``, das später überschrieben
    werden kann: ``ph = skeleton(); …; ph.empty()``."""
    import streamlit as st

    ph = st.empty()
    ph.html(skeleton_html(height, rows=rows))
    return ph


@contextmanager
def loading(text: str = "Daten werden geladen …") -> Iterator[None]:
    """Einheitlicher Lade-Spinner."""
    import streamlit as st

    with st.spinner(text, show_time=False):
        yield


class Progress:
    """Dünner Wrapper um ``st.progress`` mit deutscher Prozentanzeige.

    >>> p = Progress("Import läuft")
    >>> p.update(0.4, "Zeilen prüfen")   # 0…1
    >>> p.done()
    """

    def __init__(self, text: str = "Wird verarbeitet …") -> None:
        import streamlit as st

        self._text = text
        self._bar = st.progress(0.0, text=text)

    def update(self, fraction: float, detail: str | None = None) -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        label = f"{self._text} – {fmt_percent(fraction, ratio=True)}"
        if detail:
            label += f" · {detail}"
        self._bar.progress(fraction, text=label)

    def done(self) -> None:
        self._bar.empty()


# ---------------------------------------------------------------------------
# Diagramme / Branding
# ---------------------------------------------------------------------------
def render_chart(fig: Any, *, key: str | None = None, height: int | None = None) -> None:
    """Gibt eine Plotly-Figur im Designsystem aus (``theme=None``, damit unser Template gilt)."""
    import streamlit as st

    if height is not None:
        fig.update_layout(height=height)
    # Transparente Flächen, damit die Glas-Diagrammkarte durchscheint (Streamlit setzt sonst
    # seine Hintergrundfarbe). Eigene Hintergründe einzelner Diagramme bleiben erhalten.
    if fig.layout.plot_bgcolor is None:
        fig.update_layout(plot_bgcolor="rgba(255,255,255,0)")
    if fig.layout.paper_bgcolor is None:
        fig.update_layout(paper_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, theme=None, width="stretch", config=dict(theme.PLOTLY_CONFIG), key=key)


_BRAND_SVG = (
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M12 2.5c3.5 4.2 6 7.6 6 11a6 6 0 0 1-12 0c0-3.4 2.5-6.8 6-11z"/>'
    '<path d="M9.5 14.5a2.5 2.5 0 0 0 2.5 2.5"/></svg>'
)


def brand_html(name: str = "Climate Dashboard", tagline: str | None = "SwitchBot-Auswertung") -> str:
    tag = f'<div class="cd-brand__tag">{esc(tagline)}</div>' if tagline else ""
    return _wrap(
        f'<div class="cd-brand"><div class="cd-brand__mark">{_BRAND_SVG}</div>'
        f'<div><div class="cd-brand__name">{esc(name)}</div>{tag}</div></div>'
    )


def mode_notice_html(mode: str) -> str:
    """Deutlicher Hinweis auf den lokalen Entwicklungsmodus (leer im Produktionsmodus)."""
    if mode != "local":
        return ""
    return _wrap(
        '<div class="cd-mode" role="status"><span aria-hidden="true">●</span><div>'
        "<strong>Lokaler Entwicklungsmodus</strong>"
        "Daten werden nur lokal gespeichert (SQLite-Datei auf diesem Rechner).</div></div>"
    )


def report_footer_html(text: str) -> str:
    return _wrap(f'<footer class="cd-report-footer">{esc(text)}</footer>')

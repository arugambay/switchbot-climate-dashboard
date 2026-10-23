"""Design-Tokens, globales CSS und Plotly-Template.

Leitlinien
----------
* **Ruhig und neutral:** Slate-Grautöne als Basis, ein Primärblau (identisch zu
  ``.streamlit/config.toml``), viel Weißraum, klare Typo-Hierarchie.
* **Dezente Statusfarben:** ``ok`` (gedämpftes Grün), ``hinweis`` (Amber), ``auffaellig``
  (gedämpftes Rot), ``info`` (Blau). Status wird nie nur über Farbe vermittelt – Badges und
  Callouts tragen immer auch Text bzw. ein Symbol.
* **Messgrößen-Farben** basieren auf der Okabe-Ito-Palette (farbenblind-freundlich).
  Taupunkt wird zusätzlich gestrichelt gezeichnet, damit er neben der Temperatur auch ohne
  Farbwahrnehmung unterscheidbar ist.
* **Kein Dark Mode** (bewusst): ``config.toml`` setzt ``base = "light"``; Diagramme, PDF und
  öffentliche Berichte sind auf hellen Hintergrund abgestimmt. Alle Farben liegen als
  CSS-Custom-Properties (``--cd-*``) vor, ein Dark Mode ließe sich später zentral ergänzen.
* **Schrift:** System-Schriftstapel (Inter, falls lokal installiert; sonst Segoe UI Variable
  bzw. SF Pro), für Überschriften und große Kennzahlen die Display-Varianten. Bewusst *kein*
  Nachladen von Google Fonts – öffentliche Berichte sollen keine Drittanbieter-Anfragen
  auslösen (Datenschutz/DSGVO).
* **Gestaltung („Studio“):** ruhiger hellgrauer Grund, weiße Karten mit großen Rundungen und
  kaum Schatten, Serifen-Seitentitel, kräftige Kennzahlen mit hochgestellter Einheit,
  Streifen-Skalen für Werte innerhalb einer Spanne, Pill-Reiter, Primäraktionen in Tiefschwarz,
  Markenblau für Logo, Fokus und aktive Navigation. Inhalte und Funktionen unverändert.

CSS-Strategie
-------------
Fundament sind eigene Klassen mit Präfix ``cd-`` in HTML-Blöcken (``st.html``). Streamlit-
interne Elemente werden nur über wenige, stabile ``data-testid``-Selektoren angesprochen
(siehe ``_STREAMLIT_OVERRIDES``); jede Regel ist kommentiert und rein kosmetisch – fällt ein
Selektor in einer künftigen Streamlit-Version weg, funktioniert die App unverändert weiter.
"""

from __future__ import annotations

from typing import Literal

# ---------------------------------------------------------------------------
# Farben
# ---------------------------------------------------------------------------
NEUTRAL: dict[int, str] = {
    0: "#FFFFFF",
    50: "#F8FAFC",
    100: "#F1F5F9",
    200: "#E2E8F0",
    300: "#CBD5E1",
    400: "#94A3B8",
    500: "#64748B",
    600: "#475569",
    700: "#334155",
    800: "#1E293B",
    900: "#0F172A",
}

COLORS: dict[str, str] = {
    "bg": "#F2F3F5",  # = backgroundColor in config.toml (ruhiges Hellgrau)
    "surface": "#FFFFFF",
    "surface_muted": "#F7F8FA",  # Hover, innere Flächen, Tabellenköpfe
    "border": "#E6E8EC",
    "border_strong": "#D3D7DE",
    "text": "#111318",  # = textColor in config.toml
    "text_muted": "#4A505C",
    "text_subtle": "#868C98",
    "grid": "#EFF1F4",
    "axis": "#DADDE3",
    "ink": "#111318",  # Primäraktionen, ausgewählte Reiter
    "brand": "#2563EB",  # Markenblau (Logo, Fokus, Akzente)
    "primary": "#2563EB",
    "primary_hover": "#1D4ED8",
    "primary_soft": "#EEF3FE",
    "gap": "#E9EBEF",  # Datenlücken, leere Skalenstreifen
}

# Status-Stufen: fg = Text/Symbol, bg = Fläche, border = Rahmen, solid = Diagrammfarbe
STATUS: dict[str, dict[str, str]] = {
    "ok": {"fg": "#1B7343", "bg": "#EAF6EF", "border": "#CDE9D8", "solid": "#2E9E5B", "label": "Unauffällig"},
    "hinweis": {
        "fg": "#965A00",
        "bg": "#FFF5E1",
        "border": "#F6DFAF",
        "solid": "#E39A0C",
        "label": "Hinweis",
    },
    "auffaellig": {
        "fg": "#B4231A",
        "bg": "#FDEDEB",
        "border": "#F6CBC6",
        "solid": "#E0463E",
        "label": "Auffällig",
    },
    "info": {"fg": "#1D4ED8", "bg": "#EEF3FE", "border": "#D3E0FC", "solid": "#3B6FE0", "label": "Info"},
    "neutral": {
        "fg": NEUTRAL[600],
        "bg": NEUTRAL[100],
        "border": NEUTRAL[200],
        "solid": NEUTRAL[400],
        "label": "Neutral",
    },
}
STATUS_ICONS: dict[str, str] = {  # Unicode statt Icon-Font → funktioniert in st.html und PDF
    "ok": "✓",
    "hinweis": "!",
    "auffaellig": "!",
    "info": "i",
    "neutral": "•",
}

_LEVEL_ALIASES: dict[str, str] = {
    "ok": "ok",
    "good": "ok",
    "success": "ok",
    "unauffaellig": "ok",
    "unauffällig": "ok",
    "normal": "ok",
    "hinweis": "hinweis",
    "notice": "hinweis",
    "warning": "hinweis",
    "warn": "hinweis",
    "caution": "hinweis",
    "moderate": "hinweis",
    "medium": "hinweis",
    "auffaellig": "auffaellig",
    "auffällig": "auffaellig",
    "alert": "auffaellig",
    "high": "auffaellig",
    "critical": "auffaellig",
    "error": "auffaellig",
    "danger": "auffaellig",
    "info": "info",
    "information": "info",
    "low": "info",
    "neutral": "neutral",
    "none": "neutral",
}


def normalize_level(level: object) -> str:
    """Bildet beliebige Status-Bezeichner (deutsch/englisch, auch Enums) auf
    ``ok``/``hinweis``/``auffaellig``/``info``/``neutral`` ab."""
    if level is None:
        return "neutral"
    key = str(getattr(level, "value", level)).strip().lower()
    return _LEVEL_ALIASES.get(key, "neutral")


def level_label(level: object) -> str:
    return STATUS[normalize_level(level)]["label"]


# Messgrößen: klar unterscheidbare, gut auf Weiß lesbare Töne; dash = Linienstil als zweites,
# farbunabhängiges Merkmal (Taupunkt gestrichelt).
METRIC_COLORS: dict[str, str] = {
    "temperature": "#EC6A2C",  # Orange
    "humidity": "#1393AD",  # Petrol
    "dew_point": "#2E9E5B",  # Grün
    "vpd": "#8A63D2",  # Violett
    "abs_humidity": "#C98A12",  # Ocker
    "dew_point_spread": "#6B5BA8",
}
METRIC_DASH: dict[str, str] = {
    "temperature": "solid",
    "humidity": "solid",
    "dew_point": "dash",
    "vpd": "solid",
    "abs_humidity": "dot",
    "dew_point_spread": "solid",
}
FALLBACK_SERIES: tuple[str, ...] = (
    "#0072B2",
    "#D55E00",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#7F7F7F",
)

# Vergleich zweier Zeiträume: A kräftig, B gedämpft (Helligkeit unterscheidet zusätzlich)
COMPARE_COLORS: tuple[str, str] = ("#2563EB", "#E69F00")
DAY_NIGHT_COLORS: dict[str, str] = {"day": "#E69F00", "night": "#3F4C9A"}

# Feuchtebereiche: divergierend Sand → Blau (BrBG-artig, farbenblind-sicher), Lücke = grau
HUMIDITY_BAND_COLORS: dict[str, str] = {
    "dry": "#D6B26A",
    "normal": "#CFDCEB",
    "elevated": "#6FA5D6",
    "high": "#1F5FA8",
    "gap": COLORS["gap"],
}
_BAND_ALIASES: dict[str, str] = {
    "dry": "dry",
    "low": "dry",
    "trocken": "dry",
    "normal": "normal",
    "ok": "normal",
    "comfort": "normal",
    "unauffällig": "normal",
    "elevated": "elevated",
    "erhöht": "elevated",
    "erhoeht": "elevated",
    "high": "high",
    "hoch": "high",
    "very_high": "high",
    "gap": "gap",
    "unknown": "gap",
    "lücke": "gap",
    "luecke": "gap",
    "missing": "gap",
    "datenlücke": "gap",
}
HUMIDITY_BAND_LABELS: dict[str, str] = {
    "dry": "Trocken",
    "normal": "Unauffällig",
    "elevated": "Erhöht",
    "high": "Hoch",
    "gap": "Datenlücke",
}


def normalize_band(key: object) -> str | None:
    return _BAND_ALIASES.get(str(key).strip().lower())


def hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


# ---------------------------------------------------------------------------
# Typografie, Abstände, Radien, Schatten
# ---------------------------------------------------------------------------
FONT_FAMILY = (
    'Inter, -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable Text", "Segoe UI", Roboto, '
    '"Helvetica Neue", Arial, sans-serif'
)
# Seitentitel und Raumzustand: klassische Serifen-Systemschrift (keine externen Schriften).
FONT_DISPLAY = (
    '"Iowan Old Style", "Palatino Linotype", Palatino, "Book Antiqua", Georgia, "Times New Roman", serif'
)
# Große Kennzahlen: kompakte Display-Sans mit Tabellenziffern.
FONT_FIGURES = (
    '"Inter Display", Inter, -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI Variable Display", '
    '"Segoe UI", Roboto, sans-serif'
)
FONT_MONO = 'ui-monospace, "Cascadia Mono", "SFMono-Regular", Menlo, Consolas, monospace'
FONT_SIZE: dict[str, str] = {
    "2xs": "0.6875rem",  # Versal-Labels
    "xs": "0.75rem",
    "sm": "0.8125rem",
    "base": "0.9375rem",
    "md": "1rem",
    "lg": "1.125rem",
    "xl": "1.375rem",
    "2xl": "2.375rem",
    "metric": "2.25rem",
    "metric-lg": "3.25rem",
}
SPACE: dict[str, str] = {
    "1": "0.25rem",
    "2": "0.5rem",
    "3": "0.75rem",
    "4": "1rem",
    "5": "1.25rem",
    "6": "1.5rem",
    "8": "2rem",
    "10": "2.5rem",
}
# Große Rundungen, kaum Schatten: Karten heben sich über Weiß auf Hellgrau ab.
RADIUS: dict[str, str] = {"sm": "8px", "md": "12px", "lg": "18px", "pill": "999px"}
SHADOW: dict[str, str] = {
    "sm": "0 1px 2px rgba(17,19,24,0.04)",
    "md": "0 1px 2px rgba(17,19,24,0.04), 0 10px 28px -14px rgba(17,19,24,0.14)",
}

PLOTLY_TEMPLATE_NAME = "climate"
# Für st.plotly_chart(config=...): keine Plotly-Werbung, kein Scroll-Zoom (Seite bleibt auf
# Mobilgeräten scrollbar), Export als PNG mit sinnvollem Namen.
PLOTLY_CONFIG: dict[str, object] = {
    "displaylogo": False,
    "scrollZoom": False,
    "responsive": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d", "toggleSpikelines"],
    "toImageButtonOptions": {"format": "png", "filename": "klimadiagramm", "scale": 2},
}


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------
def css_variables() -> str:
    lines = [
        f"--cd-font: {FONT_FAMILY};",
        f"--cd-font-display: {FONT_DISPLAY};",
        f"--cd-font-figures: {FONT_FIGURES};",
        f"--cd-font-mono: {FONT_MONO};",
    ]
    for k, v in METRIC_COLORS.items():
        lines.append(f"--cd-metric-{k.replace('_', '-')}: {v};")
    for k, v in COLORS.items():
        lines.append(f"--cd-{k.replace('_', '-')}: {v};")
    for level, tokens in STATUS.items():
        for part in ("fg", "bg", "border", "solid"):
            lines.append(f"--cd-{level}-{part}: {tokens[part]};")
    for k, v in FONT_SIZE.items():
        lines.append(f"--cd-text-{k}: {v};")
    for k, v in SPACE.items():
        lines.append(f"--cd-space-{k}: {v};")
    for k, v in RADIUS.items():
        lines.append(f"--cd-radius-{k}: {v};")
    for k, v in SHADOW.items():
        lines.append(f"--cd-shadow-{k}: {v};")
    return ":root {\n  " + "\n  ".join(lines) + "\n}"


_COMPONENT_CSS = """
/* =====================================================================
   „Studio“-Designsystem: ruhiger hellgrauer Grund, weiße Karten mit großen
   Rundungen, Serifen-Überschriften, kräftige Kennzahlen, Streifen-Skalen.
   ===================================================================== */

/* ---------- Basis ---------- */
.cd { font-family: var(--cd-font); color: var(--cd-text); line-height: 1.5; -webkit-font-smoothing: antialiased; }
.cd *, .cd *::before, .cd *::after { box-sizing: border-box; }
.cd-muted { color: var(--cd-text-muted); }
.cd-subtle { color: var(--cd-text-subtle); }
.cd-num { font-variant-numeric: tabular-nums; }
.cd-label { font-size: var(--cd-text-xs); font-weight: 550; color: var(--cd-text-subtle); line-height: 1.3; }

/* ---------- Karten ---------- */
.cd-card, .cd-context, .cd-kv, .cd-steps, .cd-empty, .cd-hero, .cd-panel {
  background: var(--cd-surface); border: 1px solid var(--cd-border); border-radius: var(--cd-radius-lg);
  box-shadow: var(--cd-shadow-sm); }
.cd-card { padding: var(--cd-space-5) var(--cd-space-5); transition: border-color .15s ease, box-shadow .15s ease; }
.cd-card:hover { border-color: var(--cd-border-strong); box-shadow: var(--cd-shadow-md); }
.cd-panel { padding: var(--cd-space-5); }
.cd-panel__head { display: flex; align-items: center; justify-content: space-between; gap: var(--cd-space-3);
  margin-bottom: var(--cd-space-4); }
.cd-panel__title { font-size: var(--cd-text-md); font-weight: 650; margin: 0; letter-spacing: -0.01em; }
.cd-panel__sub { font-size: var(--cd-text-xs); color: var(--cd-text-subtle); margin: 2px 0 0; }

/* ---------- Seitenkopf ---------- */
.cd-page-header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between;
  gap: var(--cd-space-4); padding: 0 0 var(--cd-space-5); }
.cd-page-header__eyebrow { font-size: var(--cd-text-xs); font-weight: 600; color: var(--cd-text-subtle); margin: 0 0 6px;
  display: inline-flex; align-items: center; gap: 8px; }
.cd-page-header__eyebrow::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--cd-brand); }
.cd-page-header__title { font-family: var(--cd-font-display); font-size: var(--cd-text-2xl); font-weight: 500;
  letter-spacing: -0.02em; line-height: 1.1; margin: 0; color: var(--cd-text); }
.cd-page-header__subtitle { font-size: var(--cd-text-sm); color: var(--cd-text-muted); margin: var(--cd-space-2) 0 0;
  max-width: 72ch; }
.cd-page-header__actions { display: flex; gap: var(--cd-space-2); flex-wrap: wrap; }

/* ---------- Abschnittskopf ---------- */
.cd-section { margin: var(--cd-space-8) 0 var(--cd-space-3); display: flex; flex-direction: column; gap: 2px; }
.cd-section__title { font-size: var(--cd-text-lg); font-weight: 650; margin: 0; letter-spacing: -0.015em; color: var(--cd-text); }
.cd-section__subtitle { font-size: var(--cd-text-sm); color: var(--cd-text-muted); margin: 0; max-width: 80ch; }

/* ---------- Kontextleiste ---------- */
.cd-context { display: flex; flex-wrap: wrap; align-items: center; gap: var(--cd-space-3) var(--cd-space-6);
  padding: var(--cd-space-4) var(--cd-space-5); }
.cd-context__item { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cd-context__value { font-weight: 600; font-size: var(--cd-text-sm); color: var(--cd-text); overflow-wrap: anywhere; }
.cd-context__value--lg { font-family: var(--cd-font-display); font-size: var(--cd-text-xl); font-weight: 500; }
.cd-context__sep { width: 1px; align-self: stretch; background: var(--cd-border); }

/* ---------- Kennzahlen ---------- */
.cd-metric-grid { display: grid; gap: var(--cd-space-3);
  grid-template-columns: repeat(auto-fit, minmax(min(100%, var(--cd-grid-min, 190px)), 1fr)); }
.cd-metric { position: relative; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.cd-metric__label { font-size: var(--cd-text-sm); color: var(--cd-text-muted); font-weight: 550;
  display: flex; align-items: flex-start; justify-content: space-between; gap: var(--cd-space-2); min-height: 32px; }
.cd-metric__label > span:first-child { display: inline-flex; align-items: center; gap: 8px; min-width: 0; padding-top: 6px; }
.cd-metric__chip { flex: none; width: 34px; height: 34px; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; background: color-mix(in srgb, var(--cd-dot) 13%, #fff);
  box-shadow: 0 0 0 4px color-mix(in srgb, var(--cd-dot) 6%, transparent); }
.cd-metric__chip::after { content: ""; width: 10px; height: 10px; border-radius: 50%; background: var(--cd-dot); }
.cd-metric__dot { display: none; }
.cd-metric__value { font-family: var(--cd-font-figures); font-size: var(--cd-text-metric); font-weight: 700;
  letter-spacing: -0.035em; line-height: 1.05; font-variant-numeric: tabular-nums; color: var(--cd-text);
  overflow-wrap: anywhere; }
.cd-metric__unit { font-family: var(--cd-font); font-size: var(--cd-text-sm); font-weight: 550; letter-spacing: 0;
  color: var(--cd-text-subtle); margin-left: 3px; vertical-align: super; }
.cd-metric__sub { font-size: var(--cd-text-xs); color: var(--cd-text-subtle); line-height: 1.45; }
.cd-metric__delta { font-size: var(--cd-text-xs); font-weight: 600; font-variant-numeric: tabular-nums; color: var(--cd-text-muted); }
.cd-metric__status { margin-top: 2px; }
.cd-metric--lg .cd-metric__value { font-size: var(--cd-text-metric-lg); }
/* Hauptkarten einer Reihe gleich hoch; Skala unten bündig */
.cd-metric--lg.cd-card { min-height: var(--cd-row-h, 320px); }
.cd-metric--lg .cd-meter { margin-top: auto; padding-top: var(--cd-space-3); }
.cd-metric--ok .cd-metric__value { color: var(--cd-ok-fg); }
.cd-metric--hinweis .cd-metric__value { color: var(--cd-hinweis-fg); }
.cd-metric--auffaellig .cd-metric__value { color: var(--cd-auffaellig-fg); }

/* ---------- Kompakte Kennzahlen-Karte ---------- */
.cd-stats { list-style: none; margin: 0 !important; padding: var(--cd-space-1) var(--cd-space-5) !important;
  background: var(--cd-surface); border: 1px solid var(--cd-border); border-radius: var(--cd-radius-lg);
  box-shadow: var(--cd-shadow-sm); }
.cd-stat { display: flex; align-items: center; justify-content: space-between; gap: var(--cd-space-4);
  padding: var(--cd-space-4) 0; border-bottom: 1px solid var(--cd-grid); }
.cd-stat:last-child { border-bottom: none; }
.cd-stat__text { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cd-stat__label { font-size: var(--cd-text-sm); font-weight: 600; color: var(--cd-text); }
.cd-stat__sub { font-size: var(--cd-text-xs); color: var(--cd-text-subtle); }
.cd-stat__value { font-family: var(--cd-font-figures); font-size: 1.5rem; font-weight: 700; letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums; white-space: nowrap; color: var(--cd-text); }
.cd-stat:first-child .cd-stat__value { color: var(--cd-dot, var(--cd-text)); }
.cd-stat__unit { font-family: var(--cd-font); font-size: var(--cd-text-sm); font-weight: 550; color: var(--cd-text-subtle); }

/* ---------- Streifen-Skala (Wert innerhalb einer Spanne) ---------- */
.cd-meter { margin-top: var(--cd-space-3); }
.cd-meter__bars { position: relative; display: flex; align-items: flex-end; gap: 2px; height: 40px; }
.cd-meter__bar { flex: 1 1 0; min-width: 2px; height: 70%; border-radius: 2px; background: var(--cd-gap); }
.cd-meter__bar--on { background: var(--cd-dot); }
.cd-meter__bar--threshold { height: 100%; background: var(--cd-text-subtle); opacity: .55; }
.cd-meter__bar--now { height: 100%; background: var(--cd-text); position: relative; }
.cd-meter__bar--now::before { content: ""; position: absolute; top: -9px; left: 50%; width: 10px; height: 10px;
  transform: translateX(-50%); border-radius: 50%; background: var(--cd-surface); border: 2.5px solid var(--cd-dot); }
.cd-meter__scale { display: flex; justify-content: space-between; gap: var(--cd-space-2); margin-top: 6px;
  font-size: 0.6875rem; color: var(--cd-text-subtle); font-variant-numeric: tabular-nums; }

/* ---------- Raumzustand (Hauptkarte) ---------- */
.cd-hero { position: relative; display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--cd-space-4);
  padding: var(--cd-space-6); min-height: var(--cd-row-h, 320px); overflow: hidden; }
.cd-hero::before { content: ""; position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--cd-hero-solid); }
.cd-hero__kicker { font-size: var(--cd-text-sm); font-weight: 550; color: var(--cd-text-muted); margin: 0; }
.cd-hero__title { font-family: var(--cd-font-display); font-size: 1.625rem; font-weight: 500; line-height: 1.15;
  letter-spacing: -0.02em; color: var(--cd-hero-fg); margin: 10px 0 0; text-wrap: balance; hyphens: auto; }
.cd-hero__text { font-size: var(--cd-text-sm); color: var(--cd-text-muted); margin: 10px 0 0; max-width: 62ch; line-height: 1.55; }
.cd-hero__meta { display: flex; flex-wrap: wrap; gap: 6px 8px; margin-top: var(--cd-space-5); }
.cd-chip { display: inline-flex; align-items: center; gap: 6px; font-size: var(--cd-text-xs); font-weight: 550;
  color: var(--cd-text-muted); background: var(--cd-surface-muted); border: 1px solid var(--cd-border);
  border-radius: var(--cd-radius-pill); padding: 4px 10px; white-space: nowrap; max-width: 100%;
  overflow: hidden; text-overflow: ellipsis; }
.cd-chip b { color: var(--cd-text); font-weight: 650; }
.cd-ring { width: 96px; height: 96px; flex: none; align-self: start; margin-top: 4px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: radial-gradient(circle, var(--cd-surface) 0 58%, transparent 59%),
    conic-gradient(var(--cd-hero-solid) 0 92%, color-mix(in srgb, var(--cd-hero-solid) 16%, #fff) 92% 100%);
  box-shadow: 0 0 0 8px color-mix(in srgb, var(--cd-hero-solid) 7%, transparent); }
.cd-ring__glyph { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center;
  font-size: 22px; font-weight: 800; color: #fff; background: var(--cd-hero-solid); }

/* ---------- Badges / Chips ---------- */
.cd-badge { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px 3px 8px; border-radius: var(--cd-radius-pill);
  font-size: var(--cd-text-xs); font-weight: 600; line-height: 1.4; white-space: nowrap; border: 1px solid transparent; }
.cd-badge__dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; flex: none; }
.cd-badge--ok { color: var(--cd-ok-fg); background: var(--cd-ok-bg); border-color: var(--cd-ok-border); }
.cd-badge--hinweis { color: var(--cd-hinweis-fg); background: var(--cd-hinweis-bg); border-color: var(--cd-hinweis-border); }
.cd-badge--auffaellig { color: var(--cd-auffaellig-fg); background: var(--cd-auffaellig-bg); border-color: var(--cd-auffaellig-border); }
.cd-badge--info { color: var(--cd-info-fg); background: var(--cd-info-bg); border-color: var(--cd-info-border); }
.cd-badge--neutral { color: var(--cd-neutral-fg); background: var(--cd-neutral-bg); border-color: var(--cd-neutral-border); }

/* ---------- Callouts (Warnungen, Hinweise, Einschränkungen) ---------- */
.cd-callout { display: flex; gap: var(--cd-space-3); padding: var(--cd-space-4) var(--cd-space-5);
  border-radius: var(--cd-radius-md); border: 1px solid; margin: var(--cd-space-2) 0; background: var(--cd-surface); }
.cd-callout__icon { flex: none; width: 28px; height: 28px; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; font-size: 13px; font-weight: 800; color: #fff; }
.cd-callout__title { font-weight: 650; font-size: var(--cd-text-base); margin: 3px 0 0; line-height: 1.4; }
.cd-callout__text { font-size: var(--cd-text-sm); margin: 4px 0 0; color: var(--cd-text-muted); line-height: 1.55; }
.cd-callout--ok { background: var(--cd-ok-bg); border-color: var(--cd-ok-border); }
.cd-callout--ok .cd-callout__icon { background: var(--cd-ok-solid); }
.cd-callout--ok .cd-callout__title { color: var(--cd-ok-fg); }
.cd-callout--hinweis { background: var(--cd-hinweis-bg); border-color: var(--cd-hinweis-border); }
.cd-callout--hinweis .cd-callout__icon { background: var(--cd-hinweis-solid); }
.cd-callout--hinweis .cd-callout__title { color: var(--cd-hinweis-fg); }
.cd-callout--auffaellig { background: var(--cd-auffaellig-bg); border-color: var(--cd-auffaellig-border);
  box-shadow: inset 4px 0 0 var(--cd-auffaellig-solid); }
.cd-callout--auffaellig .cd-callout__icon { background: var(--cd-auffaellig-solid); }
.cd-callout--auffaellig .cd-callout__title { color: var(--cd-auffaellig-fg); }
.cd-callout--info { background: var(--cd-info-bg); border-color: var(--cd-info-border); }
.cd-callout--info .cd-callout__icon { background: var(--cd-info-solid); }
.cd-callout--info .cd-callout__title { color: var(--cd-info-fg); }
.cd-callout--neutral { background: var(--cd-surface); border-color: var(--cd-border); }
.cd-callout--neutral .cd-callout__icon { background: var(--cd-neutral-solid); }

/* ---------- Leerzustand ---------- */
.cd-empty { text-align: center; padding: var(--cd-space-10) var(--cd-space-6); border-style: dashed; }
.cd-empty__icon { width: 48px; height: 48px; margin: 0 auto var(--cd-space-3); border-radius: 50%;
  background: var(--cd-primary-soft); color: var(--cd-brand); display: flex; align-items: center; justify-content: center; font-size: 20px; }
.cd-empty__title { font-family: var(--cd-font-display); font-size: var(--cd-text-xl); font-weight: 500; margin: 0; }
.cd-empty__text { font-size: var(--cd-text-sm); color: var(--cd-text-muted); margin: var(--cd-space-1) auto 0; max-width: 52ch; }

/* ---------- Datenqualität ---------- */
.cd-quality { display: flex; flex-direction: column; gap: 8px; }
.cd-quality__head { display: flex; justify-content: space-between; align-items: baseline; font-size: var(--cd-text-sm); gap: var(--cd-space-2); }
.cd-quality__label { color: var(--cd-text-muted); }
.cd-quality__value { font-weight: 650; font-variant-numeric: tabular-nums; }
.cd-quality__track { height: 6px; border-radius: var(--cd-radius-pill); background: var(--cd-gap); overflow: hidden; }
.cd-quality__fill { height: 100%; border-radius: var(--cd-radius-pill); }
.cd-quality__fill--ok { background: var(--cd-ok-solid); }
.cd-quality__fill--hinweis { background: var(--cd-hinweis-solid); }
.cd-quality__fill--auffaellig { background: var(--cd-auffaellig-solid); }
.cd-quality__note { font-size: var(--cd-text-xs); color: var(--cd-text-subtle); }

/* ---------- Schlüssel-Wert-Liste ---------- */
.cd-kv { display: grid; grid-template-columns: minmax(150px, max-content) 1fr; gap: 0 var(--cd-space-6); margin: 0;
  font-size: var(--cd-text-sm); padding: var(--cd-space-1) var(--cd-space-5); }
.cd-kv dt, .cd-kv dd { padding: 12px 0; border-bottom: 1px solid var(--cd-grid); }
.cd-kv dt:last-of-type, .cd-kv dd:last-of-type { border-bottom: none; }
.cd-kv dt { color: var(--cd-text-muted); }
.cd-kv dd { margin: 0; font-weight: 550; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; color: var(--cd-text); }

/* ---------- Befunde (Liste mit Symbol, Titel, Text, Status rechts) ---------- */
/* !important: Streamlit rückt Listen in HTML-Blöcken standardmäßig ein */
.cd-findings { list-style: none; margin: 0 !important; padding: 0 !important; display: flex; flex-direction: column;
  background: var(--cd-surface); border: 1px solid var(--cd-border); border-radius: var(--cd-radius-lg);
  box-shadow: var(--cd-shadow-sm); overflow: hidden; }
.cd-finding { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; gap: var(--cd-space-4); align-items: start;
  padding: var(--cd-space-4) var(--cd-space-5); border-bottom: 1px solid var(--cd-grid); transition: background-color .15s ease; }
.cd-finding:last-child { border-bottom: none; }
.cd-finding:hover { background: var(--cd-surface-muted); }
.cd-finding__icon { width: 36px; height: 36px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center;
  font-size: 14px; font-weight: 800; color: var(--cd-lvl-fg); background: var(--cd-lvl-bg); }
.cd-finding__category { font-size: var(--cd-text-sm); color: var(--cd-text); font-weight: 650; }
.cd-finding__text { font-size: var(--cd-text-sm); margin: 2px 0 0; color: var(--cd-text-muted); line-height: 1.5; }
.cd-finding__evidence { font-size: var(--cd-text-xs); color: var(--cd-text-subtle); margin: 4px 0 0; }
.cd-finding__badge { padding-top: 6px; }
.cd-finding--ok { --cd-lvl-fg: var(--cd-ok-fg); --cd-lvl-bg: var(--cd-ok-bg); }
.cd-finding--hinweis { --cd-lvl-fg: var(--cd-hinweis-fg); --cd-lvl-bg: var(--cd-hinweis-bg); }
.cd-finding--auffaellig { --cd-lvl-fg: var(--cd-auffaellig-fg); --cd-lvl-bg: var(--cd-auffaellig-bg); }
.cd-finding--info { --cd-lvl-fg: var(--cd-info-fg); --cd-lvl-bg: var(--cd-info-bg); }
.cd-finding--neutral { --cd-lvl-fg: var(--cd-neutral-fg); --cd-lvl-bg: var(--cd-neutral-bg); }

/* ---------- Schrittanzeige (Import-Assistent) ---------- */
.cd-steps { display: flex; list-style: none; margin: 0 0 var(--cd-space-6) !important; padding: 6px !important;
  gap: 4px; overflow-x: auto; }
.cd-step { flex: 1 1 0; min-width: 0; display: flex; align-items: center; gap: 10px; padding: 8px 12px 8px 8px;
  border-radius: var(--cd-radius-md); }
.cd-step__num { flex: none; width: 28px; height: 28px; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; font-size: var(--cd-text-xs); font-weight: 700; font-variant-numeric: tabular-nums;
  background: var(--cd-surface-muted); color: var(--cd-text-subtle); border: 1px solid var(--cd-border); }
.cd-step__text { display: flex; flex-direction: column; min-width: 0; }
.cd-step__kicker { display: none; font-size: 0.6875rem; font-weight: 600; color: var(--cd-text-subtle); white-space: nowrap; }
.cd-step__label { font-size: var(--cd-text-sm); color: var(--cd-text-muted); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font-weight: 550; }
.cd-step--done .cd-step__num { background: var(--cd-ok-bg); color: var(--cd-ok-fg); border-color: var(--cd-ok-border); }
.cd-step--current { background: var(--cd-surface-muted); flex-grow: 1.6; }
.cd-step--current .cd-step__num { background: var(--cd-ink); color: #fff; border-color: var(--cd-ink); }
.cd-step--current .cd-step__kicker { display: block; }
.cd-step--current .cd-step__label { color: var(--cd-text); font-weight: 650; }

/* ---------- Branding / Modus ---------- */
.cd-brand { display: flex; align-items: center; gap: 11px; padding: 4px 0 var(--cd-space-4); }
.cd-brand__mark { width: 38px; height: 38px; border-radius: 11px; color: #fff; flex: none; background: var(--cd-brand);
  display: flex; align-items: center; justify-content: center; box-shadow: 0 6px 14px -8px var(--cd-brand); }
.cd-brand__name { font-family: var(--cd-font-display); font-weight: 500; font-size: var(--cd-text-lg); line-height: 1.15; color: var(--cd-text); }
.cd-brand__tag { font-size: var(--cd-text-xs); font-weight: 550; color: var(--cd-text-subtle); margin-top: 1px; }
.cd-mode { display: flex; gap: 8px; align-items: flex-start; font-size: var(--cd-text-xs); line-height: 1.45;
  padding: 10px 12px; border-radius: var(--cd-radius-md); background: var(--cd-hinweis-bg); color: var(--cd-hinweis-fg);
  border: 1px solid var(--cd-hinweis-border); }
.cd-mode strong { display: block; font-size: var(--cd-text-sm); }
.cd-banner { display: inline-flex; align-items: center; gap: 8px; font-size: var(--cd-text-xs); font-weight: 550;
  padding: 5px 12px; border-radius: var(--cd-radius-pill); margin-bottom: var(--cd-space-5);
  background: var(--cd-hinweis-bg); color: var(--cd-hinweis-fg); border: 1px solid var(--cd-hinweis-border); }
.cd-banner::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--cd-hinweis-solid); }

/* ---------- Laden ---------- */
.cd-skeleton { border-radius: var(--cd-radius-md); background: linear-gradient(90deg, var(--cd-surface-muted) 25%,
  var(--cd-border) 37%, var(--cd-surface-muted) 63%); background-size: 400% 100%; animation: cd-shimmer 1.4s ease infinite; }
@keyframes cd-shimmer { 0% { background-position: 100% 50%; } 100% { background-position: 0 50%; } }

/* ---------- Bericht (öffentliche Ansicht) ---------- */
.cd-report-footer { margin-top: var(--cd-space-10); padding-top: var(--cd-space-4); border-top: 1px solid var(--cd-border);
  font-size: var(--cd-text-xs); color: var(--cd-text-subtle); }

/* ---------- Responsiv ---------- */
@media (max-width: 640px) {
  .cd-page-header__title { font-size: 1.75rem; }
  .cd-metric__value, .cd-metric--lg .cd-metric__value { font-size: 2rem; }
  .cd-card { padding: var(--cd-space-4); }
  .cd-hero { grid-template-columns: 1fr; padding: var(--cd-space-5); min-height: 0; }
  .cd-metric--lg.cd-card { min-height: 0; }
  .cd-hero__title { font-size: 1.5rem; }
  .cd-ring { width: 64px; height: 64px; position: absolute; top: var(--cd-space-4); right: var(--cd-space-4);
    box-shadow: none; }
  .cd-ring__glyph { width: 28px; height: 28px; font-size: 15px; }
  .cd-hero__kicker { padding-right: 84px; }
  .cd-kv { grid-template-columns: 1fr; padding: var(--cd-space-1) var(--cd-space-4); }
  .cd-kv dt { border-bottom: none; padding-bottom: 0; }
  .cd-kv dd { padding-top: 2px; }
  .cd-finding { grid-template-columns: auto minmax(0, 1fr); padding: var(--cd-space-4); }
  .cd-finding__badge { grid-column: 2; padding-top: 0; }
  .cd-step { flex: 0 0 auto; padding-right: 8px; }
  .cd-step__text { display: none; }
  .cd-step--current { flex: 1 1 auto; padding-right: 14px; }
  .cd-step--current .cd-step__text { display: flex; }
  .cd-context__sep { display: none; }
  .cd-empty { padding: var(--cd-space-8) var(--cd-space-4); }
}
@media (prefers-reduced-motion: reduce) {
  .cd *, .cd *::before, .cd *::after { transition: none !important; animation: none !important; }
}
@media print {
  .cd-card, .cd-finding, .cd-hero { box-shadow: none; break-inside: avoid; }
}
"""

_STREAMLIT_OVERRIDES = """
/* Grundwerte (Farben, Schrift, Rundungen) kommen aus [theme] in .streamlit/config.toml.
   Hier rein kosmetische Ergänzungen über data-testid / data-baseweb (geprüft gegen Streamlit 1.66).
   Fällt ein Selektor künftig weg, funktioniert die App unverändert weiter. */
html, body, [data-testid="stApp"] { font-family: var(--cd-font); -webkit-font-smoothing: antialiased; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { padding-top: 3rem; max-width: 1360px; }
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; padding-top: 3rem; }
}

/* Seitenleiste: weiße, ruhige Navigationsschiene */
[data-testid="stSidebar"] { background: var(--cd-surface); border-right: 1px solid var(--cd-border); }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] { padding: 9px 12px; border-radius: 12px; margin: 2px 0;
  transition: background-color .15s ease; }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"]:hover { background: var(--cd-surface-muted); }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] p { font-size: var(--cd-text-sm); font-weight: 550; color: var(--cd-text-muted); }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] [data-testid="stIconMaterial"] { color: var(--cd-text-subtle); }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { font-size: 0.6875rem; font-weight: 650;
  color: var(--cd-text-subtle); margin: 16px 0 4px 12px; letter-spacing: .06em; text-transform: uppercase; }

/* Links im Inhalt (z. B. „Ausführliche Analyse öffnen“) als dezente Pill-Buttons */
[data-testid="stMain"] [data-testid="stPageLink-NavLink"] { display: inline-flex; width: auto; padding: 7px 14px;
  border-radius: 999px; background: var(--cd-surface); border: 1px solid var(--cd-border);
  transition: border-color .15s ease, background-color .15s ease; }
[data-testid="stMain"] [data-testid="stPageLink-NavLink"]:hover { border-color: var(--cd-text-subtle); background: var(--cd-surface-muted); }
[data-testid="stMain"] [data-testid="stPageLink-NavLink"] p { font-size: var(--cd-text-sm); font-weight: 600; color: var(--cd-text); }

/* Diagramme, Aufklappbereiche, Tabellen als Karten */
[data-testid="stPlotlyChart"] { background: var(--cd-surface); border: 1px solid var(--cd-border); border-radius: var(--cd-radius-lg);
  box-shadow: var(--cd-shadow-sm); padding: 16px 18px 8px; }
[data-testid="stExpander"] details { background: var(--cd-surface); border-radius: var(--cd-radius-md) !important;
  border-color: var(--cd-border) !important; }
[data-testid="stExpander"] summary:hover { background: var(--cd-surface-muted); }
[data-testid="stExpander"] summary p { font-weight: 600; }
[data-testid="stDataFrame"] { border-radius: var(--cd-radius-md); overflow: hidden; }

/* Segment-Auswahl (Zeitraum, Messgröße …): Pill-Gruppe, Auswahl in Tiefschwarz */
[data-testid="stButtonGroup"] button { border-radius: 999px !important; min-height: 34px; padding: 4px 14px;
  transition: background-color .15s ease, border-color .15s ease; }
[data-testid="stButtonGroup"] button p { font-weight: 550; }
[data-testid="stButtonGroup"] button[aria-checked="true"] { background: var(--cd-ink) !important; border-color: var(--cd-ink) !important; }
[data-testid="stButtonGroup"] button[aria-checked="true"] p { color: #fff !important; font-weight: 600; }

/* Tabs: Pill-Reiter */
[data-testid="stTabs"] [role="tablist"] { gap: 6px; border: none; flex-wrap: wrap; }
[data-testid="stTabs"] [data-baseweb="tab-highlight"], [data-testid="stTabs"] [data-baseweb="tab-border"] { display: none; }
[data-testid="stTab"] { border-radius: 999px; padding: 7px 14px !important; height: auto; background: var(--cd-surface);
  border: 1px solid var(--cd-border); transition: background-color .15s ease, border-color .15s ease; }
[data-testid="stTab"] p { font-size: var(--cd-text-sm); font-weight: 550; color: var(--cd-text-muted); }
[data-testid="stTab"]:hover { border-color: var(--cd-text-subtle); }
[data-testid="stTab"][aria-selected="true"] { background: var(--cd-ink); border-color: var(--cd-ink); }
[data-testid="stTab"][aria-selected="true"] p { color: #fff; font-weight: 600; }
[data-testid="stTabs"] [data-baseweb="tab-panel"] { padding-top: 1.25rem; }

/* Buttons */
[data-testid^="stBaseButton-primary"] { background: var(--cd-ink) !important; border-color: var(--cd-ink) !important;
  border-radius: 12px !important; transition: background-color .15s ease, transform .1s ease; }
[data-testid^="stBaseButton-primary"]:hover { background: #2A2E37 !important; }
[data-testid^="stBaseButton-primary"]:active { transform: translateY(1px); }
[data-testid^="stBaseButton-secondary"] { background: var(--cd-surface) !important; border-color: var(--cd-border) !important;
  border-radius: 12px !important; transition: border-color .15s ease, background-color .15s ease; }
[data-testid^="stBaseButton-secondary"]:hover { border-color: var(--cd-text-subtle) !important; background: var(--cd-surface-muted) !important; }
/* Deaktiviert: hell mit lesbarer Beschriftung statt halbtransparentem Schwarz */
[data-testid^="stBaseButton-"]:disabled { background: var(--cd-gap) !important; border-color: var(--cd-gap) !important;
  cursor: not-allowed; transform: none; }
[data-testid^="stBaseButton-"]:disabled p { color: var(--cd-text-subtle) !important; }

/* Eingabefelder */
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="textarea"] { border-radius: 12px !important; }
[data-baseweb="select"] > div:focus-within, [data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within {
  border-color: var(--cd-brand) !important; box-shadow: 0 0 0 3px rgba(37,99,235,.14); }
[data-baseweb="tag"] { border-radius: 999px !important; }
[data-testid="stWidgetLabel"] p { font-weight: 600; color: var(--cd-text); }

/* Tastaturfokus gut sichtbar */
a:focus-visible, button:focus-visible, [role="tab"]:focus-visible, [tabindex]:focus-visible {
  outline: 2px solid var(--cd-brand) !important; outline-offset: 2px; }

/* Datei-Upload */
[data-testid="stFileUploaderDropzone"] { background: var(--cd-surface); border: 1.5px dashed var(--cd-border-strong);
  border-radius: var(--cd-radius-lg); transition: border-color .15s ease; }
[data-testid="stFileUploaderDropzone"]:hover { border-color: var(--cd-brand); }

/* Fortschritt */
[data-testid="stProgress"] [role="progressbar"] > div > div { border-radius: 999px; }

@media (prefers-reduced-motion: reduce) {
  [data-testid="stApp"] *, [data-testid="stApp"] *::before, [data-testid="stApp"] *::after {
    transition: none !important; animation: none !important; }
}
"""

_REPORT_OVERRIDES = """
/* Öffentliche Berichtsansicht: keine Sidebar, kein Sidebar-Aufklapper, schmale Lesebreite */
[data-testid="stSidebar"], [data-testid="stExpandSidebarButton"] { display: none !important; }
[data-testid="stMainBlockContainer"] { max-width: 1040px; }
/* Kopfleiste (Toolbar/Deploy) im Bericht ausblenden */
[data-testid="stHeader"], [data-testid="stAppHeader"] { display: none; }
"""


def build_css(variant: Literal["app", "report"] = "app") -> str:
    """Komplettes CSS (ohne ``<style>``-Tag) – separat für Tests und PDF/HTML-Export."""
    parts = [css_variables(), _COMPONENT_CSS, _STREAMLIT_OVERRIDES]
    if variant == "report":
        parts.append(_REPORT_OVERRIDES)
    return "\n".join(parts)


def nav_active_css(url_path: str) -> str:
    """CSS, das den Navigationslink der aktuellen Seite hervorhebt.

    Streamlit markiert den aktiven ``st.page_link`` nicht über ein stabiles Attribut; der Link
    lässt sich aber über sein ``href`` (= ``url_path`` der Seite, Startseite = leer) eindeutig
    ansprechen. Rein kosmetisch – die Navigation selbst bleibt unverändert."""
    href = "".join(ch for ch in str(url_path or "") if ch.isalnum() or ch in "-_")
    sel = f'[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"][href="{href}"]'
    return (
        f"{sel} {{ background: {COLORS['bg']}; box-shadow: inset 3px 0 0 {COLORS['brand']}; }}\n"
        f"{sel}:hover {{ background: {COLORS['bg']}; }}\n"
        f"{sel} p {{ color: {COLORS['text']} !important; font-weight: 650; }}\n"
        f'{sel} [data-testid="stIconMaterial"] {{ color: {COLORS["brand"]} !important; }}'
    )


def inject_css(variant: Literal["app", "report"] = "app") -> None:
    """Injiziert das globale CSS. Muss bei jedem Skriptlauf einmal aufgerufen werden
    (``app.py`` erledigt das). Reine ``<style>``-Blöcke nehmen in Streamlit keinen Platz ein."""
    import streamlit as st

    st.html(f"<style>{build_css(variant)}</style>")


# ---------------------------------------------------------------------------
# Plotly
# ---------------------------------------------------------------------------
def build_plotly_template():
    import plotly.graph_objects as go

    axis = {
        "showgrid": True,
        "gridcolor": COLORS["grid"],
        "gridwidth": 1,
        "zeroline": False,
        "showline": True,
        "linecolor": COLORS["axis"],
        "linewidth": 1,
        "ticks": "",
        "ticklen": 0,
        "tickfont": {"size": 11, "color": COLORS["text_subtle"]},
        "title": {"font": {"size": 11, "color": COLORS["text_subtle"]}, "standoff": 10},
        "automargin": True,
    }
    layout = go.Layout(
        font={"family": FONT_FAMILY, "size": 12, "color": COLORS["text"]},
        separators=",.",  # Dezimalkomma, Tausenderpunkt
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(255,255,255,0)",  # transparent: die weiße Diagrammkarte ist der Hintergrund
        colorway=list(FALLBACK_SERIES),
        title={
            "font": {"family": FONT_FAMILY, "size": 15, "color": COLORS["text"], "weight": 600},
            "x": 0,
            "xanchor": "left",
            "pad": {"l": 4},
        },
        margin={"l": 8, "r": 16, "t": 48, "b": 8},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
            "font": {"size": 12, "color": COLORS["text_muted"]},
            "bgcolor": "rgba(0,0,0,0)",
            "itemclick": "toggle",
            "itemdoubleclick": "toggleothers",
        },
        hovermode="x unified",
        hoverlabel={
            "bgcolor": "rgba(255,255,255,0.94)",
            "bordercolor": COLORS["border_strong"],
            "font": {"family": FONT_FAMILY, "size": 12, "color": COLORS["text"]},
            "align": "left",
        },
        xaxis=axis,
        yaxis=axis,
        bargap=0.25,
        modebar={
            "bgcolor": "rgba(0,0,0,0)",
            "color": COLORS["text_subtle"],
            "activecolor": COLORS["primary"],
        },
    )
    return go.layout.Template(layout=layout)


def apply_plotly_template(set_default: bool = True) -> str:
    """Registriert das Template ``climate`` (idempotent) und setzt es optional als Standard.

    Diagramme müssen mit ``st.plotly_chart(fig, theme=None)`` ausgegeben werden, sonst
    überschreibt Streamlit das Template (siehe ``components.render_chart``).
    """
    import plotly.io as pio

    if PLOTLY_TEMPLATE_NAME not in pio.templates:
        pio.templates[PLOTLY_TEMPLATE_NAME] = build_plotly_template()
    if set_default:
        pio.templates.default = f"plotly_white+{PLOTLY_TEMPLATE_NAME}"
    return PLOTLY_TEMPLATE_NAME

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
* **Gestaltung (Glas-Design, Apple-Stil):** weicher Farbverlauf als Hintergrund, milchige
  Glasflächen mit Unschärfe (``backdrop-filter``) und Lichtkante, große Ziffern in der
  Systemschrift (SF Pro / Segoe UI Variable), Segment-Auswahl und Tabs im iOS-Stil,
  Pill-Buttons, getönte Warnflächen. Markenblau, Inhalte und Funktionen unverändert.

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
    "bg": "#EEF3FA",  # = backgroundColor in config.toml (Grundton unter dem Farbverlauf)
    "surface": "#FFFFFF",
    "surface_muted": "#F6F8FC",  # innere Kacheln, Tabellenköpfe
    "border": "#E3E8EF",
    "border_strong": "#CDD5E0",
    "text": "#0B1C30",  # = textColor in config.toml
    "text_muted": "#46566B",
    "text_subtle": "#738196",
    "grid": "#EEF2F6",
    "axis": "#D5DCE5",
    "primary": "#2563EB",  # = primaryColor in config.toml (Markenblau, unverändert)
    "primary_hover": "#1D4ED8",
    "primary_soft": "#EEF3FE",
    "gap": "#E4E9F0",  # Datenlücken
}

# Status-Stufen: fg = Text/Symbol, bg = Fläche, border = Rahmen, solid = Diagrammfarbe
STATUS: dict[str, dict[str, str]] = {
    "ok": {"fg": "#276749", "bg": "#EEF7F1", "border": "#CDE6D6", "solid": "#3F8F64", "label": "Unauffällig"},
    "hinweis": {
        "fg": "#92560B",
        "bg": "#FEF7EA",
        "border": "#F5DFB3",
        "solid": "#D99A2B",
        "label": "Hinweis",
    },
    "auffaellig": {
        "fg": "#A23B32",
        "bg": "#FCF0EF",
        "border": "#F1CFCB",
        "solid": "#C2554B",
        "label": "Auffällig",
    },
    "info": {"fg": "#1D4ED8", "bg": "#EFF4FF", "border": "#CFDCFB", "solid": "#3B6FE0", "label": "Info"},
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


# Messgrößen (Okabe-Ito); dash = Linienstil als zweites, farbunabhängiges Merkmal
METRIC_COLORS: dict[str, str] = {
    "temperature": "#D55E00",  # Zinnoberrot
    "humidity": "#0072B2",  # Blau
    "dew_point": "#009E73",  # Blaugrün
    "vpd": "#CC79A7",  # Rotviolett
    "abs_humidity": "#3A93C9",  # Himmelblau (abgedunkelt für Kontrast auf Weiß)
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
    '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable Text", "Segoe UI", Inter, Roboto, '
    '"Helvetica Neue", Arial, sans-serif'
)
# Überschriften und große Kennzahlen: optisch für große Grade optimierte Varianten, falls vorhanden.
FONT_DISPLAY = (
    '-apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI Variable Display", "Segoe UI", Inter, '
    'Roboto, "Helvetica Neue", Arial, sans-serif'
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
    "2xl": "2.25rem",
    "metric": "2.125rem",
    "metric-lg": "3rem",
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
# Glas-Design (Apple-Stil): weichere Rundungen, schwebende Flächen.
RADIUS: dict[str, str] = {"sm": "8px", "md": "14px", "lg": "20px", "pill": "999px"}
SHADOW: dict[str, str] = {
    "sm": "0 1px 2px rgba(16,32,64,0.04)",
    "md": "0 1px 2px rgba(16,32,64,0.04), 0 6px 18px -8px rgba(16,32,64,0.10)",
}

GLASS: dict[str, str] = {
    "fill": "rgba(255,255,255,0.58)",
    "border": "rgba(255,255,255,0.75)",
    "blur": "blur(24px) saturate(180%)",
    "shadow": "inset 0 1px 0 rgba(255,255,255,0.8), 0 10px 30px -12px rgba(30,60,120,0.22), "
    "0 2px 6px rgba(30,60,120,0.05)",
    "hairline": "rgba(15,40,80,0.08)",
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
    # Glas: getönte Statusflächen und milchige Flächen. --cd-surface zeigt in der Oberfläche
    # ebenfalls Glas (Diagramme/PDF verwenden weiterhin die festen Werte aus COLORS).
    for level, tokens in STATUS.items():
        lines.append(f"--cd-{level}-tint: {hex_to_rgba(tokens['solid'], 0.14)};")
        lines.append(f"--cd-{level}-tint-strong: {hex_to_rgba(tokens['solid'], 0.24)};")
    lines += [
        f"--cd-glass: {GLASS['fill']};",
        f"--cd-glass-border: {GLASS['border']};",
        f"--cd-blur: {GLASS['blur']};",
        f"--cd-shadow-glass: {GLASS['shadow']};",
        f"--cd-hairline: {GLASS['hairline']};",
        "--cd-surface: var(--cd-glass);",
    ]
    return ":root {\n  " + "\n  ".join(lines) + "\n}"


_COMPONENT_CSS = """
/* =====================================================================
   Glas-Designsystem (Apple-Stil): milchige, unscharfe Flächen über einem
   weichen Farbverlauf. Alle Glasflächen teilen eine Regel (siehe unten).
   ===================================================================== */

/* ---------- Basis ---------- */
.cd { font-family: var(--cd-font); color: var(--cd-text); line-height: 1.5; -webkit-font-smoothing: antialiased; }
.cd *, .cd *::before, .cd *::after { box-sizing: border-box; }
.cd-muted { color: var(--cd-text-muted); }
.cd-subtle { color: var(--cd-text-subtle); }
.cd-num { font-variant-numeric: tabular-nums; }
.cd-label { font-size: var(--cd-text-xs); font-weight: 600; color: var(--cd-text-subtle); line-height: 1.3; letter-spacing: -0.005em; }

/* ---------- Gemeinsame Glasfläche ---------- */
.cd-card, .cd-context, .cd-kv, .cd-steps, .cd-finding, .cd-empty {
  background: var(--cd-glass);
  -webkit-backdrop-filter: var(--cd-blur); backdrop-filter: var(--cd-blur);
  border: 1px solid var(--cd-glass-border);
  box-shadow: var(--cd-shadow-glass);
}

/* ---------- Seitenkopf ---------- */
.cd-page-header { display: flex; flex-wrap: wrap; align-items: flex-end; justify-content: space-between;
  gap: var(--cd-space-4); padding: 0 0 var(--cd-space-6); }
.cd-page-header__eyebrow { font-size: var(--cd-text-sm); font-weight: 600; color: var(--cd-primary); margin: 0 0 4px;
  letter-spacing: -0.005em; }
.cd-page-header__title { font-family: var(--cd-font-display); font-size: var(--cd-text-2xl); font-weight: 700;
  letter-spacing: -0.035em; line-height: 1.08; margin: 0; color: var(--cd-text); }
.cd-page-header__subtitle { font-size: var(--cd-text-md); color: var(--cd-text-muted); margin: var(--cd-space-2) 0 0;
  max-width: 68ch; letter-spacing: -0.01em; }
.cd-page-header__actions { display: flex; gap: var(--cd-space-2); flex-wrap: wrap; }

/* ---------- Abschnittskopf ---------- */
.cd-section { margin: var(--cd-space-10) 0 var(--cd-space-3); }
.cd-section__title { font-family: var(--cd-font-display); font-size: var(--cd-text-xl); font-weight: 700; margin: 0;
  letter-spacing: -0.025em; color: var(--cd-text); }
.cd-section__subtitle { font-size: var(--cd-text-sm); color: var(--cd-text-muted); margin: 4px 0 0; max-width: 80ch; }

/* ---------- Kontextleiste ---------- */
.cd-context { display: flex; flex-wrap: wrap; align-items: center; gap: var(--cd-space-3) var(--cd-space-8);
  border-radius: var(--cd-radius-lg); padding: var(--cd-space-4) var(--cd-space-6); }
.cd-context__item { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.cd-context__value { font-weight: 600; font-size: var(--cd-text-base); color: var(--cd-text); overflow-wrap: anywhere;
  letter-spacing: -0.01em; }
.cd-context__value--lg { font-family: var(--cd-font-display); font-size: var(--cd-text-xl); font-weight: 700;
  letter-spacing: -0.03em; }
.cd-context__sep { width: 1px; align-self: stretch; background: var(--cd-hairline); }

/* ---------- Karten & Kennzahlen ---------- */
.cd-card { border-radius: var(--cd-radius-lg); padding: var(--cd-space-5) var(--cd-space-6); }
.cd-metric-grid { display: grid; gap: var(--cd-space-4);
  grid-template-columns: repeat(auto-fit, minmax(min(100%, var(--cd-grid-min, 190px)), 1fr)); }
.cd-metric { position: relative; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.cd-metric__label { font-size: var(--cd-text-sm); color: var(--cd-text-muted); font-weight: 600; letter-spacing: -0.005em;
  display: flex; align-items: center; justify-content: space-between; gap: var(--cd-space-2); min-height: 22px; }
.cd-metric__label > span:first-child { display: inline-flex; align-items: flex-start; gap: 8px; min-width: 0; }
.cd-metric__dot { width: 10px; height: 10px; border-radius: 50%; flex: none; margin-top: 4px;
  background: var(--cd-dot, var(--cd-border-strong)); box-shadow: 0 0 0 3px color-mix(in srgb, var(--cd-dot, #94A3B8) 18%, transparent); }
.cd-metric__value { font-family: var(--cd-font-display); font-size: var(--cd-text-metric); font-weight: 700;
  letter-spacing: -0.04em; line-height: 1.05; font-variant-numeric: tabular-nums; color: var(--cd-text);
  overflow-wrap: anywhere; }
.cd-metric__unit { font-family: var(--cd-font); font-size: var(--cd-text-lg); font-weight: 500; letter-spacing: -0.01em;
  color: var(--cd-text-subtle); margin-left: 2px; }
.cd-metric__sub { font-size: var(--cd-text-xs); color: var(--cd-text-subtle); line-height: 1.45; }
.cd-metric__delta { font-size: var(--cd-text-xs); font-weight: 600; font-variant-numeric: tabular-nums; color: var(--cd-text-muted); }
.cd-metric--lg .cd-metric__value { font-size: var(--cd-text-metric-lg); }
.cd-metric--lg .cd-metric__unit { font-size: var(--cd-text-xl); }
/* Status: zartes, getöntes Glas (zusätzlich zum Text-Badge) */
.cd-metric--ok { background: linear-gradient(180deg, var(--cd-ok-tint), transparent 70%), var(--cd-glass); }
.cd-metric--hinweis { background: linear-gradient(180deg, var(--cd-hinweis-tint), transparent 70%), var(--cd-glass); }
.cd-metric--auffaellig { background: linear-gradient(180deg, var(--cd-auffaellig-tint), transparent 70%), var(--cd-glass); }
.cd-metric--info { background: linear-gradient(180deg, var(--cd-info-tint), transparent 70%), var(--cd-glass); }

/* ---------- Badges ---------- */
.cd-badge { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px 3px 8px; border-radius: var(--cd-radius-pill);
  font-size: var(--cd-text-xs); font-weight: 600; line-height: 1.4; white-space: nowrap; border: 1px solid transparent;
  letter-spacing: -0.005em; }
.cd-badge__dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; flex: none; }
.cd-badge--ok { color: var(--cd-ok-fg); background: var(--cd-ok-tint); border-color: var(--cd-ok-tint); }
.cd-badge--hinweis { color: var(--cd-hinweis-fg); background: var(--cd-hinweis-tint); border-color: var(--cd-hinweis-tint); }
.cd-badge--auffaellig { color: var(--cd-auffaellig-fg); background: var(--cd-auffaellig-tint); border-color: var(--cd-auffaellig-tint); }
.cd-badge--info { color: var(--cd-info-fg); background: var(--cd-info-tint); border-color: var(--cd-info-tint); }
.cd-badge--neutral { color: var(--cd-neutral-fg); background: rgba(118,118,128,.12); border-color: transparent; }

/* ---------- Callouts (Warnungen, Hinweise, Einschränkungen): getöntes Glas ---------- */
.cd-callout { display: flex; gap: var(--cd-space-4); padding: var(--cd-space-4) var(--cd-space-5);
  border-radius: var(--cd-radius-lg); border: 1px solid var(--cd-glass-border); margin: var(--cd-space-2) 0;
  -webkit-backdrop-filter: var(--cd-blur); backdrop-filter: var(--cd-blur); box-shadow: var(--cd-shadow-glass); }
.cd-callout__icon { flex: none; width: 26px; height: 26px; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; font-size: 13px; font-weight: 800; color: #fff; margin-top: 0; }
.cd-callout__title { font-weight: 650; font-size: var(--cd-text-base); margin: 2px 0 0; line-height: 1.4; letter-spacing: -0.01em; }
.cd-callout__text { font-size: var(--cd-text-sm); margin: 4px 0 0; color: var(--cd-text-muted); line-height: 1.55; }
.cd-callout--ok { background: linear-gradient(135deg, var(--cd-ok-tint), var(--cd-glass) 80%); }
.cd-callout--ok .cd-callout__icon { background: var(--cd-ok-solid); }
.cd-callout--ok .cd-callout__title { color: var(--cd-ok-fg); }
.cd-callout--hinweis { background: linear-gradient(135deg, var(--cd-hinweis-tint), var(--cd-glass) 80%); }
.cd-callout--hinweis .cd-callout__icon { background: var(--cd-hinweis-solid); }
.cd-callout--hinweis .cd-callout__title { color: var(--cd-hinweis-fg); }
.cd-callout--auffaellig { background: linear-gradient(135deg, var(--cd-auffaellig-tint-strong), var(--cd-glass) 85%);
  border-color: var(--cd-auffaellig-tint-strong); }
.cd-callout--auffaellig .cd-callout__icon { background: var(--cd-auffaellig-solid); box-shadow: 0 4px 12px -4px var(--cd-auffaellig-solid); }
.cd-callout--auffaellig .cd-callout__title { color: var(--cd-auffaellig-fg); }
.cd-callout--info { background: linear-gradient(135deg, var(--cd-info-tint), var(--cd-glass) 80%); }
.cd-callout--info .cd-callout__icon { background: var(--cd-info-solid); }
.cd-callout--info .cd-callout__title { color: var(--cd-info-fg); }
.cd-callout--neutral { background: var(--cd-glass); }
.cd-callout--neutral .cd-callout__icon { background: var(--cd-neutral-solid); }

/* ---------- Leerzustand ---------- */
.cd-empty { text-align: center; padding: var(--cd-space-10) var(--cd-space-6); border-radius: var(--cd-radius-lg); }
.cd-empty__icon { width: 52px; height: 52px; margin: 0 auto var(--cd-space-3); border-radius: 16px;
  background: linear-gradient(135deg, var(--cd-primary), #6A8BFF); color: #fff; display: flex; align-items: center;
  justify-content: center; font-size: 22px; box-shadow: 0 10px 24px -10px var(--cd-primary); }
.cd-empty__title { font-family: var(--cd-font-display); font-size: var(--cd-text-lg); font-weight: 700; margin: 0; letter-spacing: -0.02em; }
.cd-empty__text { font-size: var(--cd-text-sm); color: var(--cd-text-muted); margin: var(--cd-space-1) auto 0; max-width: 52ch; }

/* ---------- Datenqualität ---------- */
.cd-quality { display: flex; flex-direction: column; gap: 8px; }
.cd-quality__head { display: flex; justify-content: space-between; align-items: baseline; font-size: var(--cd-text-sm); gap: var(--cd-space-2); }
.cd-quality__label { color: var(--cd-text-muted); }
.cd-quality__value { font-weight: 650; font-variant-numeric: tabular-nums; }
.cd-quality__track { height: 8px; border-radius: var(--cd-radius-pill); background: rgba(118,118,128,.14); overflow: hidden; }
.cd-quality__fill { height: 100%; border-radius: var(--cd-radius-pill); }
.cd-quality__fill--ok { background: linear-gradient(90deg, #4BC47D, var(--cd-ok-solid)); }
.cd-quality__fill--hinweis { background: linear-gradient(90deg, #F5C451, var(--cd-hinweis-solid)); }
.cd-quality__fill--auffaellig { background: linear-gradient(90deg, #F2837A, var(--cd-auffaellig-solid)); }
.cd-quality__note { font-size: var(--cd-text-xs); color: var(--cd-text-subtle); }

/* ---------- Schlüssel-Wert-Liste (gruppierte Liste im iOS-Stil) ---------- */
.cd-kv { display: grid; grid-template-columns: minmax(140px, max-content) 1fr; gap: 0 var(--cd-space-6); margin: 0;
  font-size: var(--cd-text-sm); border-radius: var(--cd-radius-lg); padding: var(--cd-space-1) var(--cd-space-5); }
.cd-kv dt, .cd-kv dd { padding: 11px 0; border-bottom: 1px solid var(--cd-hairline); }
.cd-kv dt:last-of-type, .cd-kv dd:last-of-type { border-bottom: none; }
.cd-kv dt { color: var(--cd-text-muted); }
.cd-kv dd { margin: 0; font-weight: 550; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; color: var(--cd-text); }

/* ---------- Befunde ---------- */
/* !important: Streamlit rückt Listen in HTML-Blöcken standardmäßig ein */
.cd-findings { list-style: none; margin: 0 !important; padding: 0 !important; display: flex; flex-direction: column; gap: var(--cd-space-2); }
.cd-finding { display: grid; grid-template-columns: auto 1fr; gap: var(--cd-space-3); align-items: start;
  padding: var(--cd-space-4) var(--cd-space-5); border-radius: var(--cd-radius-md); }
.cd-finding__category { font-size: var(--cd-text-xs); color: var(--cd-text-subtle); font-weight: 600; letter-spacing: -0.005em; }
.cd-finding__text { font-size: var(--cd-text-base); margin: 2px 0 0; color: var(--cd-text); letter-spacing: -0.01em; }
.cd-finding__evidence { font-size: var(--cd-text-sm); color: var(--cd-text-muted); margin: 4px 0 0; }

/* ---------- Schrittanzeige (Import-Assistent) ---------- */
.cd-steps { display: flex; list-style: none; margin: 0 0 var(--cd-space-6) !important; padding: 6px !important;
  gap: 4px; border-radius: var(--cd-radius-pill); overflow-x: auto; }
.cd-step { flex: 1 1 0; min-width: 0; display: flex; align-items: center; gap: 9px; padding: 7px 12px 7px 7px;
  border-radius: var(--cd-radius-pill); }
.cd-step__num { flex: none; width: 28px; height: 28px; border-radius: 50%; display: inline-flex; align-items: center;
  justify-content: center; font-size: var(--cd-text-xs); font-weight: 700; font-variant-numeric: tabular-nums;
  background: rgba(118,118,128,.12); color: var(--cd-text-subtle); }
.cd-step__text { display: flex; flex-direction: column; min-width: 0; }
.cd-step__kicker { display: none; font-size: 0.6875rem; font-weight: 600; color: rgba(255,255,255,.85); white-space: nowrap; }
.cd-step__label { font-size: var(--cd-text-sm); color: var(--cd-text-muted); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font-weight: 550; }
.cd-step--done .cd-step__num { background: var(--cd-ok-tint); color: var(--cd-ok-fg); }
.cd-step--current { background: linear-gradient(135deg, var(--cd-primary), #5B7CFF); flex-grow: 1.6;
  box-shadow: 0 8px 20px -10px var(--cd-primary); }
.cd-step--current .cd-step__num { background: rgba(255,255,255,.22); color: #fff; }
.cd-step--current .cd-step__kicker { display: block; }
.cd-step--current .cd-step__label { color: #fff; font-weight: 650; }

/* ---------- Modus-Hinweis / Branding ---------- */
.cd-brand { display: flex; align-items: center; gap: 12px; padding: 2px 0 var(--cd-space-4); }
.cd-brand__mark { width: 40px; height: 40px; border-radius: 12px; color: #fff; flex: none;
  background: linear-gradient(145deg, #4C82FF, var(--cd-primary) 60%, #1E40AF);
  display: flex; align-items: center; justify-content: center;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.35), 0 8px 18px -8px var(--cd-primary); }
.cd-brand__name { font-family: var(--cd-font-display); font-weight: 700; font-size: var(--cd-text-md); line-height: 1.2;
  letter-spacing: -0.02em; color: var(--cd-text); }
.cd-brand__tag { font-size: var(--cd-text-xs); font-weight: 500; color: var(--cd-text-subtle); margin-top: 1px; }
.cd-mode { display: flex; gap: 8px; align-items: flex-start; font-size: var(--cd-text-xs); line-height: 1.45;
  padding: 10px 12px; border-radius: var(--cd-radius-md); color: var(--cd-hinweis-fg);
  background: var(--cd-hinweis-tint); border: 1px solid var(--cd-glass-border); }
.cd-mode strong { display: block; font-size: var(--cd-text-sm); }
.cd-banner { display: inline-flex; align-items: center; gap: 8px; font-size: var(--cd-text-xs); font-weight: 550;
  padding: 6px 14px; border-radius: var(--cd-radius-pill); margin-bottom: var(--cd-space-5); color: var(--cd-hinweis-fg);
  background: var(--cd-glass); -webkit-backdrop-filter: var(--cd-blur); backdrop-filter: var(--cd-blur);
  border: 1px solid var(--cd-glass-border); box-shadow: var(--cd-shadow-glass); }
.cd-banner::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--cd-hinweis-solid); }

/* ---------- Lade-/Fortschrittshilfen ---------- */
.cd-skeleton { border-radius: var(--cd-radius-md); background: linear-gradient(90deg, rgba(255,255,255,.35) 25%,
  rgba(255,255,255,.7) 37%, rgba(255,255,255,.35) 63%); background-size: 400% 100%; animation: cd-shimmer 1.4s ease infinite; }
@keyframes cd-shimmer { 0% { background-position: 100% 50%; } 100% { background-position: 0 50%; } }
@media (prefers-reduced-motion: reduce) { .cd-skeleton { animation: none; } }

/* ---------- Bericht (öffentliche Ansicht) ---------- */
.cd-report-footer { margin-top: var(--cd-space-10); padding-top: var(--cd-space-4); border-top: 1px solid var(--cd-hairline);
  font-size: var(--cd-text-xs); color: var(--cd-text-subtle); }

/* ---------- Responsiv ---------- */
@media (max-width: 640px) {
  .cd-page-header__title { font-size: 1.75rem; }
  .cd-metric__value, .cd-metric--lg .cd-metric__value { font-size: 1.875rem; }
  .cd-card { padding: var(--cd-space-4) var(--cd-space-5); }
  .cd-kv { grid-template-columns: 1fr; padding: var(--cd-space-1) var(--cd-space-4); }
  .cd-kv dt { border-bottom: none; padding-bottom: 0; }
  .cd-kv dd { padding-top: 2px; }
  .cd-steps { border-radius: var(--cd-radius-lg); }
  .cd-step { flex: 0 0 auto; padding-right: 7px; }
  .cd-step__text { display: none; }
  .cd-step--current { flex: 1 1 auto; padding-right: 14px; }
  .cd-step--current .cd-step__text { display: flex; }
  .cd-context { padding: var(--cd-space-4); gap: var(--cd-space-3) var(--cd-space-5); }
  .cd-context__sep { display: none; }
  .cd-finding { grid-template-columns: 1fr; gap: var(--cd-space-2); }
  .cd-finding .cd-badge { justify-self: start; }
  .cd-empty { padding: var(--cd-space-8) var(--cd-space-4); }
}
/* Browser ohne backdrop-filter: deckendere Flächen, damit Text lesbar bleibt */
@supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  :root { --cd-glass: rgba(255,255,255,.92); }
}
@media print {
  .cd-card, .cd-finding, .cd-callout { box-shadow: none; backdrop-filter: none; -webkit-backdrop-filter: none; background: #fff; }
}
"""

_STREAMLIT_OVERRIDES = """
/* Grundwerte (Schrift, Rundungen, Primärfarbe) kommen aus [theme] in .streamlit/config.toml.
   Hier die Glas-Gestaltung der Streamlit-Elemente über data-testid / data-baseweb (geprüft gegen
   Streamlit 1.66). Rein kosmetisch – fällt ein Selektor weg, funktioniert die App unverändert. */

/* Hintergrund: weicher, feststehender Farbverlauf hinter allen Glasflächen */
[data-testid="stApp"] {
  background:
    radial-gradient(1100px 800px at -8% -12%, rgba(84,140,255,.62), transparent 62%),
    radial-gradient(950px 750px at 108% -5%, rgba(160,128,255,.52), transparent 62%),
    radial-gradient(1000px 820px at 70% 112%, rgba(40,200,215,.46), transparent 62%),
    radial-gradient(800px 700px at -10% 100%, rgba(255,150,200,.30), transparent 60%),
    radial-gradient(700px 520px at 42% 48%, rgba(255,255,255,.45), transparent 72%),
    var(--cd-bg);
  background-attachment: fixed;
}
html, body, [data-testid="stApp"] { font-family: var(--cd-font); -webkit-font-smoothing: antialiased; }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] { padding-top: 3.25rem; max-width: 1320px; }
@media (max-width: 640px) {
  [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; padding-top: 3rem; }
}

/* Seitenleiste als Glas */
[data-testid="stSidebar"] { background: rgba(255,255,255,.52) !important;
  -webkit-backdrop-filter: blur(30px) saturate(180%); backdrop-filter: blur(30px) saturate(180%);
  border-right: 1px solid var(--cd-glass-border) !important; }
[data-testid="stSidebar"] > div, [data-testid="stSidebarContent"] { background: transparent !important; }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] { padding: 8px 12px; border-radius: 10px; margin: 1px 0;
  transition: background-color .15s ease; }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"]:hover { background: rgba(118,118,128,.10); }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] p { font-size: var(--cd-text-sm); font-weight: 550;
  color: var(--cd-text); letter-spacing: -0.01em; }
[data-testid="stSidebar"] [data-testid="stPageLink-NavLink"] [data-testid="stIconMaterial"] { color: var(--cd-primary); }
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p { font-size: var(--cd-text-xs); font-weight: 600;
  color: var(--cd-text-subtle); margin: 16px 0 4px 12px; letter-spacing: -0.005em; }

/* Glasflächen für Diagramme, Aufklappbereiche, Tabellen */
[data-testid="stPlotlyChart"], [data-testid="stExpander"] details, [data-testid="stDataFrame"] {
  background: var(--cd-glass); -webkit-backdrop-filter: var(--cd-blur); backdrop-filter: var(--cd-blur);
  border: 1px solid var(--cd-glass-border) !important; border-radius: var(--cd-radius-lg);
  box-shadow: var(--cd-shadow-glass); }
[data-testid="stPlotlyChart"] { padding: 14px 16px 6px; }
[data-testid="stDataFrame"] { padding: 4px; overflow: hidden; }
[data-testid="stExpander"] summary { border-radius: var(--cd-radius-lg); }
[data-testid="stExpander"] summary:hover { background: rgba(255,255,255,.35); }
[data-testid="stExpander"] summary p { font-weight: 600; letter-spacing: -0.01em; }

/* Segment-Auswahl (Zeitraum, Messgröße …) im iOS-Stil: graue Schiene, weißes Segment */
[data-testid="stButtonGroup"] > div { background: rgba(118,118,128,.14); border-radius: 12px; padding: 3px;
  gap: 2px; width: fit-content; max-width: 100%; flex-wrap: wrap; }
[data-testid="stButtonGroup"] button { background: transparent !important; border: none !important; border-radius: 9px !important;
  box-shadow: none !important; min-height: 32px; padding: 4px 14px; }
[data-testid="stButtonGroup"] button p { font-weight: 550; color: var(--cd-text) !important; letter-spacing: -0.01em; }
[data-testid="stButtonGroup"] button:hover { background: rgba(255,255,255,.45) !important; }
[data-testid="stButtonGroup"] button[aria-checked="true"] { background: #fff !important;
  box-shadow: 0 3px 8px rgba(0,0,0,.10), 0 1px 1px rgba(0,0,0,.05) !important; }
[data-testid="stButtonGroup"] button[aria-checked="true"] p { font-weight: 650; }

/* Tabs als Segment-Leiste */
[data-testid="stTabs"] [role="tablist"] { gap: 2px; background: rgba(118,118,128,.14); border-radius: 12px; padding: 3px;
  width: fit-content; max-width: 100%; border: none; }
[data-testid="stTabs"] [data-baseweb="tab-highlight"], [data-testid="stTabs"] [data-baseweb="tab-border"] { display: none; }
[data-testid="stTab"] { border-radius: 9px; padding: 6px 14px !important; height: auto; background: transparent; }
[data-testid="stTab"] p { font-size: var(--cd-text-sm); font-weight: 550; color: var(--cd-text); letter-spacing: -0.01em; }
[data-testid="stTab"]:hover { background: rgba(255,255,255,.45); }
[data-testid="stTab"][aria-selected="true"] { background: #fff; box-shadow: 0 3px 8px rgba(0,0,0,.10), 0 1px 1px rgba(0,0,0,.05); }
[data-testid="stTab"][aria-selected="true"] p { font-weight: 650; color: var(--cd-text); }
[data-testid="stTabs"] [data-baseweb="tab-panel"] { padding-top: 1.25rem; }

/* Buttons: Pillenform; primär als leuchtendes Blau, sekundär als Glas */
[data-testid^="stBaseButton-primary"] { border-radius: 999px !important; border: none !important;
  background: linear-gradient(180deg, #4C82FF, var(--cd-primary)) !important;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.3), 0 6px 16px -8px var(--cd-primary) !important; padding-inline: 18px !important; }
[data-testid^="stBaseButton-primary"]:hover { filter: brightness(1.06); }
[data-testid^="stBaseButton-secondary"] { border-radius: 999px !important; background: rgba(255,255,255,.6) !important;
  -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
  border: 1px solid var(--cd-glass-border) !important; box-shadow: 0 1px 2px rgba(16,32,64,.06) !important;
  padding-inline: 18px !important; }
[data-testid^="stBaseButton-secondary"]:hover { background: rgba(255,255,255,.85) !important; }
[data-testid^="stBaseButton-primary"]:disabled, [data-testid^="stBaseButton-secondary"]:disabled { opacity: .45; filter: none; }

/* Eingabefelder: halbtransparentes Weiß mit feinem Rand */
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="base-input"],
[data-testid="stNumberInputContainer"], [data-testid="stDateInputField"] {
  background: rgba(255,255,255,.66) !important; border-color: var(--cd-glass-border) !important; }
[data-baseweb="select"] > div:focus-within, [data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within {
  border-color: var(--cd-primary) !important; box-shadow: 0 0 0 3px rgba(37,99,235,.18); }
[data-baseweb="tag"] { border-radius: 999px !important; }
[data-testid="stWidgetLabel"] p { font-weight: 600; color: var(--cd-text); letter-spacing: -0.01em; }

/* Datei-Upload */
[data-testid="stFileUploaderDropzone"] { background: rgba(255,255,255,.5); -webkit-backdrop-filter: var(--cd-blur);
  backdrop-filter: var(--cd-blur); border: 1.5px dashed rgba(37,99,235,.35); border-radius: var(--cd-radius-lg); }

/* Fortschritt */
[data-testid="stProgress"] [role="progressbar"] > div > div { border-radius: 999px; }

/* Code-/Linkfeld (Freigabelink) */
[data-testid="stCode"] pre { background: rgba(255,255,255,.7) !important; border: 1px solid var(--cd-glass-border); border-radius: 12px; }
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
        f"{sel} {{ background: {hex_to_rgba(COLORS['primary'], 0.14)}; }}\n"
        f"{sel}:hover {{ background: {hex_to_rgba(COLORS['primary'], 0.20)}; }}\n"
        f"{sel} p {{ color: {COLORS['primary']} !important; font-weight: 650; }}"
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
        "gridcolor": "rgba(15,40,80,0.07)",
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
        plot_bgcolor="rgba(255,255,255,0)",  # transparent: das Glas der Diagrammkarte scheint durch
        colorway=list(FALLBACK_SERIES),
        title={
            "font": {"family": FONT_DISPLAY, "size": 15, "color": COLORS["text"], "weight": 600},
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

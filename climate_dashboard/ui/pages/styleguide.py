"""Styleguide – zeigt alle Komponenten und Diagrammtypen mit synthetischen Demo-Daten.

Nur im lokalen Entwicklungsmodus erreichbar (siehe ``app.py``).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from climate_dashboard.domain.models import DEW_POINT, HUMIDITY, TEMPERATURE, TS
from climate_dashboard.ui import charts, demo_data, theme
from climate_dashboard.ui import components as ui
from climate_dashboard.ui.formatting import (
    fmt_date_range,
    fmt_datetime,
    fmt_delta,
    fmt_duration,
    fmt_metric,
    fmt_number,
    fmt_percent,
)


@st.cache_data(show_spinner=False)
def _demo() -> pd.DataFrame:
    return demo_data.make_demo_measurements()


def _slice(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    s = pd.Timestamp(start, tz="Europe/Berlin")
    e = pd.Timestamp(end, tz="Europe/Berlin")
    return df[(df[TS] >= s) & (df[TS] < e)]


def _swatches(colors: dict[str, str]) -> str:
    items = "".join(
        f'<div style="display:flex;align-items:center;gap:8px;font-size:12px">'
        f'<span style="width:22px;height:22px;border-radius:6px;background:{c};border:1px solid rgba(15,23,42,.08)"></span>'
        f"<span><b>{ui.esc(k)}</b><br><code>{c}</code></span></div>"
        for k, c in colors.items()
    )
    return f'<div class="cd" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px">{items}</div>'


def render() -> None:
    ui.page_header(
        "Styleguide",
        "Alle Bausteine des Designsystems mit synthetischen Demo-Daten. Nur im lokalen Modus sichtbar.",
        eyebrow="Entwicklung",
    )
    df = _demo()

    tab_base, tab_comp, tab_charts = st.tabs(["Grundlagen", "Komponenten", "Diagramme"])

    with tab_base:
        ui.section_header(
            "Farben", "Neutral (Slate), Primär, Status und Messgrößen (Okabe-Ito, farbenblind-freundlich)."
        )
        st.html(_swatches({**{f"neutral-{k}": v for k, v in theme.NEUTRAL.items() if k in (50, 200, 500, 900)},
                           "primary": theme.COLORS["primary"]}))  # fmt: skip
        st.html(_swatches({lvl: t["solid"] for lvl, t in theme.STATUS.items()}))
        st.html(_swatches(theme.METRIC_COLORS))
        st.html(_swatches({theme.HUMIDITY_BAND_LABELS[k]: v for k, v in theme.HUMIDITY_BAND_COLORS.items()}))

        ui.section_header("Formatierung", "Deutsche Zahlen- und Datumsformate, locale-unabhängig.")
        ui.key_value_list(
            {
                "fmt_number(1234.5, 1)": fmt_number(1234.5, 1),
                "fmt_metric(21.44, temperature)": fmt_metric(21.44, TEMPERATURE),
                "fmt_metric(57.2, humidity)": fmt_metric(57.2, HUMIDITY),
                "fmt_delta(1.23, temperature)": fmt_delta(1.23, TEMPERATURE),
                "fmt_delta(-3, humidity)": fmt_delta(-3, HUMIDITY),
                "fmt_percent(0.964, 1, ratio=True)": fmt_percent(0.964, 1, ratio=True),
                "fmt_duration(12000)": fmt_duration(12000),
                "fmt_duration(187200)": fmt_duration(187200),
                "fmt_datetime(…)": fmt_datetime(pd.Timestamp("2026-10-22T19:45Z")),
                "fmt_date_range(…)": fmt_date_range(
                    pd.Timestamp("2026-03-01T00:00Z"), pd.Timestamp("2026-10-22T12:00Z")
                ),
                "fehlender Wert": fmt_metric(None, TEMPERATURE),
            }
        )

    with tab_comp:
        ui.section_header(
            "Kennzahlen", "metric_grid – responsives Raster, bricht auf dem Smartphone einspaltig um."
        )
        ui.metric_grid(
            [
                {"label": "Ø Temperatur", "value": fmt_number(20.84, 1), "unit": "°C", "sublabel": "zeitgewichtet",
                 "delta": f"{fmt_delta(0.6, TEMPERATURE)} ggü. Vormonat"},
                {"label": "Ø Rel. Feuchte", "value": 58, "unit": "%", "decimals": 0, "sublabel": "Max 78 % am 03.02.",
                 "status": "hinweis"},
                {"label": "Zeit ≥ 70 % rF", "value": fmt_duration(4.5 * 3600), "sublabel": "4 Phasen", "status": "auffaellig"},
                {"label": "Zeitabdeckung", "value": fmt_number(96.4, 1), "unit": "%", "status": "ok"},
            ]
        )  # fmt: skip
        ui.section_header("Status-Badges")
        st.html(
            " ".join(ui.status_badge_html(lvl) for lvl in ("ok", "hinweis", "auffaellig", "info", "neutral"))
        )

        ui.section_header("Callouts")
        ui.callout(
            "info",
            "Hinweis zur Methode",
            "Anteile werden über die Zeit zwischen Messungen berechnet, nicht über die Anzahl.",
        )
        ui.callout(
            "hinweis",
            "Einschränkung",
            "Aus Raumluftdaten lässt sich nicht ableiten, ob tatsächlich Schimmel vorhanden ist.",
        )
        ui.callout(
            "auffaellig",
            "Längere feuchte Phase",
            "Vom 02.02. bis 05.02.2026 lag die relative Feuchte durchgehend über 60 %.",
        )
        ui.callout("ok", "Daten vollständig", "Keine Lücken im gewählten Zeitraum.")

        ui.section_header("Befunde", "finding_list – Raumnamen und Texte werden immer HTML-maskiert.")
        ui.finding_list(demo_data.demo_findings())

        left, right = st.columns(2, gap="large")
        with left:
            ui.section_header("Datenqualität")
            ui.data_quality_bar(96.4, note="2 Lücken, längste 2 Tage 11 Std.")
            ui.data_quality_bar(78.0, label="Messvollständigkeit")
            ui.data_quality_bar(41.5, label="Zeitabdeckung März")
        with right:
            ui.section_header("Schlüssel-Wert-Liste")
            ui.key_value_list({"Raum": "Bad <EG>", "Sensor": "Hygrometer 2", "Zeitraum": "01.03.–22.10.2026",
                               "Messungen": 33120, "Intervall": "10 Min."})  # fmt: skip

        ui.section_header("Schrittanzeige", "step_indicator – für den Import-Assistenten.")
        ui.step_indicator(["Datei", "Raum & Sensor", "Prüfung", "Konflikte", "Import"], 2)

        ui.section_header("Leerzustand")
        if ui.empty_state("Noch keine Daten", "Importiere einen SwitchBot-CSV-Export, um loszulegen.",
                          "CSV importieren", key="sg_empty"):  # fmt: skip
            st.toast("Aktion ausgelöst")

        ui.section_header("Laden")
        st.html(ui.skeleton_html(56, rows=2))

    with tab_charts:
        settings_thresholds = charts.default_thresholds()
        ui.section_header(
            "Zeitverlauf", "Geteilte x-Achse, Lücken sichtbar, Min/Max-erhaltendes Downsampling."
        )
        fig = charts.timeseries_chart(
            df,
            [TEMPERATURE, DEW_POINT, HUMIDITY],
            bands=[{"start": pd.Timestamp("2026-02-02", tz="Europe/Berlin"), "end": pd.Timestamp("2026-02-05", tz="Europe/Berlin"),
                    "label": "Feuchte Phase", "level": "hinweis"}],
            thresholds=settings_thresholds,
            range_selector=True,
        )  # fmt: skip
        ui.render_chart(fig, key="sg_ts")
        meta = fig.layout.meta or {}
        if meta.get("downsampled_from"):
            st.caption(
                f"Anzeige vereinfacht: {fmt_number(meta['points_shown'])} von {fmt_number(meta['downsampled_from'])} "
                "Messpunkten (Min/Max je Zeitfenster bleiben erhalten). Kennzahlen nutzen alle Messwerte."
            )

        c1, c2 = st.columns(2, gap="large")
        with c1:
            ui.section_header("Zeitanteile in Feuchtebereichen")
            ui.render_chart(charts.band_share_bar(demo_data.demo_band_shares(), labels={
                "dry": "Trocken < 35 %", "normal": "35–60 %", "elevated": "Erhöht 60–70 %", "high": "Hoch ≥ 70 %"}),
                key="sg_bands")  # fmt: skip
        with c2:
            ui.section_header("Tag/Nacht")
            ui.render_chart(charts.day_night_chart(demo_data.demo_day_night(df)), key="sg_dn")

        c1, c2 = st.columns(2, gap="large")
        with c1:
            ui.section_header("Monatsübersicht", "Unvollständige Monate schraffiert, Mittelwert hohl.")
            ui.render_chart(charts.monthly_chart(demo_data.demo_monthly(df)), key="sg_month")
        with c2:
            ui.section_header("Jahreszeiten")
            ui.render_chart(
                charts.seasonal_chart(demo_data.demo_seasonal(df, TEMPERATURE), TEMPERATURE), key="sg_season"
            )

        ui.section_header("Vergleich zweier Zeiträume")
        a, b = _slice(df, "2026-01-05", "2026-01-19"), _slice(df, "2026-02-01", "2026-02-15")
        labels = ("05.01.–18.01.2026", "01.02.–14.02.2026")
        mode = st.segmented_control(
            "Darstellung", ["Verlauf", "Tagesmittel"], default="Verlauf", key="sg_cmp_mode"
        )
        ui.render_chart(
            charts.comparison_chart(
                a, b, HUMIDITY, labels=labels, mode="daily" if mode == "Tagesmittel" else "overlay"
            ),
            key="sg_cmp",
        )

        ui.section_header("Heatmap Stunde × Tag", "Leere Felder = keine Messung (nicht aufgefüllt).")
        ui.render_chart(
            charts.heatmap_hour_by_day(_slice(df, "2026-01-01", "2026-02-15"), HUMIDITY), key="sg_heat"
        )

        ui.section_header("Phasen-Zeitleiste")
        ui.render_chart(
            charts.episode_timeline(demo_data.demo_episodes(), period=(pd.Timestamp("2026-02-01T00:00Z"),
                                                                      pd.Timestamp("2026-02-14T00:00Z"))),
            key="sg_episodes",
        )  # fmt: skip

        ui.section_header("Abstand Temperatur – Taupunkt")
        ui.render_chart(
            charts.dew_point_spread_chart(_slice(df, "2026-01-20", "2026-02-10"), surface_offset=4.0),
            key="sg_spread",
        )

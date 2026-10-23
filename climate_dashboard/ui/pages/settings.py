"""Seite „Einstellungen“ – Schwellenwerte der Analyse, Speichermodus, Abmelden, Dokumentation.

Berichte speichern ihre Einstellungen zum Erstellungszeitpunkt im Snapshot; Änderungen hier
wirken nur auf neue Auswertungen und neue Berichtsversionen.
"""

from __future__ import annotations

from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from climate_dashboard.config import PROJECT_ROOT
from climate_dashboard.domain.models import AnalysisSettings
from climate_dashboard.ui import components as ui
from climate_dashboard.ui import selectors as sel
from climate_dashboard.ui import state

FLASH_KEY = "settings_flash"
FORM_VERSION_KEY = "settings_form_version"  # erzwingt neue Widget-Werte nach „Standard wiederherstellen“
DOCS = ("FORMELN.md", "ARCHITECTURE.md")


def validate(s: AnalysisSettings) -> list[str]:
    """Fehlertexte für unplausible Kombinationen (leer = gültig)."""
    errors = []
    if not s.humidity_low < s.humidity_elevated < s.humidity_high:
        errors.append("Es muss gelten: trocken < erhöht < hoch.")
    if s.night_start_hour == s.night_end_hour:
        errors.append("Nachtbeginn und Nachtende dürfen nicht gleich sein.")
    try:
        ZoneInfo(s.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"Unbekannte Zeitzone „{s.timezone}“.")
    return errors


def _form(current: AnalysisSettings) -> AnalysisSettings | None:
    v = st.session_state.get(FORM_VERSION_KEY, 0)
    with st.form(f"settings_form_{v}", border=False):
        ui.section_header("Luftfeuchtigkeit", "Bereiche für Zeitanteile und Phasen (relative Feuchte in %).")
        c1, c2, c3 = st.columns(3)
        low = c1.number_input("Trocken unter (%)", 10.0, 60.0, float(current.humidity_low), 1.0, key=f"set_low_{v}")
        elevated = c2.number_input("Erhöht ab (%)", 40.0, 90.0, float(current.humidity_elevated), 1.0,
                                   key=f"set_el_{v}")  # fmt: skip
        high = c3.number_input("Hoch ab (%)", 45.0, 99.0, float(current.humidity_high), 1.0, key=f"set_hi_{v}")
        st.caption("Orientierungswerte, keine Grenzwerte. Üblich sind 40–60 % rF; dauerhaft über 60–70 % gilt als "
                   "feuchtebegünstigend.")  # fmt: skip

        ui.section_header("Feuchtephasen")
        c1, c2 = st.columns(2)
        min_ep = c1.number_input("Anhaltend ab (Minuten)", 10, 1440, int(current.min_episode_minutes), 10,
                                 key=f"set_minep_{v}", help="Kürzere Phasen gelten als kurze Ausschläge.")  # fmt: skip
        merge = c2.number_input("Kurze Unterbrechungen überbrücken bis (Minuten)", 0, 240,
                                int(current.episode_merge_gap_minutes), 5, key=f"set_merge_{v}",
                                help="Sinkt die Feuchte nur kurz unter die Schwelle, bleibt es eine Phase.")  # fmt: skip

        ui.section_header("Taupunkt")
        spread = st.number_input("Hinweis bei Abstand Raumluft – Taupunkt unter (K)", 0.5, 15.0,
                                 float(current.dew_point_spread_warn), 0.5, key=f"set_spread_{v}")  # fmt: skip
        st.caption("Eine angenommene Oberflächentemperatur wird nicht hier, sondern bewusst je Analyse als "
                   "ANNAHME eingestellt (Analyse → Taupunkt & Kondensation).")  # fmt: skip

        ui.section_header("Tag und Nacht")
        c1, c2 = st.columns(2)
        night_start = c1.number_input("Nacht beginnt um (Uhr)", 0, 23, int(current.night_start_hour), 1,
                                      key=f"set_ns_{v}")  # fmt: skip
        night_end = c2.number_input("Nacht endet um (Uhr)", 0, 23, int(current.night_end_hour), 1, key=f"set_ne_{v}")

        ui.section_header("Datenlücken")
        c1, c2 = st.columns(2)
        gap_factor = c1.number_input("Lücke ab Vielfachem des Messintervalls", 1.5, 20.0, float(current.gap_factor),
                                     0.5, key=f"set_gf_{v}")  # fmt: skip
        gap_min = c2.number_input("… mindestens jedoch (Minuten)", 10, 1440, int(current.min_gap_minutes), 10,
                                  key=f"set_gm_{v}")  # fmt: skip
        st.caption("Lückengrenze = max(Faktor × erwartetes Intervall, Mindestminuten). Längere Abstände zählen "
                   "als Datenlücke – weder als auffällig noch als unauffällig.")  # fmt: skip

        ui.section_header("Zeitzone")
        tz = st.text_input("Zeitzone für Tage, Monate und Tag/Nacht", current.timezone, key=f"set_tz_{v}")

        submitted = st.form_submit_button("Einstellungen speichern", type="primary")
    if not submitted:
        return None
    return AnalysisSettings(
        humidity_elevated=float(elevated), humidity_high=float(high), humidity_low=float(low),
        min_episode_minutes=int(min_ep), episode_merge_gap_minutes=int(merge),
        dew_point_spread_warn=float(spread), surface_offset_k=current.surface_offset_k,
        night_start_hour=int(night_start), night_end_hour=int(night_end), gap_factor=float(gap_factor),
        min_gap_minutes=int(gap_min), timezone=tz.strip(),
    )  # fmt: skip


def _storage() -> None:
    app = state.settings()
    ui.section_header("Speicherung und Anmeldung")
    if app.is_local:
        ui.key_value_list({"Modus": "Lokal (SQLite, nur dieser Rechner)", "Datei": str(app.sqlite_path)})
        ui.callout("hinweis", "Lokaler Entwicklungsmodus",
                   "Daten liegen nur in der lokalen Datei. Für dauerhafte Online-Speicherung Supabase einrichten "
                   "(docs/SUPABASE_SETUP.md).")  # fmt: skip
        return
    session = st.session_state.get(state.AUTH_SESSION_KEY)
    host = urlparse(app.supabase_url or "").hostname or "–"
    ui.key_value_list(
        {"Modus": "Supabase (online)", "Projekt": host, "Angemeldet als": getattr(session, "email", None) or "–"}
    )
    st.caption("Zugangsschlüssel werden nicht angezeigt.")
    if st.button("Abmelden", key="settings_logout"):
        try:
            state.auth().sign_out(session)
        finally:
            st.session_state.pop(state.AUTH_SESSION_KEY, None)
            state.invalidate_data()
        st.rerun()


def _docs() -> None:
    ui.section_header("Dokumentation", "Wie gerechnet und eingeschätzt wird.")
    for name in DOCS:
        path = PROJECT_ROOT / "docs" / name
        if not path.is_file():
            continue
        with st.expander(f"docs/{name}"):
            st.markdown(path.read_text(encoding="utf-8"))


def _body() -> None:
    ui.page_header("Einstellungen", "Schwellenwerte, Zeitzone und Speicherung.", eyebrow="System")
    msg = st.session_state.pop(FLASH_KEY, None)
    if msg:
        ui.callout("ok", msg)
    ui.callout(
        "info",
        "Berichte bleiben unverändert",
        "Bereits erstellte Berichtsversionen behalten die Einstellungen ihres Erstellungszeitpunkts. Änderungen "
        "wirken auf Analysen, Vergleiche und neue Berichtsversionen.",
    )
    current = state.analysis_settings()
    new = _form(current)
    if new is not None:
        errors = validate(new)
        if errors:
            ui.callout("hinweis", "Nicht gespeichert", " ".join(errors))
        else:
            state.save_analysis_settings(new)
            st.session_state[FLASH_KEY] = "Einstellungen gespeichert."
            st.rerun()
    if st.button("Standard wiederherstellen", key="settings_reset"):
        defaults = AnalysisSettings(timezone=state.settings().default_timezone)
        state.save_analysis_settings(defaults)
        st.session_state[FORM_VERSION_KEY] = st.session_state.get(FORM_VERSION_KEY, 0) + 1
        st.session_state[FLASH_KEY] = "Standardwerte wiederhergestellt."
        st.rerun()
    _storage()
    _docs()


def render() -> None:
    sel.guarded(_body)

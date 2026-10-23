"""Anmeldeseite (Supabase-Modus).

Vertrag mit ``app.py``: Nach erfolgreicher Anmeldung liegt die ``AuthSession`` unter
``st.session_state[AUTH_SESSION_KEY]``; danach ``st.rerun()``. Das Passwort wird weder
gespeichert, noch geloggt, noch angezeigt (das Formular wird nach dem Absenden geleert).
"""

from __future__ import annotations

import logging

import streamlit as st

from climate_dashboard.ui import components as ui
from climate_dashboard.ui import state

AUTH_SESSION_KEY = state.AUTH_SESSION_KEY


def render() -> None:
    ui.page_header("Anmelden", "Bitte mit E-Mail-Adresse und Passwort anmelden.", eyebrow="SwitchBot Climate Dashboard")
    try:
        app = state.settings()
    except Exception:  # noqa: BLE001 - Konfigurationsfehler verständlich anzeigen
        ui.callout("auffaellig", "Konfiguration fehlerhaft", "Die App-Konfiguration konnte nicht gelesen werden.")
        return
    if app.is_local:
        ui.callout("info", "Keine Anmeldung nötig", "Im lokalen Entwicklungsmodus gibt es nur einen Nutzer.")
        return

    with st.form("login_form", clear_on_submit=True):
        email = st.text_input("E-Mail-Adresse", key="login_email", autocomplete="email")
        password = st.text_input("Passwort", type="password", key="login_password", autocomplete="current-password")
        submitted = st.form_submit_button("Anmelden", type="primary", width="stretch")
    st.caption("Ein Konto wird ausschließlich vom Administrator angelegt; eine Selbstregistrierung gibt es nicht.")
    if not submitted:
        return
    from climate_dashboard.auth.session import AuthError

    try:
        session = state.auth().sign_in(email, password)
    except AuthError as exc:
        ui.callout("hinweis", "Anmeldung nicht möglich", str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - keine Details anzeigen (könnten Eingaben enthalten)
        # Nur den Fehlertyp protokollieren (für „Manage app“ → Protokoll), keine Eingaben.
        logging.getLogger(__name__).warning("Anmeldung: unerwarteter Fehler %s", type(exc).__name__)
        ui.callout("auffaellig", "Anmeldung fehlgeschlagen", "Der Anmeldedienst ist nicht erreichbar. Bitte später erneut versuchen.")
        return
    finally:
        password = None  # noqa: F841 - Referenz nicht länger halten
    st.session_state[AUTH_SESSION_KEY] = session
    state.invalidate_data()
    st.rerun()

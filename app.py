"""Einstieg der Streamlit-App.

Routing
-------
* ``?r=<token>`` → öffentliche Berichtsansicht (ohne Sidebar, ohne Navigation, ohne Login).
* sonst → interne App mit Navigation. Im Supabase-Modus ist eine Anmeldung nötig; im
  lokalen Entwicklungsmodus gibt es einen Einzelnutzer ohne Login.

Navigation: ``st.navigation(position="hidden")`` + eigene Links (``st.page_link``) in der
Sidebar. So ist die Navigation unabhängig von ``client.showSidebarNavigation`` in
``config.toml`` und kann gruppiert und mit Branding/Modus-Hinweis gestaltet werden.
"""

from __future__ import annotations

import streamlit as st

_token = st.query_params.get("r")

st.set_page_config(
    page_title="SwitchBot Climate Dashboard",
    page_icon=":material/humidity_mid:",
    layout="wide",
    initial_sidebar_state="collapsed" if _token else "auto",
    menu_items={
        "Get help": None,
        "Report a bug": None,
        "About": "SwitchBot Climate Dashboard – Raumklima-Auswertung",
    },
)

from climate_dashboard.config import ConfigError, load_settings  # noqa: E402
from climate_dashboard.ui import components as ui  # noqa: E402
from climate_dashboard.ui import theme  # noqa: E402
from climate_dashboard.ui.pages import (  # noqa: E402
    analysis,
    compare,
    import_wizard,
    login,
    overview,
    public_report,
    reports,
    rooms,
    settings,
    styleguide,
)


def _public_report(token: str) -> None:
    theme.inject_css("report")
    public_report.render(token)


def _build_pages(is_local: bool) -> dict[str, list[st.Page]]:
    sections: dict[str, list[st.Page]] = {
        "": [
            st.Page(
                overview.render,
                title="Übersicht",
                icon=":material/dashboard:",
                url_path="uebersicht",
                default=True,
            )
        ],
        "Daten": [
            st.Page(import_wizard.render, title="Import", icon=":material/upload_file:", url_path="import"),
            st.Page(rooms.render, title="Räume & Sensoren", icon=":material/sensors:", url_path="raeume"),
        ],
        "Auswertung": [
            st.Page(analysis.render, title="Analyse", icon=":material/monitoring:", url_path="analyse"),
            st.Page(
                compare.render, title="Vergleich", icon=":material/compare_arrows:", url_path="vergleich"
            ),
            st.Page(
                reports.render,
                title="Berichte & Freigaben",
                icon=":material/description:",
                url_path="berichte",
            ),
        ],
        "System": [
            st.Page(
                settings.render, title="Einstellungen", icon=":material/settings:", url_path="einstellungen"
            )
        ],
    }
    if is_local:
        sections["System"].append(
            st.Page(styleguide.render, title="Styleguide", icon=":material/palette:", url_path="styleguide")
        )
    return sections


def _sidebar(sections: dict[str, list[st.Page]], mode: str, current: st.Page) -> None:
    # Aktive Seite hervorheben: Die Startseite hat im Link ein leeres href, alle anderen ihren url_path.
    default_page = sections[""][0]
    href = "" if current.url_path == default_page.url_path else current.url_path
    st.html(f"<style>{theme.nav_active_css(href)}</style>")
    with st.sidebar:
        st.html(ui.brand_html())
        notice = ui.mode_notice_html(mode)
        if notice:
            st.html(notice)
        for title, pages in sections.items():
            if title:
                st.caption(title)
            for page in pages:
                st.page_link(page, width="stretch")


def main() -> None:
    theme.apply_plotly_template()

    if _token:
        _public_report(str(_token))
        return

    theme.inject_css("app")
    try:
        app_settings = load_settings()
    except ConfigError as exc:
        ui.callout("auffaellig", "Konfiguration fehlerhaft", str(exc))
        st.stop()

    if not app_settings.is_local and st.session_state.get(login.AUTH_SESSION_KEY) is None:
        login.render()
        return

    sections = _build_pages(app_settings.is_local)
    page = st.navigation(sections, position="hidden")
    _sidebar(sections, app_settings.mode, page)
    if app_settings.is_local:
        st.html(
            '<div class="cd"><div class="cd-banner">Lokaler Entwicklungsmodus – Daten werden nur lokal gespeichert.</div></div>'
        )
    page.run()


main()

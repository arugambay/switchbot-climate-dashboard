"""Gemeinsamer Zustand der internen Seiten: Konfiguration, Anmeldung, Repository, Daten-Cache.

Alle Seiten holen Repository und Daten ausschließlich hierüber, damit Caching und
Cache-Invalidierung (nach Importen/Änderungen) an einer Stelle geregelt sind.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from climate_dashboard.auth.session import AuthSession, LocalAuth, SupabaseAuth
from climate_dashboard.config import Settings, load_settings
from climate_dashboard.domain.models import AnalysisSettings
from climate_dashboard.storage.base import Repository
from climate_dashboard.storage.factory import get_repository

AUTH_SESSION_KEY = "auth_session"
_DATA_VERSION_KEY = "_data_version"
_SELECTED_ROOM_KEY = "selected_room_id"


def settings() -> Settings:
    return load_settings()


def auth():
    """``LocalAuth`` im lokalen Modus, sonst ``SupabaseAuth``."""
    app_settings = settings()
    return LocalAuth() if app_settings.is_local else SupabaseAuth.from_settings(app_settings)


def current_session() -> AuthSession | None:
    app_settings = settings()
    if app_settings.is_local:
        return LocalAuth().current_session()
    session: AuthSession | None = st.session_state.get(AUTH_SESSION_KEY)
    if session is not None:
        session = auth().ensure_fresh(session)
        st.session_state[AUTH_SESSION_KEY] = session
    return session


def repo() -> Repository:
    return get_repository(settings(), current_session())


def user_key() -> str:
    session = current_session()
    return session.user_id if session else "anonymous"


# ---------------------------------------------------------------------------
# Cache-Invalidierung
# ---------------------------------------------------------------------------
def data_version() -> int:
    return int(st.session_state.get(_DATA_VERSION_KEY, 0))


def invalidate_data() -> None:
    """Nach Import, Konfliktauflösung, Umbenennung, Löschung usw. aufrufen."""
    st.session_state[_DATA_VERSION_KEY] = data_version() + 1
    st.cache_data.clear()


@st.cache_data(show_spinner=False, max_entries=32, ttl=3600)
def _cached_measurements(
    _repo: Any, user: str, sensor_ids: tuple[str, ...], start: datetime | None, end: datetime | None, version: int
) -> pd.DataFrame:
    return _repo.load_measurements(list(sensor_ids), start, end)


def load_measurements(
    sensor_ids: list[str], start: datetime | None = None, end: datetime | None = None
) -> pd.DataFrame:
    """Messungen (zeitlich begrenzt) mit Cache pro Nutzer, Sensoren, Zeitraum und Datenstand."""
    return _cached_measurements(repo(), user_key(), tuple(sorted(sensor_ids)), start, end, data_version())


# ---------------------------------------------------------------------------
# Einstellungen und Auswahl
# ---------------------------------------------------------------------------
def analysis_settings() -> AnalysisSettings:
    stored = repo().get_settings() or {}
    result = AnalysisSettings.from_dict(stored.get("analysis"))
    if "analysis" not in stored or "timezone" not in (stored.get("analysis") or {}):
        result.timezone = settings().default_timezone
    return result


def save_analysis_settings(value: AnalysisSettings) -> None:
    current = repo().get_settings() or {}
    current["analysis"] = value.to_dict()
    repo().save_settings(current)
    invalidate_data()


def selected_room_id() -> str | None:
    return st.session_state.get(_SELECTED_ROOM_KEY)


def set_selected_room_id(room_id: str | None) -> None:
    st.session_state[_SELECTED_ROOM_KEY] = room_id

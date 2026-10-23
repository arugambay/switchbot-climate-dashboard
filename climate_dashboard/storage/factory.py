"""Auswahl der Repository-Implementierung.

* Modus ``local``  → ``SqliteRepository(settings.sqlite_path)`` (Ordner wird angelegt).
* Modus ``supabase`` → ``SupabaseRepository`` mit anon-Key + JWT des angemeldeten Nutzers,
  sodass Row Level Security greift. Ein Service-Role-Key wird nie verwendet und aktiv
  abgelehnt (``assert_not_service_key``).

Verwendung in der Oberfläche::

    settings = load_settings()
    session = st.session_state.get("auth_session")      # AuthSession oder None
    repo = get_repository(settings, session)             # interne App
    public = get_public_repository(settings)             # nur für ?r=<token>
"""

from __future__ import annotations

import base64
import json
import threading
from pathlib import Path
from typing import Any

from climate_dashboard.config import ConfigError, Settings
from climate_dashboard.storage.base import Repository, RepositoryError

_SQLITE_CACHE: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


class NotAuthenticatedError(RepositoryError):
    """Im Supabase-Modus ist ohne Anmeldung kein Datenzugriff möglich."""


def _jwt_role(key: str) -> str | None:
    parts = key.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except (ValueError, json.JSONDecodeError, AttributeError):
        return None


def assert_not_service_key(key: str | None) -> None:
    """Lehnt Service-Role-/Secret-Schlüssel ab (alt: JWT mit role=service_role,
    neu: ``sb_secret_…``). Erlaubt sind nur anon-/publishable-Schlüssel."""
    if not key:
        return
    if key.startswith("sb_secret_") or _jwt_role(key) == "service_role":
        raise ConfigError(
            "Als SUPABASE_ANON_KEY ist ein geheimer Service-Role-Schlüssel eingetragen. "
            "Bitte den öffentlichen 'anon'/'publishable'-Schlüssel verwenden."
        )


def create_supabase_client(settings: Settings, access_token: str | None = None) -> Any:
    """Supabase-Client mit anon-Key. Mit ``access_token`` laufen alle Datenbankanfragen
    im Namen dieses Nutzers (RLS). Keine Sitzungs-Persistenz, kein Auto-Refresh im
    Hintergrund (Streamlit-Server teilt Prozesse zwischen Besuchern)."""
    if not settings.supabase_url or not settings.supabase_anon_key:
        raise ConfigError("SUPABASE_URL und SUPABASE_ANON_KEY müssen gesetzt sein.")
    assert_not_service_key(settings.supabase_anon_key)

    from supabase.lib.client_options import SyncClientOptions

    from supabase import create_client

    headers: dict[str, str] = {}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    options = SyncClientOptions(headers=headers, auto_refresh_token=False, persist_session=False)
    return create_client(settings.supabase_url, settings.supabase_anon_key, options=options)


def _sqlite_repository(path: Path | str) -> Any:
    from climate_dashboard.storage.sqlite_repo import SqliteRepository

    key = str(path)
    with _CACHE_LOCK:
        repo = _SQLITE_CACHE.get(key)
        if repo is None:
            if key != ":memory:":
                Path(key).parent.mkdir(parents=True, exist_ok=True)
            repo = SqliteRepository(key)
            _SQLITE_CACHE[key] = repo
        return repo


def clear_cache() -> None:
    """Schließt gecachte SQLite-Verbindungen (Tests)."""
    with _CACHE_LOCK:
        for repo in _SQLITE_CACHE.values():
            repo.close()
        _SQLITE_CACHE.clear()


def get_repository(settings: Settings, auth_session: Any = None) -> Repository:
    """Repository für die interne (angemeldete) App."""
    if settings.mode == "local":
        return _sqlite_repository(settings.sqlite_path)

    from climate_dashboard.storage.supabase_repo import SupabaseRepository

    if auth_session is None or not getattr(auth_session, "access_token", None):
        raise NotAuthenticatedError("Bitte zuerst anmelden.")
    client = create_supabase_client(settings, access_token=auth_session.access_token)
    return SupabaseRepository(client, user_id=auth_session.user_id)


def get_public_repository(settings: Settings) -> Any:
    """Repository für die öffentliche Berichtsansicht – nur ``resolve_share`` /
    ``resolve_share_token`` verwenden. Im Supabase-Modus ohne Nutzer-JWT (Rolle anon):
    RLS verweigert jeden Tabellenzugriff, nur die RPC ``get_shared_report`` ist erlaubt."""
    if settings.mode == "local":
        return _sqlite_repository(settings.sqlite_path)

    from climate_dashboard.storage.supabase_repo import SupabaseRepository

    return SupabaseRepository(create_supabase_client(settings), user_id=None)

"""Zentrale Konfiguration.

Werte werden in dieser Reihenfolge gesucht:
1. Streamlit-Secrets (``st.secrets``) – auf Streamlit Community Cloud.
2. Umgebungsvariablen bzw. eine lokale ``.env``-Datei.

Fehlen Supabase-Zugangsdaten, läuft die App im lokalen Entwicklungsmodus mit einer
SQLite-Datei. Dieser Modus ist ausdrücklich nicht für dauerhafte Online-Speicherung gedacht.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "dev.sqlite"

# Schlüssel, die niemals in der App-Konfiguration auftauchen dürfen.
FORBIDDEN_KEYS = ("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY", "SERVICE_ROLE_KEY")


class ConfigError(RuntimeError):
    """Fehlerhafte oder unsichere Konfiguration."""


@dataclass(frozen=True)
class Settings:
    supabase_url: str | None
    supabase_anon_key: str | None
    app_base_url: str | None
    default_timezone: str = "Europe/Berlin"
    sqlite_path: Path = DEFAULT_SQLITE_PATH

    @property
    def mode(self) -> str:
        """``"supabase"`` wenn Zugangsdaten vorhanden sind, sonst ``"local"``."""
        return "supabase" if self.supabase_url and self.supabase_anon_key else "local"

    @property
    def is_local(self) -> bool:
        return self.mode == "local"


def _read_streamlit_secrets() -> dict[str, str]:
    try:
        import streamlit as st

        return {k: str(v) for k, v in st.secrets.items() if not isinstance(v, dict)}
    except Exception:  # keine secrets.toml vorhanden oder kein Streamlit-Kontext
        return {}


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Lädt die Konfiguration. ``env`` erlaubt Tests ohne echte Umgebung."""
    if env is None:
        try:
            from dotenv import load_dotenv

            load_dotenv(PROJECT_ROOT / ".env", override=False)
        except ImportError:
            pass
        env = {**os.environ, **_read_streamlit_secrets()}

    for key in FORBIDDEN_KEYS:
        if env.get(key):
            raise ConfigError(
                f"{key} ist gesetzt. Der Service-Role-Schlüssel darf nicht in der App-Konfiguration "
                "stehen. Bitte entfernen – die App benötigt ihn nicht."
            )

    def get(name: str) -> str | None:
        value = (env.get(name) or "").strip()
        return value or None

    sqlite_path = get("SQLITE_PATH")
    return Settings(
        supabase_url=get("SUPABASE_URL"),
        supabase_anon_key=get("SUPABASE_ANON_KEY"),
        app_base_url=(get("APP_BASE_URL") or "").rstrip("/") or None,
        default_timezone=get("DEFAULT_TIMEZONE") or "Europe/Berlin",
        sqlite_path=Path(sqlite_path) if sqlite_path else DEFAULT_SQLITE_PATH,
    )

"""Tests für Konfiguration und Repository-Auswahl."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from climate_dashboard.auth.session import AuthSession
from climate_dashboard.config import DEFAULT_SQLITE_PATH, ConfigError, load_settings
from climate_dashboard.storage import factory
from climate_dashboard.storage.sqlite_repo import SqliteRepository


def _jwt(payload: dict) -> str:
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    return f"{enc({'alg': 'HS256', 'typ': 'JWT'})}.{enc(payload)}.signature"


def test_local_mode_without_credentials():
    s = load_settings({})
    assert s.mode == "local" and s.is_local
    assert s.sqlite_path == DEFAULT_SQLITE_PATH
    assert s.default_timezone == "Europe/Berlin"


def test_local_mode_if_only_url():
    assert load_settings({"SUPABASE_URL": "https://x.supabase.co"}).mode == "local"


def test_supabase_mode():
    s = load_settings(
        {
            "SUPABASE_URL": " https://x.supabase.co ",
            "SUPABASE_ANON_KEY": "anon",
            "APP_BASE_URL": "https://klima.streamlit.app/",
            "SQLITE_PATH": "C:/tmp/x.sqlite",
        }
    )
    assert s.mode == "supabase" and not s.is_local
    assert s.supabase_url == "https://x.supabase.co"
    assert s.app_base_url == "https://klima.streamlit.app"
    assert s.sqlite_path == Path("C:/tmp/x.sqlite")


@pytest.mark.parametrize("key", ["SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_KEY", "SERVICE_ROLE_KEY"])
def test_service_role_key_rejected(key):
    with pytest.raises(ConfigError):
        load_settings({"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_ANON_KEY": "anon", key: "secret"})


def test_empty_service_role_key_ignored():
    assert load_settings({"SUPABASE_SERVICE_ROLE_KEY": ""}).mode == "local"


def test_service_key_as_anon_key_rejected():
    factory.assert_not_service_key(_jwt({"role": "anon"}))
    factory.assert_not_service_key("sb_publishable_abc")
    with pytest.raises(ConfigError):
        factory.assert_not_service_key(_jwt({"role": "service_role"}))
    with pytest.raises(ConfigError):
        factory.assert_not_service_key("sb_secret_abc")
    s = load_settings({"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_ANON_KEY": "sb_secret_abc"})
    with pytest.raises(ConfigError):
        factory.create_supabase_client(s)


def test_factory_local_creates_folder(tmp_path):
    path = tmp_path / "nested" / "dir" / "dev.sqlite"
    s = load_settings({"SQLITE_PATH": str(path)})
    try:
        repo = factory.get_repository(s)
        assert isinstance(repo, SqliteRepository)
        assert path.exists()
        assert factory.get_repository(s) is repo  # eine Verbindung pro Datei
        assert factory.get_public_repository(s) is repo
    finally:
        factory.clear_cache()


def test_factory_supabase_requires_login():
    s = load_settings({"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_ANON_KEY": _jwt({"role": "anon"})})
    with pytest.raises(factory.NotAuthenticatedError):
        factory.get_repository(s, None)


def test_factory_supabase_uses_user_jwt_and_anon_key():
    anon = _jwt({"role": "anon"})
    s = load_settings({"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_ANON_KEY": anon})
    session = AuthSession(user_id="u1", email="a@b.de", access_token="user.jwt.token", refresh_token="r")
    repo = factory.get_repository(s, session)
    assert repo.user_id == "u1"
    headers = repo.client.options.headers
    assert headers["Authorization"] == "Bearer user.jwt.token"
    assert headers["apiKey"] == anon
    public = factory.get_public_repository(s)
    assert public.user_id is None
    assert public.client.options.headers["Authorization"] == f"Bearer {anon}"


def test_supabase_url_copied_with_rest_suffix_is_normalized():
    for raw in ("https://abc.supabase.co/rest/v1/", "https://abc.supabase.co/rest/v1", "https://abc.supabase.co/"):
        s = load_settings({"SUPABASE_URL": raw, "SUPABASE_ANON_KEY": "anon"})
        assert s.supabase_url == "https://abc.supabase.co"

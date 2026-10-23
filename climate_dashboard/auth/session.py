"""Anmeldung ohne Oberfläche.

* ``LocalAuth`` – lokaler Entwicklungsmodus: ein Nutzer (``user_id = "local"``), kein Login.
* ``SupabaseAuth`` – E-Mail + Passwort über Supabase Auth.

Sicherheit: Passwörter werden nur an Supabase durchgereicht, nie geloggt oder gespeichert.
``AuthSession`` zeigt Tokens nicht in ``repr`` (damit sie nicht versehentlich in Logs oder
Fehlermeldungen landen). Die Oberfläche hält die ``AuthSession`` in ``st.session_state``
und übergibt sie an ``storage.factory.get_repository(settings, session)``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)

LOCAL_USER_ID = "local"


class AuthError(RuntimeError):
    """Anmeldung fehlgeschlagen oder Sitzung ungültig – Meldung ist für Nutzer lesbar."""


@dataclass(frozen=True)
class AuthSession:
    user_id: str
    email: str | None = None
    access_token: str | None = field(default=None, repr=False)
    refresh_token: str | None = field(default=None, repr=False)
    expires_at: datetime | None = None

    @property
    def is_local(self) -> bool:
        return self.user_id == LOCAL_USER_ID

    def is_expired(self, leeway_s: int = 60, now: datetime | None = None) -> bool:
        """True, wenn der Zugriffstoken (fast) abgelaufen ist und erneuert werden sollte."""
        if self.expires_at is None:
            return False
        now = now or datetime.now(UTC)
        return self.expires_at - timedelta(seconds=leeway_s) <= now


class LocalAuth:
    """Einzelnutzer ohne Login (nur lokal/Entwicklung)."""

    requires_login = False

    def current_session(self) -> AuthSession:
        return AuthSession(user_id=LOCAL_USER_ID, email=None)

    def sign_in(self, email: str, password: str) -> AuthSession:  # noqa: ARG002 - Schnittstelle
        return self.current_session()

    def sign_out(self, session: AuthSession | None = None) -> None:
        return None

    def refresh(self, session: AuthSession) -> AuthSession:
        return session


def _session_from_response(response: Any) -> AuthSession:
    session = getattr(response, "session", None)
    user = getattr(response, "user", None) or getattr(session, "user", None)
    if session is None or user is None or not getattr(session, "access_token", None):
        raise AuthError("Anmeldung fehlgeschlagen: keine gültige Sitzung erhalten.")
    expires_at = getattr(session, "expires_at", None)
    expires = datetime.fromtimestamp(int(expires_at), tz=UTC) if expires_at else None
    return AuthSession(
        user_id=str(user.id),
        email=getattr(user, "email", None),
        access_token=session.access_token,
        refresh_token=getattr(session, "refresh_token", None),
        expires_at=expires,
    )


def _translate_auth_error(exc: Exception, action: str) -> AuthError:
    text = str(exc).lower()
    status = getattr(exc, "status", None)
    if "refresh token" in text or "refresh_token" in text:
        return AuthError("Die Sitzung ist abgelaufen. Bitte erneut anmelden.")
    if "invalid login credentials" in text or getattr(exc, "code", None) == "invalid_credentials":
        return AuthError("E-Mail-Adresse oder Passwort ist falsch.")
    if "email not confirmed" in text:
        return AuthError("Die E-Mail-Adresse ist noch nicht bestätigt.")
    if status == 429 or "rate limit" in text:
        return AuthError("Zu viele Versuche. Bitte einige Minuten warten.")
    if status in (401, 403):
        return AuthError("Die Sitzung ist abgelaufen. Bitte erneut anmelden.")
    name = type(exc).__name__
    if "Retryable" in name or "Connect" in name or "Timeout" in name or "Network" in name:
        return AuthError("Keine Verbindung zum Anmeldedienst. Bitte Internetverbindung prüfen.")
    return AuthError(f"{action} fehlgeschlagen. Bitte später erneut versuchen.")


class SupabaseAuth:
    """Supabase-Anmeldung mit dem anon-Key. ``client`` ist ein ``supabase.Client``
    (in Tests ein Fake). Es wird nie ein Service-Role-Key verwendet."""

    requires_login = True

    def __init__(self, client: Any) -> None:
        self.client = client

    @classmethod
    def from_settings(cls, settings: Any) -> SupabaseAuth:
        from climate_dashboard.storage.factory import create_supabase_client

        return cls(create_supabase_client(settings))

    def sign_in(self, email: str, password: str) -> AuthSession:
        email = (email or "").strip()
        if not email or not password:
            raise AuthError("Bitte E-Mail-Adresse und Passwort eingeben.")
        try:
            response = self.client.auth.sign_in_with_password({"email": email, "password": password})
        except Exception as exc:  # Fehlertext ohne Passwort weitergeben
            # Nur Typ, HTTP-Status und Fehlercode protokollieren – nie E-Mail, Passwort oder Fehlertext.
            logger.warning(
                "Anmeldung fehlgeschlagen: %s (status=%s, code=%s)",
                type(exc).__name__,
                getattr(exc, "status", None),
                getattr(exc, "code", None),
            )
            raise _translate_auth_error(exc, "Anmeldung") from None
        return _session_from_response(response)

    def refresh(self, session: AuthSession) -> AuthSession:
        if not session.refresh_token:
            raise AuthError("Die Sitzung ist abgelaufen. Bitte erneut anmelden.")
        try:
            response = self.client.auth.refresh_session(session.refresh_token)
        except Exception as exc:
            raise _translate_auth_error(exc, "Sitzungserneuerung") from None
        return _session_from_response(response)

    def ensure_fresh(self, session: AuthSession, leeway_s: int = 60) -> AuthSession:
        """Erneuert die Sitzung, falls der Zugriffstoken bald abläuft."""
        return self.refresh(session) if session.is_expired(leeway_s) else session

    def set_session(self, session: AuthSession) -> None:
        """Sitzung auf dem Client setzen (z. B. für ``sign_out``)."""
        if not session.access_token or not session.refresh_token:
            raise AuthError("Unvollständige Sitzung.")
        try:
            self.client.auth.set_session(session.access_token, session.refresh_token)
        except Exception as exc:
            raise _translate_auth_error(exc, "Sitzung setzen") from None

    def sign_out(self, session: AuthSession | None = None) -> None:
        """Meldet ab (widerruft den Refresh-Token serverseitig). Fehler werden ignoriert –
        die lokale Sitzung muss die Oberfläche in jedem Fall verwerfen."""
        try:
            if session is not None and session.access_token and session.refresh_token:
                self.client.auth.set_session(session.access_token, session.refresh_token)
            self.client.auth.sign_out()
        except Exception as exc:
            logger.info("Abmeldung serverseitig fehlgeschlagen (%s)", type(exc).__name__)

"""Freigabelinks für Berichte: Token erzeugen, hashen, URL bauen.

Ablauf
------
1. ``generate_token()`` erzeugt einen zufälligen Token (256 Bit Entropie).
2. Gespeichert wird ausschließlich ``hash_token(token)`` (SHA-256, hex) plus ``token_hint``
   (letzte 4 Zeichen zur Wiedererkennung in der Liste der Freigaben).
3. Der Klartext-Token wird dem Nutzer genau einmal angezeigt (als Link ``share_url``).
4. Beim Aufruf ``/?r=<token>`` prüft ``is_plausible_token`` zuerst Format und Länge, bevor
   die Datenbank gefragt wird; danach ``resolve_shared_report(repo, token)``.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta
from typing import Any

from climate_dashboard.domain.models import utcnow

# 32 Byte = 256 Bit Zufall → 43 Zeichen URL-sicheres Base64 (ohne Padding).
TOKEN_BYTES = 32
TOKEN_LENGTH = 43
# Toleranz für die Plausibilitätsprüfung (ältere/neuere Token-Längen).
MIN_TOKEN_LENGTH = 32
MAX_TOKEN_LENGTH = 128
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# Ablauf-Optionen für die Oberfläche: Beschriftung → Tage (None = kein Ablauf).
EXPIRY_OPTIONS: dict[str, int | None] = {
    "7 Tage": 7,
    "30 Tage": 30,
    "90 Tage": 90,
    "1 Jahr": 365,
    "Kein Ablaufdatum": None,
}
DEFAULT_EXPIRY_LABEL = "30 Tage"


def generate_token() -> str:
    """Neuer, nicht erratbarer Freigabe-Token (``secrets.token_urlsafe(32)`` → 256 Bit)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 des Tokens als Hex-String (64 Zeichen, Kleinbuchstaben).

    Warum kein Salt / kein langsamer Passwort-Hash (bcrypt, Argon2)?
    Salt und absichtlich langsame Hashes schützen *niedrig-entropische* Geheimnisse
    (Passwörter) gegen Wörterbuch- und Rainbow-Table-Angriffe. Der Token hier besteht aus
    256 Bit echtem Zufall – ein Durchprobieren des Raums (2^256) ist unabhängig von Salt
    oder Hash-Geschwindigkeit ausgeschlossen, und vorberechnete Tabellen sind nutzlos.
    Ein deterministischer Hash ist dagegen nötig, damit die Datenbank die Freigabe per
    Index (``token_hash UNIQUE``) direkt finden kann. Der Hash schützt davor, dass ein
    Datenbank-Leak (z. B. Backup) direkt gültige Links preisgibt.
    Die Supabase-Funktion ``get_shared_report`` berechnet denselben Wert
    (``encode(sha256(convert_to(token, 'UTF8')), 'hex')``).
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_valid_token_hash(value: str) -> bool:
    return isinstance(value, str) and bool(_HASH_RE.match(value))


def token_hint(token: str) -> str:
    """Letzte 4 Zeichen – genug zur Wiedererkennung, zu wenig zum Erraten."""
    return token[-4:]


def share_url(base_url: str | None, token: str) -> str:
    """Öffentlicher Link ``<base>/?r=<token>``. Ohne Basis-URL eine relative Angabe."""
    base = (base_url or "").strip().rstrip("/")
    return f"{base}/?r={token}"


def is_plausible_token(text: str | None) -> bool:
    """Schnelle Formatprüfung, bevor die Datenbank gefragt wird (Länge + Zeichensatz)."""
    if not isinstance(text, str):
        return False
    if not MIN_TOKEN_LENGTH <= len(text) <= MAX_TOKEN_LENGTH:
        return False
    return bool(_TOKEN_RE.match(text))


def expires_at_for(days: int | None, now: datetime | None = None) -> datetime | None:
    """Ablaufzeitpunkt für eine Option aus ``EXPIRY_OPTIONS``."""
    if days is None:
        return None
    return (now or utcnow()) + timedelta(days=days)


def resolve_shared_report(repo: Any, token: str) -> dict[str, Any] | None:
    """Öffentlichen Bericht zu einem Klartext-Token laden (für die öffentliche Seite).

    Bevorzugt ``repo.resolve_share_token(token)``: In Supabase muss der *Klartext*-Token an
    die RPC ``get_shared_report`` gehen, die ihn serverseitig hasht – ein Hash-basierter
    öffentlicher Endpunkt würde den Schutz durch das Hashen aufheben. Repositories ohne
    diese Methode werden über ``resolve_share(hash_token(token))`` angesprochen.
    """
    if not is_plausible_token(token):
        return None
    by_token = getattr(repo, "resolve_share_token", None)
    if callable(by_token):
        return by_token(token)
    return repo.resolve_share(hash_token(token))

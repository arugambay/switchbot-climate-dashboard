"""Tests für Freigabe-Tokens."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from climate_dashboard.reports import sharing
from climate_dashboard.reports.sharing import (
    EXPIRY_OPTIONS,
    expires_at_for,
    generate_token,
    hash_token,
    is_plausible_token,
    is_valid_token_hash,
    resolve_shared_report,
    share_url,
    token_hint,
)


def test_token_length_charset_and_entropy():
    tokens = {generate_token() for _ in range(500)}
    assert len(tokens) == 500  # keine Kollisionen
    for t in tokens:
        assert len(t) == 43  # 32 Byte → 43 Base64url-Zeichen = 256 Bit
        assert re.fullmatch(r"[A-Za-z0-9_-]+", t)
        assert quote(t, safe="") == t  # URL-sicher ohne Kodierung
        assert is_plausible_token(t)
    assert sharing.TOKEN_BYTES * 8 >= 256


def test_hash_deterministic_sha256_hex():
    t = generate_token()
    assert hash_token(t) == hash_token(t)
    assert hash_token(t) == hashlib.sha256(t.encode()).hexdigest()
    assert len(hash_token(t)) == 64 and is_valid_token_hash(hash_token(t))
    assert hash_token(t) != hash_token(generate_token())
    # bekannter Testvektor (identisch zu PostgreSQL: encode(digest('abc','sha256'),'hex'))
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert not is_valid_token_hash(t)


def test_hint_is_last_four_chars():
    assert token_hint("abcdefghijXYZ1") == "XYZ1"


def test_share_url():
    assert share_url("https://klima.streamlit.app/", "TOK") == "https://klima.streamlit.app/?r=TOK"
    assert share_url("https://klima.streamlit.app", "TOK") == "https://klima.streamlit.app/?r=TOK"
    assert share_url(None, "TOK") == "/?r=TOK"
    assert share_url("", "TOK") == "/?r=TOK"


def test_plausibility_rejects_garbage():
    assert not is_plausible_token(None)
    assert not is_plausible_token("")
    assert not is_plausible_token("short")
    assert not is_plausible_token("a" * 129)
    assert not is_plausible_token("a" * 40 + "' OR 1=1 --")
    assert not is_plausible_token("a" * 40 + "/../")
    assert is_plausible_token("a" * 32)


def test_expiry_options():
    assert EXPIRY_OPTIONS["7 Tage"] == 7
    assert EXPIRY_OPTIONS[sharing.DEFAULT_EXPIRY_LABEL] == 30
    assert None in EXPIRY_OPTIONS.values()
    now = datetime(2026, 10, 22, tzinfo=UTC)
    assert expires_at_for(7, now) == now + timedelta(days=7)
    assert expires_at_for(None, now) is None


def test_resolve_shared_report_dispatch():
    calls = []

    class ByHash:
        def resolve_share(self, h):
            calls.append(("hash", h))
            return {"ok": 1}

    class ByToken(ByHash):
        def resolve_share_token(self, t):
            calls.append(("token", t))
            return {"ok": 2}

    t = generate_token()
    assert resolve_shared_report(ByHash(), t) == {"ok": 1}
    assert resolve_shared_report(ByToken(), t) == {"ok": 2}
    assert resolve_shared_report(ByToken(), "x") is None  # DB wird gar nicht gefragt
    assert calls == [("hash", hash_token(t)), ("token", t)]

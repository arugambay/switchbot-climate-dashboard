"""Schutz gegen versehentliche Veröffentlichung privater Daten und Geheimnisse.

Prüft alle Dateien, die Git verfolgt oder zum Commit vorgemerkt sind.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git nicht verfügbar")
    return [line for line in out.splitlines() if line.strip()]


def test_no_private_data_files_would_be_committed():
    forbidden = re.compile(r"(\.env$|\.env\.(?!example$)|secrets\.toml$|\.sqlite3?(-\w+)?$|\.db(-\w+)?$|^data/private/)")
    offenders = [f for f in _tracked_files() if forbidden.search(f)]
    assert offenders == []


def test_csv_files_only_in_test_fixtures():
    offenders = [f for f in _tracked_files() if f.lower().endswith(".csv") and not f.startswith("tests/fixtures/")]
    assert offenders == []


def test_example_sensor_export_is_not_tracked():
    assert not any("Schlafzimmer_Sensor_" in f for f in _tracked_files())


SECRET_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),  # JWT (Supabase-Keys)
    re.compile(r"sb_secret_[A-Za-z0-9_-]{10,}"),  # neue Supabase Secret Keys
    re.compile(r"service_role\"?\s*[:=]\s*\"?eyJ"),
    re.compile(r"postgres(ql)?://[^:\s]+:[^@\s]{6,}@"),  # Verbindungs-URL mit Passwort
]


def test_no_secrets_in_tracked_text_files():
    offenders = []
    for name in _tracked_files():
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(f"{name}: {pattern.pattern[:30]}")
    assert offenders == []


def test_forbidden_pattern_covers_sqlite_side_files():
    """Auch SQLite-Hilfsdateien (WAL/SHM/Journal) enthalten Messdaten."""
    pattern = re.compile(r"(\.sqlite3?(-\w+)?$|\.db(-\w+)?$)")
    for name in ("data/dev.sqlite", "data/dev.sqlite-wal", "data/dev.sqlite-shm", "x.db-journal"):
        assert pattern.search(name), name

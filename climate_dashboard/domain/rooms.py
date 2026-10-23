"""Raumnamen: Normalisierung, Prüfung, ähnliche Räume finden und Namensvorschläge.

Räume werden nie automatisch zusammengeführt; ähnliche Namen werden nur angezeigt, damit der
Nutzer selbst entscheiden kann. Namensvorschläge aus Dateinamen sind reine Vorschläge – es
gibt keine vorgegebenen Standardnamen.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections.abc import Iterable
from pathlib import PurePath

from climate_dashboard.domain.models import Room

MAX_ROOM_NAME_LENGTH = 80

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

# Wörter in Dateinamen, die keinen Raum beschreiben
_FILE_NOISE_WORDS = {
    "export",
    "exports",
    "data",
    "daten",
    "switchbot",
    "csv",
    "log",
    "logs",
    "history",
    "verlauf",
    "download",
    "backup",
    "copy",
    "kopie",
}
_SENSOR_WORDS = {"sensor", "meter", "thermometer", "hygrometer", "thermohygrometer"}
_DATE_TOKEN = re.compile(r"^\d{4}([-.]?\d{2}){1,2}$|^\d{6,14}$|^\d{1,2}\.\d{1,2}\.\d{2,4}$")


def normalize_room_name(name: str) -> str:
    """Vergleichsform eines Namens: getrimmt, Mehrfach-Leerzeichen zusammengefasst,
    casefold, Umlaute als ae/oe/ue/ss. Nur für Vergleiche, nicht zur Anzeige."""
    text = unicodedata.normalize("NFKC", name or "")
    text = re.sub(r"\s+", " ", text).strip().casefold()
    return text.translate(_UMLAUTS)


def validate_room_name(name: str) -> str | None:
    """Gibt einen Fehlertext zurück oder ``None``, wenn der Name gültig ist."""
    cleaned = re.sub(r"\s+", " ", name or "").strip()
    if not cleaned:
        return "Bitte einen Namen für den Raum eingeben."
    if len(cleaned) > MAX_ROOM_NAME_LENGTH:
        return f"Der Name darf höchstens {MAX_ROOM_NAME_LENGTH} Zeichen lang sein."
    return None


def find_similar_rooms(name: str, rooms: Iterable[Room], threshold: float = 0.8) -> list[tuple[Room, float]]:
    """Findet Räume mit gleichem oder ähnlichem Namen (absteigend nach Ähnlichkeit).

    Gleicher normalisierter Name → 1.0; sonst difflib-Ähnlichkeit der normalisierten Namen.
    Archivierte Räume sind enthalten (erkennbar an ``room.is_archived``). Es wird nie
    automatisch zusammengeführt.
    """
    target = normalize_room_name(name)
    if not target:
        return []
    result: list[tuple[Room, float]] = []
    for room in rooms:
        other = normalize_room_name(room.name)
        score = 1.0 if other == target else difflib.SequenceMatcher(None, target, other).ratio()
        if score >= threshold:
            result.append((room, round(score, 3)))
    result.sort(key=lambda x: (-x[1], x[0].is_archived, x[0].name))
    return result


def is_duplicate_name(name: str, rooms: Iterable[Room], exclude_id: str | None = None) -> bool:
    """True, wenn ein anderer Raum (auch archiviert) denselben normalisierten Namen hat."""
    target = normalize_room_name(name)
    return any(r.id != exclude_id and normalize_room_name(r.name) == target for r in rooms)


def _tokens(file_name: str) -> list[str]:
    stem = PurePath(file_name or "").name
    stem = re.sub(r"\.(csv|txt|tsv)$", "", stem, flags=re.IGNORECASE)
    return [t for t in re.split(r"[_\s]+", stem) if t]


def _clean(parts: list[str]) -> str | None:
    text = " ".join(parts)
    text = re.sub(r"[-.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or text.replace(" ", "").isdigit():
        return None
    return text[:MAX_ROOM_NAME_LENGTH]


def _leading_parts(file_name: str, stop_words: set[str]) -> list[str]:
    parts: list[str] = []
    for token in _tokens(file_name):
        if _DATE_TOKEN.match(token) or token.casefold() in stop_words:
            break
        parts.append(token)
    return parts


def suggest_room_name_from_filename(file_name: str) -> str | None:
    """Raumnamen-Vorschlag aus dem Dateinamen (nur Vorschlag).

    ``"Schlafzimmer_Sensor_20260301_20261022.csv"`` → ``"Schlafzimmer"``;
    ``"Keller-links_export.csv"`` → ``"Keller links"``; nichts Sinnvolles → ``None``.
    """
    return _clean(_leading_parts(file_name, _FILE_NOISE_WORDS | _SENSOR_WORDS))


def suggest_sensor_name_from_filename(file_name: str) -> str | None:
    """Sensornamen-Vorschlag aus dem Dateinamen (nur Vorschlag).

    ``"Schlafzimmer_Sensor_20260301_20261022.csv"`` → ``"Schlafzimmer Sensor"``;
    ``"Keller-links_export.csv"`` → ``"Keller links"``.
    """
    return _clean(_leading_parts(file_name, _FILE_NOISE_WORDS))

"""Öffentliche, schreibgeschützte Berichtsansicht (``/?r=<token>``).

Regeln:

* Rendert ausschließlich aus dem Berichts-Snapshot (``resolve_shared_report``) – keine
  Rohdaten-Abfragen, keine interne Navigation, keine Einstellungen.
* Der Token wird nie ausgegeben (auch nicht in Fehlermeldungen oder Dateinamen).
* Ungültige, widerrufene, abgelaufene oder unbekannte Links ergeben dieselbe neutrale Seite –
  so lässt sich nicht erkennen, ob ein Token jemals existiert hat.
* Pro Browsersitzung wird der Bericht nur einmal je ``RECHECK_SECONDS`` aufgelöst (sonst würde
  jede Interaktion, z. B. ein Tab-Wechsel, als neuer Zugriff gezählt). Ein Widerruf wirkt
  spätestens nach dieser Zeit bzw. beim nächsten Seitenaufruf.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import streamlit as st

from climate_dashboard.config import load_settings
from climate_dashboard.reports.sharing import hash_token, resolve_shared_report
from climate_dashboard.storage import factory
from climate_dashboard.ui import components as ui
from climate_dashboard.ui import report_view

log = logging.getLogger(__name__)
RECHECK_SECONDS = 300
_STATE_KEY = "_public_report_cache"


def _resolve(token: str) -> dict[str, Any] | None:
    cache: dict[str, tuple[float, Any]] = st.session_state.setdefault(_STATE_KEY, {})
    k = hash_token(token)[:24]
    hit = cache.get(k)
    if hit and time.monotonic() - hit[0] < RECHECK_SECONDS:
        return hit[1]
    try:
        result = resolve_shared_report(factory.get_public_repository(load_settings()), token)
    except Exception:  # noqa: BLE001 - nach außen keine Details (Token-Existenz, Infrastruktur)
        log.warning("Öffentlicher Bericht konnte nicht geladen werden.", exc_info=False)
        result = None
    if result is not None:
        cache[k] = (time.monotonic(), result)
    else:
        cache.pop(k, None)
    return result


@st.cache_data(show_spinner=False, max_entries=8, ttl=3600)
def _pdf_cached(snapshot_json: str) -> bytes:
    import json

    from climate_dashboard.reports.pdf import render_pdf

    return render_pdf(json.loads(snapshot_json))


def _file_name(snapshot: dict[str, Any]) -> str:
    import re

    meta = snapshot.get("meta") or {}
    base = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß_-]+", "-", f"Klimabericht-{meta.get('room_name') or ''}").strip("-")
    start = str(meta.get("period_start") or "")[:10]
    return f"{base[:60]}{('-' + start) if start else ''}.pdf"


def render_unavailable() -> None:
    st.html(ui.page_header_html("Dieser Bericht ist nicht (mehr) verfügbar", eyebrow="Klimabericht"))
    st.html(ui.empty_state_html(
        "Bericht nicht verfügbar",
        "Der Link ist ungültig, abgelaufen oder wurde zurückgezogen. Bitte wenden Sie sich an die Person, "
        "die Ihnen den Link geschickt hat.",
        icon="⌀",
    ))
    st.html(ui.report_footer_html(report_view.FOOTER_TEXT))


def render(token: str) -> None:
    result = _resolve(token)
    snapshot = (result or {}).get("content") if isinstance(result, dict) else None
    if not isinstance(snapshot, dict) or not snapshot:
        render_unavailable()
        return
    try:
        snapshot_json = report_view.snapshot_key(snapshot)
        report_view.render_report(
            snapshot,
            key="public",
            pdf_bytes=lambda: _pdf_cached(snapshot_json),
            pdf_file_name=_file_name(snapshot),
        )
    except Exception:
        # Anonyme Besucher sehen nie technische Details (siehe auch client.showErrorDetails).
        log.warning("Öffentlicher Bericht: Darstellung fehlgeschlagen.")
        render_unavailable()

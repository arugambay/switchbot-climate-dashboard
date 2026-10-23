"""Wiederverwendbare Auswahl-Widgets und Seitenhilfen der internen App.

* Raum-, Sensor- und Messgrößenauswahl
* Zeitraumauswahl mit Schnellwahl → ``(start, end)`` als tz-aware UTC, halboffen ``[start, end)``
* Navigationslinks, die auch außerhalb von ``st.navigation`` (Tests) nicht abstürzen
* einheitliche, verständliche Fehleranzeige (``guarded``)

Die Schnellwahl bezieht sich auf die **letzte vorhandene Messung**, nicht auf „heute“ –
SwitchBot-Daten kommen als Export und sind oft einige Tage alt.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd
import streamlit as st

from climate_dashboard.domain.models import (
    ABS_HUMIDITY,
    DEW_POINT,
    HUMIDITY,
    METRIC_LABELS,
    METRICS,
    TEMPERATURE,
    VPD,
    Room,
    Sensor,
)
from climate_dashboard.ui import components as ui
from climate_dashboard.ui import state
from climate_dashboard.ui.formatting import fmt_date_range

QUICK_PERIODS: tuple[str, ...] = ("7 Tage", "30 Tage", "3 Monate", "Jahr", "Gesamt", "Frei")
DEFAULT_METRICS: tuple[str, ...] = (TEMPERATURE, HUMIDITY, DEW_POINT)
METRIC_ORDER: tuple[str, ...] = (TEMPERATURE, HUMIDITY, DEW_POINT, ABS_HUMIDITY, VPD)

# url_path → (Titel, Icon) – muss zu app.py passen
PAGES: dict[str, tuple[str, str]] = {
    "uebersicht": ("Übersicht", ":material/dashboard:"),
    "import": ("Import", ":material/upload_file:"),
    "raeume": ("Räume & Sensoren", ":material/sensors:"),
    "analyse": ("Analyse", ":material/monitoring:"),
    "vergleich": ("Vergleich", ":material/compare_arrows:"),
    "berichte": ("Berichte & Freigaben", ":material/description:"),
    "einstellungen": ("Einstellungen", ":material/settings:"),
}


# ---------------------------------------------------------------------------
# Zeit
# ---------------------------------------------------------------------------
def utc(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    t = pd.Timestamp(value)
    if t is pd.NaT:
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def local_midnight(d: date, tz: str) -> pd.Timestamp:
    """Lokale Mitternacht eines Kalendertags → UTC."""
    naive = pd.Timestamp(datetime.combine(d, time()))
    return naive.tz_localize(tz, ambiguous=True, nonexistent="shift_forward").tz_convert("UTC")


def local_datetime(d: date, t: time, tz: str) -> pd.Timestamp:
    naive = pd.Timestamp(datetime.combine(d, t))
    return naive.tz_localize(tz, ambiguous=True, nonexistent="shift_forward").tz_convert("UTC")


def data_end(last: Any, expected_s: float | None = None) -> pd.Timestamp:
    """Ende des Datenzeitraums: letzte Messung + ein Intervall (halboffen)."""
    return utc(last) + pd.Timedelta(seconds=float(expected_s or 60))


def quick_period(
    choice: str, first: Any, last: Any, tz: str, expected_s: float | None = None
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Schnellwahl relativ zur letzten Messung → ``[start, end)`` in UTC (auf den Datenbeginn begrenzt)."""
    end = data_end(last, expected_s)
    first_t = utc(first) or end
    if choice == "Gesamt":
        return first_t, end
    local_end = end.tz_convert(tz)
    if choice == "7 Tage":
        start = local_end - pd.Timedelta(days=7)
    elif choice == "3 Monate":
        start = local_end - pd.DateOffset(months=3)
    elif choice == "Jahr":
        start = local_end - pd.DateOffset(years=1)
    else:  # "30 Tage" und Rückfall
        start = local_end - pd.Timedelta(days=30)
    start = pd.Timestamp(start).tz_convert("UTC")
    return max(start, first_t), end


def period_label(start: Any, end: Any, tz: str) -> str:
    return fmt_date_range(start, end, tz, end_exclusive=True)


# ---------------------------------------------------------------------------
# Daten-Hilfen (gecacht)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, ttl=600, max_entries=256)
def _extent(_repo: Any, user: str, sensor_id: str, version: int) -> tuple[Any, Any, int]:
    return _repo.measurement_extent(sensor_id)


def sensor_extent(sensor_id: str) -> tuple[pd.Timestamp | None, pd.Timestamp | None, int]:
    """(erste Messung, letzte Messung, Anzahl) ohne alle Daten zu laden (gecacht je Datenstand)."""
    first, last, n = _extent(state.repo(), state.user_key(), sensor_id, state.data_version())
    return utc(first), utc(last), int(n)


def combined_extent(sensor_ids: Sequence[str]) -> tuple[pd.Timestamp | None, pd.Timestamp | None, int]:
    firsts, lasts, total = [], [], 0
    for sid in sensor_ids:
        f, la, n = sensor_extent(sid)
        if n:
            firsts.append(f)
            lasts.append(la)
            total += n
    if not total:
        return None, None, 0
    return min(firsts), max(lasts), total


def main_sensor(sensors: Sequence[Sensor]) -> Sensor | None:
    """Sensor mit der jüngsten Messung (für Raum-Übersichten)."""
    best: tuple[pd.Timestamp, Sensor] | None = None
    for s in sensors:
        _, last, n = sensor_extent(s.id)
        if n and (best is None or last > best[0]):
            best = (last, s)
    if best is not None:
        return best[1]
    return sensors[0] if sensors else None


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------
def room_selector(
    rooms: Sequence[Room], *, key: str, label: str = "Raum", remember: bool = True
) -> Room | None:
    """Raumauswahl; merkt sich die Auswahl seitenübergreifend (``state.selected_room_id``)."""
    if not rooms:
        return None
    ids = [r.id for r in rooms]
    by_id = {r.id: r for r in rooms}
    preferred = state.selected_room_id() if remember else None
    index = ids.index(preferred) if preferred in ids else 0
    chosen = st.selectbox(
        label,
        ids,
        index=index,
        key=key,
        format_func=lambda rid: by_id[rid].name + (" (archiviert)" if by_id[rid].is_archived else ""),
    )
    if remember and chosen != preferred:
        state.set_selected_room_id(chosen)
    return by_id.get(chosen)


def sensor_selector(sensors: Sequence[Sensor], *, key: str, label: str = "Sensor") -> Sensor | None:
    if not sensors:
        return None
    by_id = {s.id: s for s in sensors}
    if len(sensors) == 1:
        return sensors[0]
    chosen = st.selectbox(label, list(by_id), key=key, format_func=lambda sid: by_id[sid].name)
    return by_id.get(chosen)


def metric_selector(
    *, key: str, default: Sequence[str] = DEFAULT_METRICS, label: str = "Messgrößen"
) -> list[str]:
    chosen = st.multiselect(
        label,
        list(METRIC_ORDER),
        default=[m for m in default if m in METRICS],
        key=key,
        format_func=lambda m: METRIC_LABELS.get(m, m),
    )
    return [m for m in METRIC_ORDER if m in chosen]


def period_selector(
    *,
    key: str,
    first: Any,
    last: Any,
    tz: str,
    expected_s: float | None = None,
    default: str = "30 Tage",
    label: str = "Zeitraum",
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Schnellwahl (7 Tage/30 Tage/3 Monate/Jahr/Gesamt) oder freie Datumsauswahl.

    Rückgabe: ``(start, end)`` tz-aware UTC, halboffen. Freie Auswahl: ganze lokale Kalendertage.
    """
    choice = st.segmented_control(
        label, list(QUICK_PERIODS), default=default, key=f"{key}_quick", selection_mode="single"
    )
    choice = choice or default
    first_t, last_t = utc(first), utc(last)
    if choice != "Frei":
        start, end = quick_period(choice, first_t, last_t, tz, expected_s)
        st.caption(f"{period_label(start, end, tz)} · bezogen auf die letzte Messung")
        return start, end
    lo = first_t.tz_convert(tz).date()
    hi = last_t.tz_convert(tz).date()
    default_start = max(lo, (last_t.tz_convert(tz) - pd.Timedelta(days=30)).date())
    picked = st.date_input(
        "Von – bis",
        value=(default_start, hi),
        min_value=lo,
        max_value=hi,
        format="DD.MM.YYYY",
        key=f"{key}_dates",
    )
    if isinstance(picked, tuple | list):
        d0 = picked[0] if len(picked) > 0 else default_start
        d1 = picked[1] if len(picked) > 1 else d0
    else:
        d0 = d1 = picked
    if d1 < d0:
        d0, d1 = d1, d0
    return local_midnight(d0, tz), local_midnight(d1 + timedelta(days=1), tz)


# ---------------------------------------------------------------------------
# Navigation und Fehler
# ---------------------------------------------------------------------------
def nav_link(url_path: str, label: str | None = None) -> None:
    """Link auf eine interne Seite. Außerhalb von ``st.navigation`` (z. B. in Tests) wird ein
    Texthinweis angezeigt statt einer Ausnahme."""
    title, icon = PAGES.get(url_path, (url_path, None))
    page_fn = _page_function(url_path)
    try:
        if page_fn is None:
            raise LookupError(url_path)
        st.page_link(st.Page(page_fn, title=title, icon=icon, url_path=url_path), label=label or title, icon=icon)
    except Exception:  # noqa: BLE001 - Seite nicht registriert (Tests/Einzelaufruf)
        st.caption(f"→ Menüpunkt „{title}“ in der Seitenleiste")


def _page_function(url_path: str) -> Callable[[], None] | None:
    from climate_dashboard.ui.pages import (
        analysis,
        compare,
        import_wizard,
        overview,
        reports,
        rooms,
        settings,
    )

    return {
        "uebersicht": overview.render,
        "import": import_wizard.render,
        "raeume": rooms.render,
        "analyse": analysis.render,
        "vergleich": compare.render,
        "berichte": reports.render,
        "einstellungen": settings.render,
    }.get(url_path)


def show_error(exc: Exception) -> None:
    """Verständliche Fehlermeldung für Speicher-/Verbindungsfehler."""
    from climate_dashboard.storage.base import RepositoryError
    from climate_dashboard.storage.factory import NotAuthenticatedError
    from climate_dashboard.storage.supabase_repo import ConnectionFailedError, SessionExpiredError

    if isinstance(exc, SessionExpiredError | NotAuthenticatedError):
        st.session_state.pop(state.AUTH_SESSION_KEY, None)
        ui.callout("hinweis", "Bitte erneut anmelden", str(exc) or "Die Sitzung ist abgelaufen.")
        if st.button("Zur Anmeldung", type="primary", key="err_relogin"):
            st.rerun()
    elif isinstance(exc, ConnectionFailedError):
        ui.callout("auffaellig", "Keine Verbindung zur Datenbank", str(exc))
    elif isinstance(exc, RepositoryError):
        ui.callout("auffaellig", "Speicherfehler", str(exc))
    else:
        raise exc


def guarded(body: Callable[[], None]) -> None:
    """Führt ``body`` aus und zeigt Speicherfehler verständlich an (statt eines Stacktraces)."""
    from climate_dashboard.auth.session import AuthError
    from climate_dashboard.storage.base import RepositoryError

    try:
        body()
    except RepositoryError as exc:
        show_error(exc)
    except AuthError as exc:
        st.session_state.pop(state.AUTH_SESSION_KEY, None)
        ui.callout("hinweis", "Bitte erneut anmelden", str(exc))


def settings_key(settings: Any) -> tuple:
    """Hashbarer Schlüssel für ``AnalysisSettings`` (für ``st.cache_data``)."""
    return tuple(sorted((k, v) for k, v in settings.to_dict().items()))


# ---------------------------------------------------------------------------
# Analyse-Cache
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=256, ttl=3600)
def _memo(key: tuple, _fn: Callable[[], Any]) -> Any:
    return _fn()


def memo(key: tuple, fn: Callable[[], Any]) -> Any:
    """Cacht ``fn()`` unter ``key`` + Nutzer + Datenstand (``state.data_version``).

    ``key`` muss alle Parameter enthalten, die das Ergebnis bestimmen (Sensor, Zeitraum,
    Einstellungen …). Nach ``state.invalidate_data()`` wird neu berechnet.
    """
    return _memo((state.user_key(), state.data_version(), *key), fn)


LOAD_TAIL = pd.Timedelta(days=1)  # Puffer nach dem Zeitraumende (Zeitgewicht der letzten Messung)


def load_prepared(
    sensor_id: str, start: Any, end: Any, settings: Any, expected_s: float | None
) -> tuple[pd.DataFrame, Any]:
    """Lädt NUR den Zeitraum (+ 1 Tag Puffer danach) und bereitet ihn einmal auf.

    Rückgabe: ``(roh, prepared)`` – ``roh`` sind die Originalwerte in ``[start, end)``,
    ``prepared`` ist ``analysis.stats.Prepared`` für genau diesen Zeitraum.
    """
    from climate_dashboard.analysis.stats import prepare
    from climate_dashboard.domain.models import SENSOR_ID, TS

    s, e = utc(start), utc(end)

    def compute() -> tuple[pd.DataFrame, Any]:
        df = state.load_measurements([sensor_id], s, e + LOAD_TAIL)
        df = df.drop(columns=[SENSOR_ID], errors="ignore")
        prep = prepare(df, settings, expected_s, None, s, e)
        raw = df[(df[TS] >= s) & (df[TS] < e)].reset_index(drop=True)
        return raw, prep

    return memo(("prepared", sensor_id, s, e, settings_key(settings), expected_s), compute)


# ---------------------------------------------------------------------------
# Darstellung von Einschätzungen
# ---------------------------------------------------------------------------
CATEGORY_LABELS: dict[str, str] = {
    "feuchte": "Luftfeuchtigkeit",
    "dauer": "Dauer feuchter Phasen",
    "taupunkt": "Taupunkt",
    "kondensation": "Kondensation",
    "datenqualitaet": "Datenqualität",
    "veraenderung": "Veränderung",
    "trocken": "Trockene Luft",
    "temperatur": "Temperatur",
}
OVERALL_LEVEL: dict[str, str] = {
    "unauffaellig": "ok",
    "feuchte": "hinweis",
    "kondensation": "auffaellig",
    "schimmel": "auffaellig",
    "keine_daten": "neutral",
}
_LEVEL_RANK = {"auffaellig": 0, "hinweis": 1, "ok": 2}


def finding_dicts(findings: Sequence[Any], *, top: int | None = None, skip_ok: bool = False) -> list[dict]:
    """Befunde → Dicts für ``components.finding_list`` (deutsche Kategorie, ohne Rohschlüssel).
    Sortiert nach Schwere; ``top`` begrenzt die Anzahl."""
    items = [f for f in findings if not (skip_ok and f.level == "ok")]
    items = sorted(items, key=lambda f: _LEVEL_RANK.get(f.level, 3))
    if top is not None:
        items = items[:top]
    return [
        {"level": f.level, "category": CATEGORY_LABELS.get(f.category, f.category), "text": f.text}
        for f in items
    ]


# ---------------------------------------------------------------------------
# Vergleichstabelle (analysis.periods.ComparisonResult)
# ---------------------------------------------------------------------------
_CMP_DECIMALS: dict[str, int] = {
    "temperature_mean": 1, "temperature_min": 1, "temperature_max": 1, "humidity_mean": 0,
    "humidity_max": 0, "dew_point_mean": 1, "abs_humidity_mean": 1, "vpd_mean": 2, "pct_elevated": 1,
    "pct_high": 1, "hours_elevated": 1, "sustained_episodes": 0, "sustained_per_week": 1,
    "longest_episode_hours": 1, "pct_spread_below_warn": 1, "time_coverage_pct": 0,
}  # fmt: skip


def _with_unit(text: str, unit: str) -> str:
    from climate_dashboard.ui.formatting import MISSING, NARROW_NBSP

    if text == MISSING or unit in ("", "Anzahl"):
        return text
    return f"{text}{NARROW_NBSP}{unit}"


def comparison_rows(cmp: Any, keys: Sequence[str] | None = None) -> pd.DataFrame:
    """Vergleich als Tabelle (Texte deutsch formatiert): Kennzahl, A, B, Δ, Δ %.
    Δ % nur, wo die Analyse es für sinnvoll hält (sonst „–“)."""
    from climate_dashboard.ui.formatting import fmt_number, fmt_percent

    la, lb = cmp.labels
    rows = []
    for item in cmp.items:
        if keys is not None and item.key not in keys:
            continue
        d = _CMP_DECIMALS.get(item.key, 1)
        rows.append({
            "Kennzahl": item.label,
            la: _with_unit(fmt_number(item.a, d), item.unit),
            lb: _with_unit(fmt_number(item.b, d), item.unit),
            "Δ (B − A)": _with_unit(fmt_number(item.delta, d, sign=True), item.delta_unit),
            "Δ %": fmt_percent(item.delta_pct, 0, sign=True),
        })  # fmt: skip
    return pd.DataFrame(rows)


def table(df: pd.DataFrame, *, key: str | None = None, height: int | str = "auto") -> None:
    """Einheitliche, schreibgeschützte Tabelle (Texte bereits formatiert)."""
    st.dataframe(df, hide_index=True, width="stretch", key=key, height=height)

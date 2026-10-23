"""Synthetische Messdaten für Tests.

Es werden niemals echte, private Messdaten in Tests verwendet. Diese Funktionen erzeugen
reproduzierbare Datenreihen im kanonischen Format bzw. als SwitchBot-ähnliche CSV-Datei.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from climate_dashboard.domain.models import ABS_HUMIDITY, DEW_POINT, HUMIDITY, TEMPERATURE, TS, VPD


def _svp_kpa(t):
    return 0.6112 * np.exp(17.62 * t / (243.12 + t))


def _localize_bound(value: str, tz: str) -> pd.Timestamp:
    """Lokale Zeitangabe → UTC (nicht existente Zeit: nächste gültige; mehrdeutige: Sommerzeit)."""
    t = pd.Timestamp(value)
    if t.tzinfo is not None:
        return t.tz_convert("UTC")
    return t.tz_localize(tz, ambiguous=True, nonexistent="shift_forward").tz_convert("UTC")


def make_measurements(
    start: str = "2026-03-01 00:00",
    end: str = "2026-03-08 00:00",
    interval_min: float = 30,
    *,
    tz: str = "Europe/Berlin",
    seed: int = 1,
    drop_prob: float = 0.0,
    gaps: list[tuple[str, str]] | None = None,
    humid_spells: list[tuple[str, str, float]] | None = None,
    base_rh: float = 52.0,
    base_t: float = 20.0,
) -> pd.DataFrame:
    """Erzeugt kanonische Messdaten (``ts`` in UTC) mit Tagesgang.

    Das Raster ist – wie bei einem echten Sensor – in UTC regelmäßig (``start``/``end`` sind
    lokale Zeiten in ``tz``). Dadurch ist es sommerzeitrobust: Die beim Sprung auf Sommerzeit
    übersprungene Stunde kommt in Ortszeit nicht vor, die doppelte Herbststunde erscheint in
    Ortszeit zweimal. Bei Intervallen, die keine Teiler einer Stunde sind (z. B. 12 h, 24 h),
    verschiebt sich die Ortszeit nach einer Umstellung um eine Stunde.

    ``gaps``: lokale Zeiträume ohne Messungen. ``humid_spells``: (start, ende, rF%) – in diesen
    lokalen Zeiträumen wird die relative Feuchte auf den angegebenen Wert gesetzt.
    """
    rng = np.random.default_rng(seed)
    utc = pd.date_range(
        _localize_bound(start, tz),
        _localize_bound(end, tz),
        freq=pd.Timedelta(minutes=interval_min),
        inclusive="left",
    )
    keep = rng.random(len(utc)) >= drop_prob
    utc = utc[keep]
    local_aware = utc.tz_convert(tz)
    for g_start, g_end in gaps or []:
        lo, hi = _localize_bound(g_start, tz), _localize_bound(g_end, tz)
        local_aware = local_aware[(local_aware < lo) | (local_aware >= hi)]
    local = local_aware.tz_localize(None)
    hours = local.hour + local.minute / 60
    t = base_t + 1.5 * np.sin((hours - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.15, len(local))
    rh = base_rh + 6 * np.sin((hours - 3) / 24 * 2 * np.pi) + rng.normal(0, 1.0, len(local))
    rh = np.asarray(rh, dtype=float)
    for s_start, s_end, value in humid_spells or []:
        lo, hi = _localize_bound(s_start, tz), _localize_bound(s_end, tz)
        mask = (local_aware >= lo) & (local_aware < hi)
        rh[mask] = value
    t = np.round(np.asarray(t, dtype=float), 1)
    rh = np.clip(np.round(rh), 1, 100)
    svp = _svp_kpa(t)
    gamma = np.log(rh / 100) + 17.62 * t / (243.12 + t)
    td = 243.12 * gamma / (17.62 - gamma)
    ah = 216.7 * (rh / 100 * svp * 10) / (273.15 + t)
    ts = local_aware.tz_convert("UTC")
    return pd.DataFrame(
        {
            TS: ts,
            TEMPERATURE: t,
            HUMIDITY: rh,
            DEW_POINT: np.round(td, 1),
            VPD: np.round(svp * (1 - rh / 100), 2),
            ABS_HUMIDITY: np.round(ah, 2),
        }
    ).reset_index(drop=True)


def to_switchbot_csv(
    df: pd.DataFrame,
    *,
    tz: str = "Europe/Berlin",
    delimiter: str = ",",
    encoding: str = "utf-8",
    header: tuple[str, ...] = (
        "timestamp",
        "temperature(℃)",
        "humidity(%)",
        "dpt(℃)",
        "vpd(kPa)",
        "absoluteHumidity(g/m³)",
    ),
    decimal: str = ",",
) -> bytes:
    """Schreibt Daten im Format des echten SwitchBot-Exports (Komma-getrennt, Dezimalkomma
    in Anführungszeichen, lokale naive Zeitstempel). Vektorisiert – auch für große Reihen."""
    local = df[TS].dt.tz_convert(tz).dt.strftime("%Y-%m-%d %H:%M:%S").astype(str)
    quote = decimal == delimiter or (decimal == "," and delimiter == ",")

    def fmt(series: pd.Series, decimals: int) -> pd.Series:
        text = pd.Series(np.char.mod(f"%.{decimals}f", series.to_numpy(dtype=float)), index=series.index)
        if decimal != ".":
            text = text.str.replace(".", decimal, regex=False)
        return '"' + text + '"' if quote else text

    humidity = pd.Series(df[HUMIDITY].to_numpy(dtype=float).astype(int).astype(str), index=df.index)
    lines = (
        local
        + delimiter
        + fmt(df[TEMPERATURE], 1)
        + delimiter
        + humidity
        + delimiter
        + fmt(df[DEW_POINT], 1)
        + delimiter
        + fmt(df[VPD], 2)
        + delimiter
        + fmt(df[ABS_HUMIDITY], 2)
    )
    body = "\n".join([delimiter.join(header), *lines.tolist()])
    return (body + "\n").encode(encoding)

"""Synthetische Demo-Daten für den Styleguide (keine echten Messdaten).

Bewusst unabhängig von ``tests/synthetic.py`` – die App importiert keinen Testcode.
Die Aggregat-Funktionen sind einfache Anzeige-Beispiele, keine fachlich korrekte Analyse
(dafür ist ``climate_dashboard.analysis`` zuständig).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from climate_dashboard.domain.models import ABS_HUMIDITY, DEW_POINT, HUMIDITY, TEMPERATURE, TS, VPD

_A, _B = 17.62, 243.12


def make_demo_measurements(
    start: str = "2025-11-01",
    end: str = "2026-10-22",
    interval_min: int = 10,
    *,
    tz: str = "Europe/Berlin",
    seed: int = 7,
    gaps: list[tuple[str, str]] | None = None,
) -> pd.DataFrame:
    """Kanonischer Messdaten-DataFrame mit Jahres- und Tagesgang, Duschspitzen und Lücken."""
    rng = np.random.default_rng(seed)
    local = pd.date_range(start, end, freq=pd.Timedelta(minutes=interval_min), inclusive="left")
    for g0, g1 in (
        gaps
        if gaps is not None
        else [("2026-01-10 08:00", "2026-01-12 19:30"), ("2026-06-03", "2026-06-04 06:00")]
    ):
        local = local[(local < pd.Timestamp(g0)) | (local >= pd.Timestamp(g1))]
    doy = local.day_of_year.to_numpy()
    hour = (local.hour + local.minute / 60).to_numpy()
    season = np.cos((doy - 15) / 365.25 * 2 * np.pi)  # +1 im Januar, −1 im Juli
    t = 20.5 - 1.8 * season + 1.2 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.12, len(local))
    rh = 54 + 7 * season + 5 * np.sin((hour - 4) / 24 * 2 * np.pi) + rng.normal(0, 1.2, len(local))
    # morgendliche Duschspitzen (07–08:30) mit Abklingen
    shower = np.clip(1 - np.abs(hour - 7.5) / 1.2, 0, None) * (rng.random(len(local)) < 0.85)
    rh = rh + 18 * shower
    # eine längere feuchte Phase im Winter
    wet = (local >= pd.Timestamp("2026-02-02")) & (local < pd.Timestamp("2026-02-05"))
    rh = np.where(wet, rh + 12, rh)
    t, rh = np.round(t, 1), np.clip(np.round(rh), 20, 99)
    svp = 0.6112 * np.exp(_A * t / (_B + t))
    gamma = np.log(rh / 100) + _A * t / (_B + t)
    td = _B * gamma / (_A - gamma)
    ah = 216.7 * (rh / 100 * svp * 10) / (273.15 + t)
    ts = local.tz_localize(tz, ambiguous="NaT", nonexistent="NaT")
    df = pd.DataFrame(
        {
            TS: ts,
            TEMPERATURE: t,
            HUMIDITY: rh.astype(float),
            DEW_POINT: np.round(td, 1),
            VPD: np.round(svp * (1 - rh / 100), 2),
            ABS_HUMIDITY: np.round(ah, 2),
        }
    )
    df = df[df[TS].notna()].copy()
    df[TS] = df[TS].dt.tz_convert("UTC")
    return df.reset_index(drop=True)


def _local(df: pd.DataFrame, tz: str) -> pd.Series:
    return df[TS].dt.tz_convert(tz)


def demo_monthly(df: pd.DataFrame, metric: str = HUMIDITY, tz: str = "Europe/Berlin") -> pd.DataFrame:
    local = _local(df, tz)
    g = df.groupby(local.dt.strftime("%Y-%m"))[metric]
    out = g.agg(["mean", "min", "max", "count"]).reset_index(names="month")
    days = pd.PeriodIndex(out["month"], freq="M").days_in_month.to_numpy()
    out["coverage"] = np.minimum(out["count"] / (days * 24 * 6), 1.0)
    out["complete"] = out["coverage"] >= 0.9
    return out.drop(columns="count")


def demo_seasonal(df: pd.DataFrame, metric: str = HUMIDITY, tz: str = "Europe/Berlin") -> pd.DataFrame:
    local = _local(df, tz)
    month = local.dt.month
    season = month.map({12: "winter", 1: "winter", 2: "winter", 3: "spring", 4: "spring", 5: "spring",
                        6: "summer", 7: "summer", 8: "summer", 9: "autumn", 10: "autumn", 11: "autumn"})  # fmt: skip
    year = local.dt.year + (month == 12).astype(int)
    out = df.groupby([year.rename("year"), season.rename("season")], sort=False)[metric].agg(
        ["mean", "min", "max"]
    )
    out = out.reset_index()
    out["complete"] = [
        not (s == "autumn" and y == 2026) and not (s == "winter" and y == 2025)
        for s, y in zip(out["season"], out["year"], strict=True)
    ]  # noqa: E501
    return out


def demo_day_night(df: pd.DataFrame, metric: str = HUMIDITY, tz: str = "Europe/Berlin") -> pd.DataFrame:
    local = _local(df, tz)
    phase = np.where((local.dt.hour >= 22) | (local.dt.hour < 6), "night", "day")
    out = df.groupby(
        [local.dt.strftime("%Y-%m").rename("group"), pd.Series(phase, index=df.index, name="phase")]
    )[metric]
    return out.mean().rename("mean").reset_index()


def demo_band_shares() -> pd.DataFrame:
    rows = []
    for group, shares in {
        "Schlafzimmer": {"dry": 0.02, "normal": 0.71, "elevated": 0.19, "high": 0.05, "gap": 0.03},
        "Bad": {"dry": 0.0, "normal": 0.48, "elevated": 0.27, "high": 0.22, "gap": 0.03},
        "Wohnzimmer": {"dry": 0.08, "normal": 0.84, "elevated": 0.07, "high": 0.0, "gap": 0.01},
    }.items():
        for band, share in shares.items():
            rows.append({"group": group, "band": band, "share": share, "duration_s": share * 30 * 86400})
    return pd.DataFrame(rows)


def demo_episodes(tz: str = "Europe/Berlin") -> pd.DataFrame:
    def ts(s: str) -> pd.Timestamp:
        return pd.Timestamp(s, tz=tz).tz_convert("UTC")

    return pd.DataFrame(
        [
            {
                "start": ts("2026-02-02 06:40"),
                "end": ts("2026-02-05 01:10"),
                "label": "≥ 60 % rF",
                "level": "hinweis",
                "max": 69,
            },
            {
                "start": ts("2026-02-03 07:10"),
                "end": ts("2026-02-03 11:40"),
                "label": "≥ 70 % rF",
                "level": "auffaellig",
                "max": 78,
            },
            {
                "start": ts("2026-02-07 07:05"),
                "end": ts("2026-02-07 09:20"),
                "label": "≥ 60 % rF",
                "level": "hinweis",
                "max": 66,
            },
            {
                "start": ts("2026-02-10 07:15"),
                "end": ts("2026-02-10 08:55"),
                "label": "≥ 70 % rF",
                "level": "auffaellig",
                "max": 74,
            },
            {
                "start": ts("2026-02-12 18:00"),
                "end": ts("2026-02-12 18:20"),
                "label": "≥ 60 % rF",
                "level": "hinweis",
                "max": 61,
            },
        ]
    )


def demo_findings() -> list[dict]:
    return [
        {
            "level": "auffaellig",
            "category": "humidity",
            "text": "An 4 von 28 Tagen lag die relative Luftfeuchtigkeit länger als 2 Stunden über 70 %.",
            "evidence": ["längste Phase: 4 Std. 30 Min.", "Höchstwert 78 %"],
        },
        {
            "level": "hinweis",
            "category": "condensation",
            "text": "Der Abstand zwischen Raumtemperatur und Taupunkt unterschritt zeitweise 3 K. "
            "An kälteren Oberflächen könnten dann Bedingungen für Kondensation bestehen.",
            "evidence": {"Anteil der Zeit": "6 %", "Minimum": "2,1 K"},
        },
        {
            "level": "ok",
            "category": "temperature",
            "text": "Die Temperatur war über den Zeitraum gleichmäßig.",
            "evidence": None,
        },
        {
            "level": "info",
            "category": "data_quality",
            "text": "Zwischen 10.01. und 12.01.2026 fehlen Messwerte; dieser Zeitraum fließt nicht in die Anteile ein.",
            "evidence": "Zeitabdeckung 96,4 %",
        },
    ]

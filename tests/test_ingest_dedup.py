"""Tests für die Deduplizierung."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from climate_dashboard.domain.models import ABS_HUMIDITY, HUMIDITY, TEMPERATURE, TS, VPD
from climate_dashboard.ingest.dedup import classify
from tests.synthetic import make_measurements


def data(n: int = 10) -> pd.DataFrame:
    df = make_measurements("2026-01-05", "2026-01-06", interval_min=30).head(n).copy()
    df.index = range(2, 2 + n)  # Zeilennummern wie aus parse_csv
    return df


def test_no_existing_all_new():
    r = classify(data(), None)
    assert r.counts == {
        "new": 10,
        "duplicate": 0,
        "conflict": 0,
        "in_file_duplicates": 0,
        "in_file_conflicts": 0,
        "in_file_conflict_rows": 0,
    }


def test_identical_reimport_yields_only_duplicates():
    df = data()
    r = classify(df, df.reset_index(drop=True))
    assert len(r.new) == 0
    assert len(r.duplicates) == 10
    assert r.conflicts == []


def test_overlap_only_new_part_is_new():
    df = data(10)
    existing = df.iloc[:6].reset_index(drop=True)
    r = classify(df, existing)
    assert len(r.new) == 4
    assert r.new[TS].tolist() == df[TS].iloc[6:].tolist()
    assert len(r.duplicates) == 6


def test_conflicts_are_reported_not_overwritten():
    df = data(10)
    existing = df.reset_index(drop=True).copy()
    existing.loc[[1, 4, 7], TEMPERATURE] += 0.5
    r = classify(df, existing)
    assert len(r.conflicts) == 3
    assert len(r.new) == 0 and len(r.duplicates) == 7
    c = r.conflicts[0]
    assert c.ts == df[TS].iloc[1]
    assert c.existing[TEMPERATURE] == float(df[TEMPERATURE].iloc[1] + 0.5)
    assert c.incoming[TEMPERATURE] == float(df[TEMPERATURE].iloc[1])
    json.dumps(r.to_dict())


def test_rounding_tolerance():
    df = data(3)
    existing = df.reset_index(drop=True).copy()
    existing.loc[0, TEMPERATURE] += 0.0001  # nach Rundung auf 3 Stellen gleich
    existing.loc[1, TEMPERATURE] += 0.001  # unterscheidet sich
    r = classify(df, existing)
    assert len(r.duplicates) == 2
    assert [c.ts for c in r.conflicts] == [df[TS].iloc[1]]


def test_nan_equals_nan_and_nan_vs_value_differs():
    df = data(2)
    df[VPD] = [np.nan, np.nan]
    existing = df.reset_index(drop=True).copy()
    existing.loc[1, VPD] = 1.0
    r = classify(df, existing, compare_metrics=[TEMPERATURE, HUMIDITY, VPD])
    assert len(r.duplicates) == 1
    assert (
        len(r.conflicts) == 1 and r.conflicts[0].incoming[VPD] is None and r.conflicts[0].existing[VPD] == 1.0
    )


def test_metrics_missing_in_file_are_not_compared_by_default():
    df = data(3)
    incoming = df.copy()
    incoming[ABS_HUMIDITY] = np.nan  # Datei ohne Spalte für absolute Feuchte
    r = classify(incoming, df.reset_index(drop=True))
    assert ABS_HUMIDITY not in r.compared_metrics
    assert len(r.duplicates) == 3 and r.conflicts == []


def test_in_file_identical_duplicates_kept_once():
    df = data(5)
    dup = pd.concat([df, df.iloc[[1, 3]].set_axis([90, 91])])
    r = classify(dup, None)
    assert r.in_file_duplicates == 2
    assert len(r.new) == 5
    assert r.new.index.tolist() == [2, 3, 4, 5, 6]  # die erste Zeile bleibt erhalten


def test_in_file_conflicts_excluded_entirely():
    df = data(5)
    other = df.iloc[[2]].copy()
    other[TEMPERATURE] += 1
    mixed = pd.concat([df, other.set_axis([50]), df.iloc[[2]].set_axis([51])])
    r = classify(mixed, None)
    assert len(r.in_file_conflicts) == 1
    assert r.in_file_conflict_rows == 3  # Zeile 4, 50, 51 (51 ist identisch zu 4, zählt als Konfliktzeile)
    assert r.in_file_duplicates == 0
    assert sorted(r.in_file_conflicts[0].lines) == [4, 50, 51]
    assert len(r.in_file_conflicts[0].variants) == 2
    assert df[TS].iloc[2] not in set(r.new[TS])
    assert len(r.new) == 4
    total = len(r.new) + len(r.duplicates) + len(r.conflicts) + r.in_file_duplicates + r.in_file_conflict_rows
    assert total == len(mixed)


def test_timestamps_normalized_to_seconds():
    df = data(2)
    incoming = df.copy()
    incoming[TS] = incoming[TS] + pd.Timedelta(milliseconds=400)
    r = classify(incoming, df.reset_index(drop=True))
    assert len(r.duplicates) == 2
    r2 = classify(incoming, None)
    assert (r2.new[TS].dt.microsecond == 0).all()


def test_existing_with_sensor_id_column():
    df = data(4)
    existing = df.reset_index(drop=True).copy()
    existing.insert(0, "sensor_id", "s1")
    r = classify(df, existing.iloc[:2])
    assert len(r.duplicates) == 2 and len(r.new) == 2

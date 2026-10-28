"""End-to-end test of the offline data catalog pipeline.

Builds a catalog from fixture snapshots in a temporary directory, then runs the
simulator in catalog mode and checks that the data actually drives the results
and that provenance is recorded.
"""

import csv
import io
import json
import os
import subprocess
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.data.catalog import Catalog
from src.simulation import DataCenterSimulator


class _StubConfig:
    def __init__(self, data_source):
        self._data = {
            "simulation": {"random_seed": 42, "sla_slack": 1.5},
            "task_generation": {"base_rate": 4, "peak_rate": 10, "peak_hours": [9, 18]},
            "task_completion": {"mode": "service_based", "completion_probability": 0.3},
            "cooling": {"power_coefficient": 500, "target_temp": 22, "fan_target_temp": 35,
                        "dissipation_coefficient": 8.0, "thermal_inertia": 0.25},
            "energy": {"idle_power_factor": 0.6, "part_load_penalty": 0.5,
                       "temp_penalty_coefficient": 1.0, "temp_reference": 35},
            "genetic": {"population_size": 8, "generations": 4, "enable_parallel": False},
            "greedy": {"temperature_weight": 0.3},
            "data_source": data_source,
        }

    def to_dict(self):
        return self._data


def _write_dataset(raw_root, kind, name, rows, meta):
    folder = os.path.join(raw_root, kind)
    os.makedirs(folder, exist_ok=True)
    csv_path = os.path.join(folder, name + ".csv")
    with io.open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with io.open(os.path.splitext(csv_path)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    return csv_path


def _ambient_rows(hours=48):
    rows = []
    for hour in range(hours):
        ts = "2026-03-01T%02d:00:00" % (hour % 24)
        temp = 26.0 + 8.0 * ((hour % 24) / 24.0)
        rows.append({"ts": ts, "temp_c": round(temp, 2), "humidity": 45, "wind_ms": 2.0})
    return rows


def _build(raw_root, db_path, extra_args=()):
    cmd = [sys.executable, os.path.join("tools", "etl", "build_catalog.py"),
           "--raw-root", str(raw_root), "--db", str(db_path)]
    cmd.extend(extra_args)
    return subprocess.run(cmd, cwd=PROJECT_ROOT, capture_output=True, text=True)


def test_catalog_builds_and_feeds_the_simulator(tmp_path):
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "catalog.db"

    _write_dataset(str(raw_root), "ambient", "campus", _ambient_rows(48), {
        "source_id": "test-ambient",
        "name": "测试用逐时气温",
        "publisher": "Test Publisher",
        "url": "https://example.invalid/test",
        "license": "test-only",
        "retrieved_at": "2026-10-15",
        "site_id": "campus-x",
    })
    outcome = _build(str(raw_root), str(db_path))
    assert outcome.returncode == 0, outcome.stderr
    assert "测试用逐时气温" in outcome.stdout or "ambient" in outcome.stdout

    catalog = Catalog(str(db_path))
    assert catalog.available()

    series = catalog.ambient_series("campus-x")
    assert series is not None and len(series) == 48
    assert series[0] == 26.0

    provenance = catalog.provenance()
    ids = {row["source_id"] for row in provenance}
    assert "test-ambient" in ids
    ambient_meta = [row for row in provenance if row["source_id"] == "test-ambient"][0]
    assert ambient_meta["publisher"] == "Test Publisher"
    assert ambient_meta["url"] == "https://example.invalid/test"
    assert ambient_meta["sha256"]

    versions = catalog.versions()
    assert versions and all(row["row_count"] for row in versions)

    simulator = DataCenterSimulator(
        num_servers=16, simulation_hours=24, seed=42,
        config=_StubConfig({
            "mode": "catalog",
            "catalog_db": str(db_path),
            "site_id": "campus-x",
        }),
    )
    assert simulator.ambient_series is not None
    assert simulator.run_metadata["data_mode"] == "catalog"

    df = simulator.run_comparison(algorithms=["greedy", "round_robin"])
    assert df["ambient_temperature"].nunique() > 1
    assert df["pue"].nunique() > 1


def test_catalog_without_database_falls_back_to_synthetic(tmp_path):
    simulator = DataCenterSimulator(
        num_servers=8, simulation_hours=4, seed=1,
        config=_StubConfig({"mode": "catalog", "catalog_db": str(tmp_path / "missing.db")}),
    )
    assert simulator.ambient_series is None
    assert simulator.run_metadata["ambient_source"] == "synthetic-sine"
    df = simulator.run_comparison(algorithms=["greedy"])
    assert not df.empty


def test_dataset_without_provenance_is_refused(tmp_path):
    raw_root = tmp_path / "raw"
    folder = os.path.join(str(raw_root), "ambient")
    os.makedirs(folder, exist_ok=True)
    csv_path = os.path.join(folder, "no_meta.csv")
    with io.open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["ts", "temp_c"])
        writer.writeheader()
        writer.writerow({"ts": "2026-01-01T00:00:00", "temp_c": 1.0})

    db_path = tmp_path / "catalog.db"
    outcome = _build(str(raw_root), str(db_path))
    assert outcome.returncode == 0

    catalog = Catalog(str(db_path))
    assert catalog.ambient_series() is None
    assert catalog.provenance() == []


def test_trace_snapshot_builds_a_task_stream(tmp_path):
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "catalog.db"
    rows = []
    for hour in range(12):
        for i in range(5):
            rows.append({
                "hour": hour,
                "load_units": 10 + i * 3,
                "priority": 3,
                "workload_class": "cpu_bound" if i % 2 == 0 else "gpu_bound",
                "duration_h": 2,
            })
    _write_dataset(str(raw_root), "trace", "cluster", rows, {
        "source_id": "test-trace",
        "name": "测试用任务轨迹（派生演示数据）",
        "publisher": "Test Publisher",
        "url": "https://example.invalid/test-trace",
        "license": "test-only",
        "retrieved_at": "2026-10-15",
        "trace_id": "cluster-x",
    })

    outcome = _build(str(raw_root), str(db_path), ("--sla-slack", "1.5"))
    assert outcome.returncode == 0, outcome.stderr

    catalog = Catalog(str(db_path))
    stream = catalog.task_stream("cluster-x")
    assert stream is not None
    assert len(stream) == 12
    assert len(stream[0]) == 5
    load, priority, workload_class, duration, offset = stream[0][0]
    assert load >= 10 and priority == 3 and duration == 2 and offset >= 2

    simulator = DataCenterSimulator(
        num_servers=8, simulation_hours=12, seed=5,
        config=_StubConfig({"mode": "catalog", "catalog_db": str(db_path),
                            "trace_id": "cluster-x"}),
    )
    assert simulator.trace_stream is not None
    df = simulator.run_comparison(algorithms=["greedy", "damo_ets"])
    assert simulator.run_metadata["scenario_source"] == "catalog"
    assert df["offered_load"].max() > 0

"""Tests for the persistent run registry."""

import pandas as pd

from src.services import run_store


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(run_store, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(run_store, "RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(run_store, "DB_PATH", str(tmp_path / "runs.db"))
    monkeypatch.setattr(run_store, "LEGACY_RESULTS", str(tmp_path / "simulation_results.csv"))
    monkeypatch.setattr(run_store, "LEGACY_METADATA", str(tmp_path / "run_metadata.json"))
    run_store._init()


def test_run_store_roundtrip(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    run_id = run_store.create_run({
        "num_servers": 6,
        "simulation_hours": 2,
        "algorithms": ["greedy"],
        "site_id": "北京市",
    }, origin="test")
    df = pd.DataFrame([{
        "time": 0,
        "algorithm": "greedy",
        "total_load": 10.0,
        "served_load": 10.0,
        "total_energy": 50.0,
        "pue": 1.4,
        "peak_temperature": 55.0,
        "assigned": 10,
        "dropped": 0,
    }])
    summary = {
        "greedy": {
            "service_rate": 1.0,
            "energy_per_served_load": 5.0,
            "avg_pue": 1.4,
            "max_temp": 55.0,
        }
    }
    run_store.save_results(run_id, df, {"run_id": run_id}, summary)
    run_store.update_run(run_id, status="success", progress=100)
    record = run_store.get_run(run_id)
    assert record["status"] == "success"
    assert record["summary"]["greedy"]["energy_per_served_load"] == 5.0
    assert run_store.list_runs(1)[0]["run_id"] == run_id
    assert run_store.load_results(run_id).shape[0] == 1


def test_compare_runs_uses_right_minus_left(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    left = run_store.create_run({"num_servers": 6, "simulation_hours": 2, "algorithms": ["greedy"]})
    right = run_store.create_run({"num_servers": 24, "simulation_hours": 2, "algorithms": ["greedy"]})
    run_store.update_run(left, summary_json={
        "greedy": {"service_rate": 0.5, "energy_per_served_load": 4.0, "avg_pue": 1.4, "max_temp": 60.0}
    })
    run_store.update_run(right, summary_json={
        "greedy": {"service_rate": 0.8, "energy_per_served_load": 5.0, "avg_pue": 1.5, "max_temp": 55.0}
    })
    comparison = run_store.compare_runs(left, right)
    item = comparison["metrics"][0]
    assert item["energy_per_served_load"]["delta"] == 1.0
    assert item["energy_per_served_load"]["percent"] == 25.0
    assert item["service_rate"]["delta"] == 0.3

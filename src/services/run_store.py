"""Persistent run registry shared by the web UI, the assistant and reports."""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA_DIR = os.path.join(ROOT, "data")
RUNS_DIR = os.path.join(DATA_DIR, "runs")
DB_PATH = os.path.join(DATA_DIR, "runs.db")
LEGACY_RESULTS = os.path.join(DATA_DIR, "simulation_results.csv")
LEGACY_METADATA = os.path.join(DATA_DIR, "run_metadata.json")

_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _connect() -> sqlite3.Connection:
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _init() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                origin TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                params_json TEXT,
                site_id TEXT,
                num_servers INTEGER,
                simulation_hours INTEGER,
                algorithms_json TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                results_path TEXT,
                metadata_path TEXT,
                summary_path TEXT,
                summary_json TEXT,
                error TEXT
            )
            """
        )
        conn.commit()


_init()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _row_to_run(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["params"] = _loads(item.pop("params_json", None), {})
    item["algorithms"] = _loads(item.pop("algorithms_json", None), [])
    item["summary"] = _loads(item.pop("summary_json", None), {})
    return item


def create_run(params: Dict[str, Any], origin: str = "web", run_id: Optional[str] = None) -> str:
    run_id = run_id or (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    os.makedirs(run_dir(run_id), exist_ok=True)
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            INSERT INTO runs (
                run_id, origin, status, progress, params_json, site_id,
                num_servers, simulation_hours, algorithms_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id, origin, "queued", 0, _dump(params), params.get("site_id"),
                params.get("num_servers"), params.get("simulation_hours"),
                _dump(params.get("algorithms") or []), _now(),
            ),
        )
        conn.commit()
    return run_id


def update_run(run_id: str, **fields: Any) -> None:
    allowed = {
        "status", "progress", "started_at", "finished_at", "results_path",
        "metadata_path", "summary_path", "summary_json", "error",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    for key in ("summary_json",):
        if key in updates:
            updates[key] = _dump(updates[key])
    assignments = ", ".join("%s = ?" % key for key in updates)
    with _LOCK, _connect() as conn:
        conn.execute(
            "UPDATE runs SET %s WHERE run_id = ?" % assignments,
            list(updates.values()) + [run_id],
        )
        conn.commit()


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return _row_to_run(row) if row else None


def list_runs(limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_run(row) for row in rows]


def latest_run() -> Optional[Dict[str, Any]]:
    runs = list_runs(1)
    return runs[0] if runs else None


def run_dir(run_id: str) -> str:
    return os.path.join(RUNS_DIR, run_id)


def save_results(run_id: str, df: pd.DataFrame, metadata: Dict[str, Any],
                 summary: Dict[str, Any]) -> Dict[str, str]:
    folder = run_dir(run_id)
    os.makedirs(folder, exist_ok=True)
    results_path = os.path.join(folder, "results.csv")
    metadata_path = os.path.join(folder, "metadata.json")
    summary_path = os.path.join(folder, "summary.json")
    df.to_csv(results_path, index=False)
    with open(metadata_path, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2, default=str)
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2, default=str)
    update_run(
        run_id,
        results_path=results_path,
        metadata_path=metadata_path,
        summary_path=summary_path,
        summary_json=summary,
    )
    return {
        "results_path": results_path,
        "metadata_path": metadata_path,
        "summary_path": summary_path,
    }


def load_results(run_id: str) -> Optional[pd.DataFrame]:
    record = get_run(run_id)
    path = (record or {}).get("results_path")
    if not path or not os.path.exists(path):
        path = os.path.join(run_dir(run_id), "results.csv")
    if not os.path.exists(path):
        return None
    return pd.read_csv(path)


def compare_runs(left_id: str, right_id: str) -> Optional[Dict[str, Any]]:
    left = get_run(left_id)
    right = get_run(right_id)
    if not left or not right:
        return None
    left_summary = left.get("summary") or {}
    right_summary = right.get("summary") or {}
    algorithms = sorted(set(left_summary) | set(right_summary))
    rows = []
    for algorithm in algorithms:
        left_metrics = left_summary.get(algorithm) or {}
        right_metrics = right_summary.get(algorithm) or {}
        item = {"algorithm": algorithm}
        for key in ("energy_per_served_load", "service_rate", "avg_pue", "max_temp", "co2_kg"):
            left_value = left_metrics.get(key)
            right_value = right_metrics.get(key)
            delta = None
            percent = None
            if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
                delta = round(float(right_value) - float(left_value), 4)
                if left_value:
                    percent = round((float(right_value) - float(left_value)) / float(left_value) * 100, 2)
            item[key] = {
                "left": left_value,
                "right": right_value,
                "delta": delta,
                "percent": percent,
            }
        rows.append(item)
    return {
        "left": left,
        "right": right,
        "metrics": rows,
        "rule": "right 相对 left 的变化",
    }


def import_legacy_run() -> Optional[str]:
    if list_runs(1):
        return None
    if not os.path.exists(LEGACY_RESULTS) or not os.path.exists(LEGACY_METADATA):
        return None
    try:
        df = pd.read_csv(LEGACY_RESULTS)
        with open(LEGACY_METADATA, "r", encoding="utf-8") as handle:
            meta = json.load(handle)
    except Exception:
        return None
    from src.simulation.metrics import summarize

    run_id = meta.get("run_id") or (
        "legacy-" + datetime.fromtimestamp(os.path.getmtime(LEGACY_RESULTS)).strftime("%Y%m%d-%H%M%S")
    )
    params = {
        "num_servers": meta.get("num_servers") or 10,
        "simulation_hours": meta.get("simulation_hours") or 24,
        "algorithms": meta.get("algorithms") or list(df["algorithm"].unique()),
        "site_id": meta.get("site_id"),
        "school_name": meta.get("school_name"),
    }
    create_run(params, origin="legacy", run_id=run_id)
    summary = summarize(df)
    meta = dict(meta)
    meta["run_id"] = run_id
    save_results(run_id, df, meta, summary)
    update_run(
        run_id,
        status="success",
        progress=100,
        finished_at=_now(),
    )
    return run_id

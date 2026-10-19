"""Build data/catalog.db from offline snapshots in data/raw.

Each dataset is a CSV plus a sidecar JSON describing where it came from:

    data/raw/ambient/<name>.csv
    data/raw/ambient/<name>.json

The JSON sidecar carries publisher / url / license / retrieved_at, which is what
makes the numbers in a report traceable. A dataset without a sidecar is refused,
so "where did this come from" can never be answered with a shrug.

Usage:
    python tools/etl/build_catalog.py --list
    python tools/etl/build_catalog.py --db data/catalog.db
    python tools/etl/build_catalog.py --kind trace --scale-keep 0.35 --sla-slack 1.5
"""

import argparse
import csv
import hashlib
import io
import json
import os
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RAW_ROOT = os.path.join(PROJECT_ROOT, "data", "raw")


def set_raw_root(path):
    global RAW_ROOT
    RAW_ROOT = os.path.abspath(path)
    return RAW_ROOT
SCHEMA_PATH = os.path.join(HERE, "schema.sql")

KINDS = ("ambient", "trace", "standards", "emission", "server_type", "schools", "climate")

REQUIRED_COLUMNS = {
    "ambient": ("ts", "temp_c"),
    "trace": ("load_units",),
    "standards": ("standard_id", "pue_limit"),
    "emission": ("region", "year", "kg_co2_per_kwh"),
    "server_type": ("server_type",),
    "schools": ("school_name", "city"),
    "climate": ("city", "date", "tmax_c", "tmin_c", "tmean_c"),
}


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: str) -> List[Dict[str, str]]:
    with io.open(path, "r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_sidecar(csv_path: str) -> Optional[Dict[str, Any]]:
    meta_path = os.path.splitext(csv_path)[0] + ".json"
    if not os.path.exists(meta_path):
        return None
    with io.open(meta_path, "r", encoding="utf-8") as f:
        return json.load(f)


def dataset_files(kind: str) -> List[str]:
    folder = os.path.join(RAW_ROOT, kind)
    if not os.path.isdir(folder):
        return []
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(".csv")
    )


def list_datasets() -> None:
    print("数据目录快照 (data/raw):")
    found = False
    for kind in KINDS:
        for path in dataset_files(kind):
            found = True
            meta = read_sidecar(path)
            marker = "有出处" if meta else "缺少 sidecar JSON (不会导入)"
            print("  [%s] %s  <- %s" % (kind, os.path.basename(path), marker))
    if not found:
        print("  (空) 请按 tools/etl/README.md 的格式放入 CSV 与 sidecar JSON")


def connect(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with io.open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = f.read()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)
    conn.commit()
    return conn


def register_source(conn: sqlite3.Connection, source_id: str, kind: str,
                    csv_path: str, meta: Dict[str, Any]) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO meta_data_source
           (source_id, name, publisher, url, license, retrieved_at, sha256, etl_script, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source_id,
            meta.get("name") or os.path.basename(csv_path),
            meta.get("publisher", ""),
            meta.get("url", ""),
            meta.get("license", ""),
            meta.get("retrieved_at", ""),
            sha256_of(csv_path),
            "tools/etl/build_catalog.py",
            meta.get("note", ""),
        ),
    )


def register_version(conn: sqlite3.Connection, source_id: str, kind: str,
                     table_name: str, row_count: int, csv_path: str,
                     hash_key: str, span: Tuple[str, str]) -> str:
    version_id = "%s:%s:%s" % (kind, source_id, hash_key[:12])
    conn.execute(
        """INSERT OR REPLACE INTO meta_dataset_version
           (version_id, source_id, table_name, row_count, min_ts, max_ts, hash, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (version_id, source_id, table_name, row_count, span[0], span[1],
         hash_key, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    return version_id


def _num(value: Any, cast=float, default=None):
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def import_ambient(conn, csv_path, meta, source_id) -> int:
    rows = read_csv(csv_path)
    site_id = str(meta.get("site_id") or "default")
    payload = []
    timestamps = []
    for row in rows:
        ts = str(row.get("ts") or row.get("time") or "")
        temp = _num(row.get("temp_c") if row.get("temp_c") is not None else row.get("temperature"))
        if ts == "" or temp is None:
            continue
        timestamps.append(ts)
        payload.append((site_id, ts, temp,
                        _num(row.get("humidity")), _num(row.get("wind_ms")),
                        source_id, ""))
    if not payload:
        return 0
    conn.execute(
        "DELETE FROM dim_ambient_hourly WHERE site_id = ? AND source_id = ?",
        (site_id, source_id),
    )
    version_id = register_version(conn, source_id, "ambient", "dim_ambient_hourly",
                                 len(payload), csv_path, sha256_of(csv_path),
                                 (min(timestamps), max(timestamps)))
    conn.executemany(
        """INSERT OR REPLACE INTO dim_ambient_hourly
           (site_id, ts, temp_c, humidity, wind_ms, source_id, version_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [(p[0], p[1], p[2], p[3], p[4], p[5], version_id) for p in payload],
    )
    return len(payload)


def _hour_of(row: Dict[str, Any], base_ts: Optional[float]) -> Optional[int]:
    if row.get("hour") not in (None, ""):
        return int(float(row["hour"]))
    if row.get("arrival_hour") not in (None, ""):
        return int(float(row["arrival_hour"]))
    if row.get("ts") not in (None, "") and base_ts is not None:
        try:
            return int((float(row["ts"]) - base_ts) // 3600)
        except (TypeError, ValueError):
            return None
    return None


def import_trace(conn, csv_path, meta, source_id, scale_keep: float,
                 sla_slack: float, seed: int) -> int:
    import random

    rows = read_csv(csv_path)
    if not rows:
        return 0

    base_ts = None
    if "ts" in rows[0] and "hour" not in rows[0] and "arrival_hour" not in rows[0]:
        values = [_num(r.get("ts")) for r in rows]
        values = [v for v in values if v is not None]
        base_ts = min(values) if values else None

    if scale_keep < 1.0:
        rng = random.Random(seed)
        rows = [row for row in rows if rng.random() < scale_keep]

    trace_id = str(meta.get("trace_id") or os.path.splitext(os.path.basename(csv_path))[0])
    payload = []
    seq = 0
    for row in rows:
        hour = _hour_of(row, base_ts)
        load = _num(row.get("load_units") or row.get("load"), int)
        if hour is None or load is None or hour < 0:
            continue
        duration = _num(row.get("duration_h") or row.get("duration"), int, 1) or 1
        offset = _num(row.get("deadline_offset_h"), int)
        if offset is None:
            offset = max(duration, int(round(duration * sla_slack)), 1)
        payload.append((
            trace_id, seq, hour, int(load),
            _num(row.get("priority"), int, 3),
            str(row.get("workload_class") or "cpu_bound"),
            int(duration), int(offset), source_id,
        ))
        seq += 1

    if not payload:
        return 0

    conn.execute("DELETE FROM fact_task_arrivals WHERE trace_id = ?", (trace_id,))
    hours = [p[2] for p in payload]
    register_version(conn, source_id, "trace", "fact_task_arrivals", len(payload),
                     csv_path, sha256_of(csv_path), (str(min(hours)), str(max(hours))))
    conn.executemany(
        """INSERT OR REPLACE INTO fact_task_arrivals
           (trace_id, seq, hour, load_units, priority, workload_class,
            duration_h, deadline_offset_h, source_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        payload,
    )
    return len(payload)


def import_standards(conn, csv_path, meta, source_id) -> int:
    rows = read_csv(csv_path)
    payload = []
    for row in rows:
        limit = _num(row.get("pue_limit"))
        if limit is None:
            continue
        payload.append((str(row.get("standard_id") or meta.get("name") or ""),
                        str(row.get("grade") or ""), limit,
                        str(row.get("applies_to") or ""), source_id))
    if payload:
        conn.execute("DELETE FROM dim_pue_benchmark WHERE source_id = ?", (source_id,))
        register_version(conn, source_id, "standards", "dim_pue_benchmark", len(payload),
                         csv_path, sha256_of(csv_path), ("", ""))
        conn.executemany(
            """INSERT INTO dim_pue_benchmark
               (standard_id, grade, pue_limit, applies_to, source_id)
               VALUES (?, ?, ?, ?, ?)""",
            payload,
        )
    return len(payload)


def import_emission(conn, csv_path, meta, source_id) -> int:
    rows = read_csv(csv_path)
    payload = []
    for row in rows:
        region = str(row.get("region") or "")
        year = _num(row.get("year"), int)
        factor = _num(row.get("kg_co2_per_kwh"))
        if not region or year is None or factor is None:
            continue
        payload.append((region, year, factor, source_id))
    if payload:
        conn.execute("DELETE FROM dim_emission_factor WHERE source_id = ?", (source_id,))
        register_version(conn, source_id, "emission", "dim_emission_factor", len(payload),
                         csv_path, sha256_of(csv_path), ("", ""))
        conn.executemany(
            """INSERT INTO dim_emission_factor
               (region, year, kg_co2_per_kwh, source_id) VALUES (?, ?, ?, ?)""",
            payload,
        )
    return len(payload)


def import_server_type(conn, csv_path, meta, source_id) -> int:
    rows = read_csv(csv_path)
    payload = []
    for row in rows:
        name = str(row.get("server_type") or row.get("name") or "")
        if not name:
            continue
        payload.append((name, _num(row.get("max_load"), float, 100.0),
                        _num(row.get("max_temp"), float, 85.0),
                        _num(row.get("base_power"), float, 100.0),
                        _num(row.get("max_power"), float, 300.0),
                        _num(row.get("efficiency"), float, 0.9), source_id))
    if payload:
        conn.execute("DELETE FROM dim_server_type WHERE source_id = ?", (source_id,))
        register_version(conn, source_id, "server_type", "dim_server_type", len(payload),
                         csv_path, sha256_of(csv_path), ("", ""))
        conn.executemany(
            """INSERT OR REPLACE INTO dim_server_type
               (server_type, max_load, max_temp, base_power, max_power, efficiency, source_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            payload,
        )
    return len(payload)


def import_schools(conn, csv_path, meta, source_id) -> int:
    rows = read_csv(csv_path)
    payload = []
    for row in rows:
        name = str(row.get("school_name") or "").strip()
        city = str(row.get("city") or "").strip()
        if not name or not city:
            continue
        payload.append((name, str(row.get("province") or ""), city,
                        str(row.get("level") or ""), str(row.get("department") or ""),
                        source_id))
    if payload:
        conn.execute("DELETE FROM dim_school WHERE source_id = ?", (source_id,))
        register_version(conn, source_id, "schools", "dim_school", len(payload),
                         csv_path, sha256_of(csv_path), ("", ""))
        conn.executemany(
            """INSERT INTO dim_school
               (school_name, province, city, level, department, source_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            payload,
        )
    return len(payload)


def import_climate(conn, csv_path, meta, source_id) -> int:
    rows = read_csv(csv_path)
    payload = []
    dates = []
    for row in rows:
        city = str(row.get("city") or "").strip()
        date = str(row.get("date") or "").strip()
        tmax = _num(row.get("tmax_c"))
        tmin = _num(row.get("tmin_c"))
        tmean = _num(row.get("tmean_c"))
        if not city or not date or tmax is None or tmin is None or tmean is None:
            continue
        dates.append(date)
        payload.append((city, date, tmax, tmin, tmean, source_id))
    if payload:
        conn.execute("DELETE FROM dim_city_daily WHERE source_id = ?", (source_id,))
        register_version(conn, source_id, "climate", "dim_city_daily", len(payload),
                         csv_path, sha256_of(csv_path),
                         (min(dates), max(dates)))
        conn.executemany(
            """INSERT OR REPLACE INTO dim_city_daily
               (city, date, tmax_c, tmin_c, tmean_c, source_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            payload,
        )
    return len(payload)


IMPORTERS = {
    "ambient": import_ambient,
    "schools": import_schools,
    "climate": import_climate,
    "standards": import_standards,
    "emission": import_emission,
    "server_type": import_server_type,
}


def load_kind(conn, kind: str, args) -> int:
    total = 0
    for csv_path in dataset_files(kind):
        meta = read_sidecar(csv_path)
        basename = os.path.splitext(os.path.basename(csv_path))[0]
        if meta is None:
            print("  跳过 %s: 缺少出处 sidecar (%s.json)" % (basename, basename))
            continue
        missing = [c for c in REQUIRED_COLUMNS.get(kind, ()) if c not in (read_csv(csv_path)[0] if read_csv(csv_path) else {})]
        if missing:
            print("  跳过 %s: 缺少列 %s" % (basename, ", ".join(missing)))
            continue
        source_id = str(meta.get("source_id") or "%s:%s" % (kind, basename))
        register_source(conn, source_id, kind, csv_path, meta)
        if kind == "trace":
            count = import_trace(conn, csv_path, meta, source_id,
                                 args.scale_keep, args.sla_slack, args.seed)
        else:
            count = IMPORTERS[kind](conn, csv_path, meta, source_id)
        conn.commit()
        print("  导入 [%s] %s -> %d 行 (source_id=%s)" % (kind, basename, count, source_id))
        total += count
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the provenance-tagged data catalog")
    parser.add_argument("--db", default=os.path.join(PROJECT_ROOT, "data", "catalog.db"))
    parser.add_argument("--kind", default=None, choices=list(KINDS),
                        help="只导入某一类数据，默认全部")
    parser.add_argument("--scale-keep", type=float, default=1.0,
                        help="trace 降采样保留比例 (0, 1]")
    parser.add_argument("--sla-slack", type=float, default=1.5,
                        help="缺失 deadline 时使用的 SLA 松弛系数")
    parser.add_argument("--seed", type=int, default=42, help="降采样随机种子")
    parser.add_argument("--list", action="store_true", help="只列出可用快照")
    parser.add_argument("--raw-root", default=RAW_ROOT,
                        help="快照根目录，默认 data/raw")
    args = parser.parse_args()
    set_raw_root(args.raw_root)

    if args.list:
        list_datasets()
        return 0

    conn = connect(args.db)
    kinds = [args.kind] if args.kind else list(KINDS)
    print("构建数据目录: %s" % args.db)
    total = 0
    for kind in kinds:
        total += load_kind(conn, kind, args)
    conn.commit()

    tables = [row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    print("完成: 共写入 %d 行, 表: %s" % (total, ", ".join(tables)))
    if total == 0:
        print("\n提示: data/raw 下暂无可用快照。数据获取方式见 tools/etl/README.md。")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

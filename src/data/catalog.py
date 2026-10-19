"""Read-only access to the provenance-tagged data catalog.

The catalog is a plain SQLite file (``data/catalog.db``) built by the scripts in
``tools/etl``. Every row carries a ``source_id`` pointing at
``meta_data_source``, so any number that ends up in a report can be traced back
to a publisher, a URL and a retrieval timestamp.

Nothing here reaches out to the network: the simulation must run offline, so the
catalog is a frozen snapshot taken while preparing the demo.
"""

import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from src.utils.logger import setup_logger

logger = setup_logger("catalog")

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_DB = os.path.join(PROJECT_ROOT, "data", "catalog.db")

PROVINCE_REGION = {
    "北京市": "华北", "天津市": "华北", "河北省": "华北", "山西省": "华北",
    "内蒙古自治区": "华北",
    "辽宁省": "东北", "吉林省": "东北", "黑龙江省": "东北",
    "上海市": "华东", "江苏省": "华东", "浙江省": "华东", "安徽省": "华东",
    "福建省": "华东", "江西省": "华东", "山东省": "华东",
    "河南省": "华中", "湖北省": "华中", "湖南省": "华中",
    "陕西省": "西北", "甘肃省": "西北", "青海省": "西北",
    "宁夏回族自治区": "西北", "新疆维吾尔自治区": "西北",
    "广东省": "南方", "广西壮族自治区": "南方", "海南省": "南方",
    "重庆市": "西南", "四川省": "西南", "贵州省": "西南",
    "云南省": "西南", "西藏自治区": "西南",
}


def region_for_province(province: Optional[str]) -> Optional[str]:
    return PROVINCE_REGION.get(province) if province else None


REQUIRED_TABLES = (
    "meta_data_source",
    "meta_dataset_version",
    "dim_ambient_hourly",
    "dim_server_type",
    "dim_pue_benchmark",
    "dim_emission_factor",
    "fact_task_arrivals",
)


class Catalog:
    """Thin, read-only wrapper around the catalog database."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        raw = db_path or DEFAULT_DB
        self.db_path = raw if os.path.isabs(raw) else os.path.join(PROJECT_ROOT, raw)
        self._conn: Optional[sqlite3.Connection] = None

    # ------------------------------------------------------------------ basics
    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            self._conn = conn
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Catalog":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def available(self) -> bool:
        if not os.path.exists(self.db_path):
            return False
        try:
            conn = self.connect()
            names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        except sqlite3.Error as exc:
            logger.warning("读取数据目录失败: %s", exc)
            return False
        return "dim_ambient_hourly" in names

    def table_names(self) -> List[str]:
        conn = self.connect()
        return [
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]

    def _rows(self, sql: str, params: Tuple[Any, ...] = ()) -> List[sqlite3.Row]:
        try:
            return list(self.connect().execute(sql, params))
        except sqlite3.Error as exc:
            logger.warning("查询失败 (%s): %s", sql.split()[1] if len(sql.split()) > 1 else "?", exc)
            return []

    # ------------------------------------------------------------------- reads
    def provenance(self, source_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if source_id:
            rows = self._rows(
                "SELECT * FROM meta_data_source WHERE source_id = ?", (source_id,)
            )
        else:
            rows = self._rows("SELECT * FROM meta_data_source ORDER BY source_id")
        return [dict(row) for row in rows]

    def versions(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self._rows(
            "SELECT * FROM meta_dataset_version ORDER BY created_at DESC"
        )]

    def ambient_series(self, site_id: Optional[str] = None) -> Optional[List[float]]:
        """Hourly outdoor temperature for a site.

        Uses the stored hourly series when one exists. Otherwise falls back to
        the city's daily max/min/mean and expands it with a standard diurnal
        curve (peak at 15:00, trough at 03:00), which is how a school maps to
        its local climate without storing 8760 rows for 345 cities.
        """
        if site_id:
            rows = self._rows(
                "SELECT temp_c FROM dim_ambient_hourly WHERE site_id = ? ORDER BY ts",
                (site_id,),
            )
        else:
            rows = self._rows("SELECT temp_c FROM dim_ambient_hourly ORDER BY ts")
        values = [float(row["temp_c"]) for row in rows if row["temp_c"] is not None]
        if values:
            return values
        if site_id:
            daily = self.city_daily(site_id)
            if daily:
                return self._expand_daily(daily)
        return None

    @staticmethod
    def _expand_daily(daily: List[Dict[str, Any]]) -> List[float]:
        import math
        series: List[float] = []
        for row in daily:
            tmax = float(row["tmax_c"])
            tmin = float(row["tmin_c"])
            tmean = float(row["tmean_c"])
            amplitude = max(0.0, (tmax - tmin) / 2.0)
            for hour in range(24):
                series.append(
                    tmean + amplitude * math.sin(2.0 * math.pi * (hour - 9) / 24.0)
                )
        return series

    def city_daily(self, city: str) -> Optional[List[Dict[str, Any]]]:
        rows = self._rows(
            "SELECT city, date, tmax_c, tmin_c, tmean_c FROM dim_city_daily "
            "WHERE city = ? ORDER BY date",
            (city,),
        )
        return [dict(row) for row in rows] or None

    def cities(self) -> List[Dict[str, Any]]:
        rows = self._rows(
            "SELECT d1.city, "
            "  (SELECT d2.province FROM dim_school d2 WHERE d2.city = d1.city "
            "   GROUP BY d2.province ORDER BY COUNT(*) DESC LIMIT 1) AS province, "
            "  COUNT(*) AS school_count "
            "FROM dim_school d1 GROUP BY d1.city ORDER BY province, d1.city"
        )
        return [dict(row) for row in rows]

    def city_climate_summary(self) -> List[Dict[str, Any]]:
        rows = self._rows(
            "SELECT city, AVG(tmean_c) AS avg_temp FROM dim_city_daily GROUP BY city"
        )
        return [dict(row) for row in rows]

    def _school_filter(self, query: Optional[str], city: Optional[str]):
        clauses = []
        params: List[Any] = []
        if city:
            clauses.append("city = ?")
            params.append(city)
        if query:
            clauses.append("school_name LIKE ?")
            params.append("%" + query + "%")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, tuple(params)

    def count_schools(self, query: Optional[str] = None, city: Optional[str] = None) -> int:
        where, params = self._school_filter(query, city)
        rows = self._rows("SELECT COUNT(*) AS n FROM dim_school" + where, params)
        return int(rows[0]["n"]) if rows else 0

    def schools(self, query: Optional[str] = None, city: Optional[str] = None,
                limit: int = 50) -> List[Dict[str, Any]]:
        where, params = self._school_filter(query, city)
        rows = self._rows(
            "SELECT school_name, province, city, level, department FROM dim_school"
            + where + " ORDER BY school_name LIMIT ?",
            params + (int(limit),),
        )
        return [dict(row) for row in rows]

    def task_stream(
        self, trace_id: Optional[str] = None, hours: Optional[int] = None
    ) -> Optional[List[List[Tuple[int, int, str, int, int]]]]:
        """Rebuild the pre-built task stream as per-hour spec lists.

        Rows are expected to carry the hour bucket, resource request, priority,
        workload class, duration and deadline offset recorded during import.
        """
        clauses: List[str] = []
        params: List[Any] = []
        if trace_id:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._rows(
            "SELECT seq, hour, load_units, priority, workload_class, duration_h,"
            " deadline_offset_h FROM fact_task_arrivals" + where + " ORDER BY seq",
            tuple(params),
        )
        if not rows:
            return None

        buckets: Dict[int, List[Tuple[int, int, str, int, int]]] = {}
        for row in rows:
            hour = int(row["hour"])
            if hours is not None and hour >= hours:
                continue
            buckets.setdefault(hour, []).append((
                int(row["load_units"]),
                int(row["priority"]),
                str(row["workload_class"]),
                int(row["duration_h"]),
                int(row["deadline_offset_h"]),
            ))

        if not buckets:
            return None
        top = max(buckets)
        return [buckets.get(hour, []) for hour in range(top + 1)]

    def pue_benchmarks(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self._rows(
            "SELECT * FROM dim_pue_benchmark ORDER BY pue_limit"
        )]

    def city_province(self, city: str) -> Optional[str]:
        rows = self._rows(
            "SELECT province FROM dim_school WHERE city = ? "
            "GROUP BY province ORDER BY COUNT(*) DESC LIMIT 1",
            (city,),
        )
        return rows[0]["province"] if rows else None

    def emission_factor(
        self, region: Optional[str] = None, year: Optional[int] = None
    ) -> Optional[float]:
        if region:
            sql = "SELECT kg_co2_per_kwh FROM dim_emission_factor WHERE region = ?"
            params: List[Any] = [region]
            if year is not None:
                sql += " AND year = ?"
                params.append(int(year))
            rows = self._rows(sql, tuple(params))
            for row in rows:
                if row["kg_co2_per_kwh"] is not None:
                    return float(row["kg_co2_per_kwh"])

        rows = self._rows(
            "SELECT kg_co2_per_kwh FROM dim_emission_factor "
            "WHERE region = '全国（电力平均）' LIMIT 1"
        )
        for row in rows:
            if row["kg_co2_per_kwh"] is not None:
                return float(row["kg_co2_per_kwh"])
        return None

    def summary(self) -> Dict[str, Any]:
        """Compact description used by the API and the dashboard."""
        info: Dict[str, Any] = {
            "db_path": self.db_path,
            "available": self.available(),
        }
        if not info["available"]:
            return info
        info["tables"] = self.table_names()
        info["sources"] = self.provenance()
        ambient = self.ambient_series()
        info["ambient_hours"] = len(ambient) if ambient else 0
        return info

"""把最近一次仿真结果整理成可回答的时段与温区洞察。"""

from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from src.simulation.metrics import safe_number

SERVICE_FLOOR = 0.90

TEMP_BANDS = [
    {"label": "低于 45℃", "low": None, "high": 45.0},
    {"label": "45–55℃", "low": 45.0, "high": 55.0},
    {"label": "55–65℃", "low": 55.0, "high": 65.0},
    {"label": "65–75℃", "low": 65.0, "high": 75.0},
    {"label": "75℃ 及以上", "low": 75.0, "high": None},
]

TEMP_THRESHOLDS = [40, 45, 50, 55, 60, 65, 70, 75, 80]


def latest_data_time() -> Optional[str]:
    import os

    path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "data", "simulation_results.csv")
    )
    if not os.path.exists(path):
        return None
    return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")


def _round(value: Any, digits: int = 3) -> Optional[float]:
    number = safe_number(value)
    if number is None:
        return None
    return round(number, digits)


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    frame["time"] = pd.to_numeric(frame.get("time"), errors="coerce")
    frame = frame.dropna(subset=["time"])
    frame["time"] = frame["time"].astype(int)

    for name in (
        "total_load", "served_load", "total_energy", "pue", "peak_temperature",
        "avg_temperature", "ambient_temperature", "fan_speed", "cooling_power",
        "assigned", "dropped", "overheated_servers",
    ):
        if name in frame.columns:
            frame[name] = pd.to_numeric(frame[name], errors="coerce")

    served = frame["served_load"] if "served_load" in frame.columns else pd.Series(index=frame.index, dtype=float)
    energy = frame["total_energy"] if "total_energy" in frame.columns else pd.Series(index=frame.index, dtype=float)
    frame["energy_per_load"] = energy / served.where(served > 0)

    assigned = frame["assigned"] if "assigned" in frame.columns else pd.Series(index=frame.index, dtype=float)
    dropped = frame["dropped"] if "dropped" in frame.columns else pd.Series(index=frame.index, dtype=float)
    offered = assigned + dropped
    frame["hour_service_rate"] = assigned / offered.where(offered > 0)

    return frame


def _aggregate(group: pd.DataFrame) -> Dict[str, Any]:
    return {
        "hours": int(group["time"].nunique()),
        "avg_energy_per_load": _round(group["energy_per_load"].mean(), 3),
        "avg_pue": _round(group["pue"].mean(), 3) if "pue" in group else None,
        "avg_peak_temp": _round(group["peak_temperature"].mean(), 1) if "peak_temperature" in group else None,
        "max_peak_temp": _round(group["peak_temperature"].max(), 1) if "peak_temperature" in group else None,
        "avg_fan_speed": _round(group["fan_speed"].mean(), 2) if "fan_speed" in group else None,
        "avg_cooling_power": _round(group["cooling_power"].mean(), 1) if "cooling_power" in group else None,
        "avg_service_rate": _round(group["hour_service_rate"].mean(), 4),
        "avg_load": _round(group["total_load"].mean(), 1) if "total_load" in group else None,
        "avg_served_load": _round(group["served_load"].mean(), 1) if "served_load" in group else None,
    }


def _pick_best(records: Dict[str, Dict[str, Any]], floor: float = SERVICE_FLOOR):
    usable = [
        (name, data) for name, data in records.items()
        if data and data.get("avg_energy_per_load") is not None
    ]
    reliable = [
        (name, data) for name, data in usable
        if (data.get("avg_service_rate") or 0) >= floor
    ]
    pool = reliable or usable
    if not pool:
        return None, False
    name, data = min(pool, key=lambda item: item[1]["avg_energy_per_load"])
    return name, bool(reliable)


def _hour_record(row: pd.Series) -> Dict[str, Any]:
    record = {
        "hour": int(row["time"]),
        "energy_per_load": _round(row.get("energy_per_load"), 3),
        "load": _round(row.get("total_load"), 1),
        "served_load": _round(row.get("served_load"), 1),
        "pue": _round(row.get("pue"), 3),
        "avg_temp": _round(row.get("avg_temperature"), 1),
        "peak_temp": _round(row.get("peak_temperature"), 1),
        "ambient_temp": _round(row.get("ambient_temperature"), 1),
        "fan_speed": _round(row.get("fan_speed"), 2),
        "cooling_power": _round(row.get("cooling_power"), 1),
        "service_rate": _round(row.get("hour_service_rate"), 4),
    }
    return record


def _corr(frame: pd.DataFrame, left: str, right: str) -> Optional[float]:
    if left not in frame.columns or right not in frame.columns:
        return None
    pairs = frame[[left, right]].dropna()
    if len(pairs) < 2:
        return None
    if pairs[left].nunique() < 2 or pairs[right].nunique() < 2:
        return None
    value = pairs[left].corr(pairs[right])
    return _round(value, 3)


def _range(frame: pd.DataFrame, name: str) -> Dict[str, Optional[float]]:
    if name not in frame.columns:
        return {"min": None, "max": None}
    values = frame[name].dropna()
    if values.empty:
        return {"min": None, "max": None}
    return {"min": _round(values.min(), 2), "max": _round(values.max(), 2)}


def _band_mask(frame: pd.DataFrame, low: Optional[float], high: Optional[float]):
    values = frame.get("peak_temperature")
    if values is None:
        return pd.Series(False, index=frame.index)
    mask = values.notna()
    if low is not None:
        mask &= values >= low
    if high is not None:
        mask &= values < high
    return mask


def build_insights(df: pd.DataFrame, meta: Optional[Dict[str, Any]] = None,
                   generated_at: Optional[str] = None) -> Dict[str, Any]:
    meta = meta or {}
    if df is None or df.empty or "algorithm" not in df.columns:
        return {}

    frame = _prepare(df)
    if frame.empty:
        return {}

    frame["algorithm"] = frame["algorithm"].astype(str)
    algorithms = [str(item) for item in frame["algorithm"].drop_duplicates()]
    hours = sorted(int(item) for item in frame["time"].unique())

    series_hours = hours
    if len(hours) > 72:
        step = max(1, len(hours) // 72)
        series_hours = hours[::step][:72]

    time_series = {
        "hour": series_hours,
        "sampled": len(series_hours) < len(hours),
        "by_algorithm": {},
    }
    for algorithm in algorithms:
        part = frame[frame["algorithm"] == algorithm].set_index("time").reindex(series_hours)
        time_series["by_algorithm"][algorithm] = {
            "energy_per_load": [_round(value, 3) for value in part["energy_per_load"]],
            "peak_temp": [_round(value, 1) for value in part["peak_temperature"]] if "peak_temperature" in part else [],
            "pue": [_round(value, 3) for value in part["pue"]] if "pue" in part else [],
            "service_rate": [_round(value, 4) for value in part["hour_service_rate"]],
            "served_load": [_round(value, 1) for value in part["served_load"]] if "served_load" in part else [],
        }

    best_hours: Dict[str, List[Dict[str, Any]]] = {}
    for algorithm in algorithms:
        rows = frame[frame["algorithm"] == algorithm].dropna(subset=["energy_per_load"])
        rows = rows.sort_values("energy_per_load").head(3)
        best_hours[algorithm] = [_hour_record(row) for _, row in rows.iterrows()]

    overall_rows = frame.dropna(subset=["energy_per_load"])
    if not overall_rows.empty:
        best_index = overall_rows.groupby("time")["energy_per_load"].idxmin()
        overall_rows = overall_rows.loc[best_index]
    reliable_rows = overall_rows[
        overall_rows["hour_service_rate"].fillna(0) >= SERVICE_FLOOR
    ]
    if not reliable_rows.empty:
        overall_rows = reliable_rows
    overall_rows = overall_rows.sort_values("energy_per_load").head(5)
    overall_best_hours = []
    for _, row in overall_rows.iterrows():
        record = _hour_record(row)
        record["algorithm"] = str(row["algorithm"])
        overall_best_hours.append(record)

    safe_rows = frame.dropna(subset=["energy_per_load"])
    if "overheated_servers" in safe_rows.columns:
        safe_rows = safe_rows[safe_rows["overheated_servers"].fillna(0) <= 0]
    if "peak_temperature" in safe_rows.columns:
        safe_rows = safe_rows[safe_rows["peak_temperature"].fillna(0) < 70]
    safe_best_hours = []
    if not safe_rows.empty:
        best_index = safe_rows.groupby("time")["energy_per_load"].idxmin()
        safe_rows = safe_rows.loc[best_index]
        safe_reliable = safe_rows[safe_rows["hour_service_rate"].fillna(0) >= SERVICE_FLOOR]
        if not safe_reliable.empty:
            safe_rows = safe_reliable
        safe_rows = safe_rows.sort_values("energy_per_load").head(5)
        for _, row in safe_rows.iterrows():
            record = _hour_record(row)
            record["algorithm"] = str(row["algorithm"])
            safe_best_hours.append(record)

    temperature_bands = []
    for band in TEMP_BANDS:
        subset = frame[_band_mask(frame, band["low"], band["high"])]
        by_algorithm = {
            algorithm: _aggregate(subset[subset["algorithm"] == algorithm])
            for algorithm in algorithms
        }
        best, reliable = _pick_best(by_algorithm)
        best_rows = subset[subset["algorithm"] == best] if best else subset.iloc[0:0]
        temperature_bands.append({
            "label": band["label"],
            "hours": int(best_rows["time"].nunique()) if not best_rows.empty else 0,
            "hours_any": int(subset["time"].nunique()) if not subset.empty else 0,
            "by_algorithm": by_algorithm,
            "best_algorithm": best,
            "best_reliable": reliable,
        })

    temperature_thresholds = []
    temperature_thresholds_above = []
    for threshold in TEMP_THRESHOLDS:
        subset = frame[_band_mask(frame, None, float(threshold))]
        by_algorithm = {
            algorithm: _aggregate(subset[subset["algorithm"] == algorithm])
            for algorithm in algorithms
        }
        best, reliable = _pick_best(by_algorithm)
        best_rows = subset[subset["algorithm"] == best] if best else subset.iloc[0:0]
        temperature_thresholds.append({
            "threshold_c": threshold,
            "hours_below": int(best_rows["time"].nunique()) if not best_rows.empty else 0,
            "hours_any_below": int(subset["time"].nunique()) if not subset.empty else 0,
            "by_algorithm": by_algorithm,
            "best_algorithm": best,
            "best_reliable": reliable,
        })

        hot_subset = frame[_band_mask(frame, float(threshold), None)]
        hot_by_algorithm = {
            algorithm: _aggregate(hot_subset[hot_subset["algorithm"] == algorithm])
            for algorithm in algorithms
        }
        hot_best, hot_reliable = _pick_best(hot_by_algorithm)
        hot_rows = hot_subset[hot_subset["algorithm"] == hot_best] if hot_best else hot_subset.iloc[0:0]
        temperature_thresholds_above.append({
            "threshold_c": threshold,
            "hours_above": int(hot_rows["time"].nunique()) if not hot_rows.empty else 0,
            "hours_any_above": int(hot_subset["time"].nunique()) if not hot_subset.empty else 0,
            "by_algorithm": hot_by_algorithm,
            "best_algorithm": hot_best,
            "best_reliable": hot_reliable,
        })

    effective_rows = reliable_rows if not reliable_rows.empty else overall_rows
    overall_records = {
        algorithm: _aggregate(frame[frame["algorithm"] == algorithm])
        for algorithm in algorithms
    }
    overall_best, overall_reliable = _pick_best(overall_records, floor=0.98)

    load_peak = frame.loc[frame["total_load"].idxmax()] if "total_load" in frame else None
    temp_peak = frame.loc[frame["peak_temperature"].idxmax()] if "peak_temperature" in frame else None
    ambient_min = frame.loc[frame["ambient_temperature"].idxmin()] if "ambient_temperature" in frame else None

    recommendations = []
    for item in temperature_bands:
        best = item.get("best_algorithm")
        data = item["by_algorithm"].get(best) if best else None
        if not data or data.get("avg_energy_per_load") is None:
            continue
        text = (
            "%s（%d 小时）：优先 %s，单位服务负载能耗 %.3f W/负载，平均 PUE %.3f，"
            "服务率 %.2f%%，平均风扇转速 %.2f。"
            % (
                item["label"], item["hours"], _display_name(best),
                data["avg_energy_per_load"], data.get("avg_pue") or 0,
                (data.get("avg_service_rate") or 0) * 100,
                data.get("avg_fan_speed") or 0,
            )
        )
        if not item.get("best_reliable"):
            text += " 该温区服务率偏低，结论只作参考。"
        recommendations.append(text)

    snapshot = {
        "run_id": meta.get("run_id"),
        "generated_at": generated_at or latest_data_time(),
        "site": meta.get("site_id"),
        "school_name": meta.get("school_name"),
        "province": meta.get("site_province"),
        "emission_region": meta.get("emission_region"),
        "num_servers": meta.get("num_servers"),
        "simulation_hours": meta.get("simulation_hours"),
        "algorithms": algorithms,
        "rows": int(len(frame)),
        "hours": len(hours),
        "controlled": meta.get("controlled"),
        "scenario_digest": meta.get("scenario_digest"),
        "scenario_source": meta.get("scenario_source"),
        "service_floor": SERVICE_FLOOR,
    }

    return {
        "snapshot": snapshot,
        "overall_best": overall_best,
        "overall_best_reliable": overall_reliable,
        "overall_best_hours": overall_best_hours,
        "safe_best_hours": safe_best_hours,
        "best_hours": best_hours,
        "time_series": time_series,
        "temperature_bands": temperature_bands,
        "temperature_thresholds": temperature_thresholds,
        "temperature_thresholds_above": temperature_thresholds_above,
        "peaks": {
            "load_peak": _hour_record(load_peak) | {"algorithm": str(load_peak["algorithm"])} if load_peak is not None else None,
            "temp_peak": _hour_record(temp_peak) | {"algorithm": str(temp_peak["algorithm"])} if temp_peak is not None else None,
            "ambient_min": _hour_record(ambient_min) | {"algorithm": str(ambient_min["algorithm"])} if ambient_min is not None else None,
        },
        "relationships": {
            "ambient_vs_pue": _corr(frame, "ambient_temperature", "pue"),
            "ambient_vs_cooling": _corr(frame, "ambient_temperature", "cooling_power"),
            "load_vs_peak_temp": _corr(frame, "total_load", "peak_temperature"),
            "load_vs_energy": _corr(frame, "total_load", "total_energy"),
            "fan_vs_cooling": _corr(frame, "fan_speed", "cooling_power"),
            "note": "相关系数只描述当前观测数据，不代表因果关系。",
        },
        "ranges": {
            "ambient_temperature": _range(frame, "ambient_temperature"),
            "total_load": _range(frame, "total_load"),
            "peak_temperature": _range(frame, "peak_temperature"),
            "pue": _range(frame, "pue"),
        },
        "recommendations": recommendations,
        "caveat": "温区建议来自当前一次仿真的观测结果，改变风扇曲线或服务器数量前建议重新运行验证。",
        "effective_hours": int(effective_rows["time"].nunique()) if not effective_rows.empty else 0,
    }


def _display_name(algorithm: str) -> str:
    return {
        "greedy": "贪心算法",
        "genetic": "遗传算法",
        "round_robin": "轮询算法",
        "damo_ets": "DAMO-ETS",
        "nsga2": "NSGA-II",
        "moead": "MOEA-D",
    }.get(algorithm, algorithm)

"""Shared metric definitions for algorithm comparison.

Everything that ranks algorithms lives here so that the CLI, the experiment
scripts, the Web API and the frontend all apply the same rule:

    1. drop every algorithm whose service rate is below the floor,
    2. rank the rest by energy per unit of actually served load.

Ranking by raw energy alone rewards an algorithm for serving less work.
"""

from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

SERVICE_RATE_FLOOR = 0.98

RANKING_RULE = "服务率 >= %.0f%% 的前提下，按单位服务负载能耗升序排名" % (
    SERVICE_RATE_FLOOR * 100.0,
)


def safe_number(value: Any) -> Optional[float]:
    """Convert to float, mapping NaN/inf/None to None so JSON stays valid."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def count_deadline_violations(tasks: Iterable[Any], last_hour: int) -> int:
    missed = 0
    for task in tasks:
        if task.missed_deadline(last_hour):
            missed += 1
    return missed


def _numeric(df: pd.DataFrame, name: str) -> Optional[pd.Series]:
    """Coerce a column to numbers, or return None when it is absent."""
    if name not in df.columns:
        return None
    return pd.to_numeric(df[name], errors="coerce")


def _first_present(df: pd.DataFrame, names: Iterable[str]) -> Optional[pd.Series]:
    for name in names:
        series = _numeric(df, name)
        if series is not None:
            return series
    return None


def summarize(df: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    """Per-algorithm summary used by every consumer of the results."""
    stats: Dict[str, Dict[str, Any]] = {}
    if df is None or df.empty or "algorithm" not in df.columns:
        return stats

    for algo in df["algorithm"].unique():
        algo_df = df[df["algorithm"] == algo]
        entry: Dict[str, Any] = {"records": int(len(algo_df))}

        energy = _numeric(algo_df, "total_energy")
        entry["avg_energy"] = safe_number(energy.mean()) if energy is not None else None
        entry["total_energy"] = safe_number(energy.sum()) if energy is not None else None

        pue = _numeric(algo_df, "pue")
        entry["avg_pue"] = safe_number(pue.mean()) if pue is not None else None

        load = _numeric(algo_df, "total_load")
        entry["total_load"] = safe_number(load.sum()) if load is not None else None

        peak = _first_present(algo_df, ("peak_temperature", "avg_temperature"))
        if peak is None:
            entry["max_temp"] = None
            entry["p95_temp"] = None
        else:
            peak = peak.dropna()
            entry["max_temp"] = safe_number(peak.max()) if len(peak) else None
            entry["p95_temp"] = safe_number(peak.quantile(0.95)) if len(peak) else None

        avg_temp = _numeric(algo_df, "avg_temperature")
        entry["avg_temp"] = safe_number(avg_temp.mean()) if avg_temp is not None else None

        assigned_series = _numeric(algo_df, "assigned")
        dropped_series = _numeric(algo_df, "dropped")
        assigned = int(assigned_series.sum()) if assigned_series is not None else 0
        dropped = int(dropped_series.sum()) if dropped_series is not None else 0
        served = assigned + dropped
        entry["assigned_tasks"] = assigned
        entry["dropped_tasks"] = dropped
        entry["service_rate"] = round(assigned / served, 4) if served else 1.0

        violations = _numeric(algo_df, "deadline_violations")
        entry["deadline_violations"] = int(violations.max()) if violations is not None else 0
        entry["deadline_violation_rate"] = (
            round(entry["deadline_violations"] / served, 4) if served else 0.0
        )

        co2 = _numeric(algo_df, "co2_kg")
        if co2 is not None:
            entry["co2_kg"] = safe_number(co2.sum())

        overheated = _numeric(algo_df, "overheated_servers")
        if overheated is not None:
            entry["overheated_hours"] = int((overheated > 0).sum())

        total_energy = entry["total_energy"]
        served = _numeric(algo_df, "served_load")
        if served is not None:
            served_sum = float(served.sum())
        else:
            served_sum = entry["total_load"] or 0.0
        entry["served_load"] = safe_number(served_sum)
        if total_energy is not None and served_sum:
            entry["energy_per_served_load"] = round(total_energy / served_sum, 4)
        else:
            entry["energy_per_served_load"] = None

        stats[algo] = entry

    return stats


def rank_algorithms(
    stats: Dict[str, Dict[str, Any]],
    floor: float = SERVICE_RATE_FLOOR,
) -> Dict[str, Any]:
    """Apply the documented ranking rule and return the full decision trace."""
    eligible: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []

    for algo, metrics in stats.items():
        rate = metrics.get("service_rate")
        rate = 1.0 if rate is None else float(rate)
        intensity = metrics.get("energy_per_served_load")
        record = {
            "algorithm": algo,
            "service_rate": round(rate, 4),
            "energy_per_served_load": intensity,
            "max_temp": metrics.get("max_temp"),
        }
        if rate < floor:
            record["reason"] = "服务率低于 %.0f%%，不参与能耗排名" % (floor * 100.0)
            excluded.append(record)
        elif intensity is None:
            record["reason"] = "缺少单位服务负载能耗，无法参与排名"
            excluded.append(record)
        else:
            eligible.append(record)

    eligible.sort(
        key=lambda item: (
            item["energy_per_served_load"],
            item["max_temp"] if item["max_temp"] is not None else float("inf"),
            item["algorithm"],
        )
    )

    for position, record in enumerate(eligible, start=1):
        record["rank"] = position

    best = eligible[0]["algorithm"] if eligible else None
    best_metrics = None
    if best is not None:
        best_metrics = {
            "algorithm": best,
            "energy_per_served_load": eligible[0]["energy_per_served_load"],
            "service_rate": eligible[0]["service_rate"],
            "avg_pue": stats[best].get("avg_pue"),
            "max_temp": stats[best].get("max_temp"),
        }

    return {
        "rule": RANKING_RULE,
        "service_rate_floor": floor,
        "eligible": eligible,
        "excluded": excluded,
        "best": best,
        "best_metrics": best_metrics,
        "note": (
            "没有算法满足服务率门槛，本轮不给出能耗排名"
            if not eligible
            else "排名基于单位服务负载能耗，已排除服务率不足的算法"
        ),
    }

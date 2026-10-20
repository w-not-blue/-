"""DAMO-ETS 权重自动寻优

在固定随机种子下枚举若干组代表性权重方案，逐组跑一次仿真并记录
能耗、温度、PUE、服务率与 deadline 违约，最后按写死的择优规则选出一组。

择优规则：先筛出任务服务率不低于阈值的方案，再在其中取平均能耗最低者；
若没有方案满足服务率阈值，则退化为取服务率最高的一组。
"""
import time
from typing import Any, Dict, List, Optional

from src.algorithms.damo_ets import DAMOETSScheduler
from src.simulation import DataCenterSimulator
from src.utils.logger import setup_logger

logger = setup_logger("weight_tuner")

SERVICE_RATE_FLOOR = 0.98
SELECTION_RULE = "服务率 >= 98% 的前提下，能耗与温度归一化后等权综合最优"

DEFAULT_SCALE = {"num_servers": 40, "simulation_hours": 8, "seed": 42}

CANDIDATES: List[Dict[str, Any]] = [
    {"name": "默认权重", "weights": {"alpha_deadline": 0.35, "beta_energy": 0.25, "gamma_thermal": 0.20, "delta_heterogeneity": 0.15, "epsilon_migration": 0.05}},
    {"name": "温度优先", "weights": {"alpha_deadline": 0.25, "beta_energy": 0.20, "gamma_thermal": 0.35, "delta_heterogeneity": 0.15, "epsilon_migration": 0.05}},
    {"name": "强温度优先", "weights": {"alpha_deadline": 0.20, "beta_energy": 0.20, "gamma_thermal": 0.45, "delta_heterogeneity": 0.10, "epsilon_migration": 0.05}},
    {"name": "能耗优先", "weights": {"alpha_deadline": 0.20, "beta_energy": 0.45, "gamma_thermal": 0.20, "delta_heterogeneity": 0.10, "epsilon_migration": 0.05}},
    {"name": "违约优先", "weights": {"alpha_deadline": 0.50, "beta_energy": 0.20, "gamma_thermal": 0.15, "delta_heterogeneity": 0.10, "epsilon_migration": 0.05}},
    {"name": "均衡方案", "weights": {"alpha_deadline": 0.30, "beta_energy": 0.25, "gamma_thermal": 0.25, "delta_heterogeneity": 0.15, "epsilon_migration": 0.05}},
    {"name": "异构匹配优先", "weights": {"alpha_deadline": 0.25, "beta_energy": 0.20, "gamma_thermal": 0.20, "delta_heterogeneity": 0.30, "epsilon_migration": 0.05}},
    {"name": "强能耗优先", "weights": {"alpha_deadline": 0.15, "beta_energy": 0.55, "gamma_thermal": 0.20, "delta_heterogeneity": 0.05, "epsilon_migration": 0.05}},
]

WEIGHT_KEYS = ["alpha_deadline", "beta_energy", "gamma_thermal", "delta_heterogeneity", "epsilon_migration"]


def _evaluate(weights: Dict[str, Any], num_servers: int, hours: int, seed: int) -> Dict[str, Any]:
    simulator = DataCenterSimulator(num_servers=num_servers, simulation_hours=hours, seed=seed)
    simulator.damo_ets_scheduler = DAMOETSScheduler(simulator.servers, config=dict(weights), seed=seed)
    frame = simulator.run_simulation("damo_ets")
    stats = simulator.get_statistics(frame)["damo_ets"]
    return {
        "avg_energy": round(stats["avg_energy"], 1),
        "max_temp": round(stats["max_temp"], 2),
        "avg_pue": round(stats["avg_pue"], 3),
        "service_rate": round(stats.get("service_rate", 1.0), 4),
        "dropped_tasks": int(stats.get("dropped_tasks", 0)),
        "deadline_violations": int(stats.get("deadline_violations", 0)),
    }


def score_row(row: Dict[str, Any], pool: List[Dict[str, Any]]) -> float:
    energies = [item["metrics"]["avg_energy"] for item in pool]
    temps = [item["metrics"]["max_temp"] for item in pool]
    e_low, e_high = min(energies), max(energies)
    t_low, t_high = min(temps), max(temps)
    energy = (row["metrics"]["avg_energy"] - e_low) / (e_high - e_low) if e_high > e_low else 0.0
    thermal = (row["metrics"]["max_temp"] - t_low) / (t_high - t_low) if t_high > t_low else 0.0
    return round(0.5 * energy + 0.5 * thermal, 4)


def pick_best(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    qualified = [row for row in rows if row["metrics"]["service_rate"] >= SERVICE_RATE_FLOOR]
    pool = qualified if qualified else rows
    best = min(pool, key=lambda row: score_row(row, pool))
    result = dict(best)
    result["score"] = score_row(best, pool)
    result["qualified"] = bool(qualified)
    return result


def run_sweep(num_servers: Optional[int] = None, hours: Optional[int] = None,
              seed: Optional[int] = None, candidates: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    scale = dict(DEFAULT_SCALE)
    if num_servers:
        scale["num_servers"] = int(num_servers)
    if hours:
        scale["simulation_hours"] = int(hours)
    if seed is not None:
        scale["seed"] = int(seed)

    plans = candidates or CANDIDATES
    started = time.perf_counter()
    rows: List[Dict[str, Any]] = []
    for plan in plans:
        metrics = _evaluate(plan["weights"], scale["num_servers"], scale["simulation_hours"], scale["seed"])
        rows.append({"name": plan["name"], "weights": dict(plan["weights"]), "metrics": metrics})
        logger.info("寻优: %s -> 能耗 %.1f W, 温度 %.2f C, 服务率 %.2f%%" % (
            plan["name"], metrics["avg_energy"], metrics["max_temp"], metrics["service_rate"] * 100))

    best = pick_best(rows)
    baseline = next((row for row in rows if row["name"] == "默认权重"), rows[0])
    return {
        "rule": SELECTION_RULE,
        "service_rate_floor": SERVICE_RATE_FLOOR,
        "scale": scale,
        "candidates": len(rows),
        "elapsed_sec": round(time.perf_counter() - started, 2),
        "rows": rows,
        "best": best,
        "baseline": baseline,
    }


def diff_weights(weights: Dict[str, Any], base: Dict[str, Any]) -> List[Dict[str, Any]]:
    changes = []
    for key in WEIGHT_KEYS:
        old = float(base.get(key, 0))
        new = float(weights.get(key, 0))
        if abs(new - old) >= 0.001:
            changes.append({"key": key, "from": old, "to": new, "delta": round(new - old, 3)})
    changes.sort(key=lambda item: abs(item["delta"]), reverse=True)
    return changes

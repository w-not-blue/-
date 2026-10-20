"""AI 目标驱动的全局方案寻优。

在后台串行评估「服务器数量 × 算法 × DAMO 权重」组合，先按用户约束过滤，
再输出 Pareto 候选、推荐方案和取舍说明。所有候选都在内存中完成，不覆盖
当前页面数据；只有用户点击「应用并运行」时才正式写入运行记录。
"""

from __future__ import annotations

import math
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from src.algorithms.damo_ets import DAMOETSScheduler
from src.services.run_manager import get_run_manager
from src.simulation import DataCenterSimulator
from src.simulation.metrics import summarize
from src.utils.logger import setup_logger
from src.utils.validation import validate_algorithms, validate_float, validate_int, validate_str

logger = setup_logger("plan_optimizer")

ALGORITHM_LABELS = {
    "greedy": "贪心算法",
    "genetic": "遗传算法",
    "round_robin": "轮询算法",
    "damo_ets": "DAMO-ETS",
    "nsga2": "NSGA-II",
    "moead": "MOEA-D",
}

TARGET_LABELS = {
    "pue": "平均 PUE 最低",
    "energy": "单位服务负载能耗最低",
    "temp": "峰值温度最低",
    "balanced": "能耗、温度、PUE 均衡",
}

WEIGHT_PROFILES = {
    "energy": {
        "label": "DAMO-ETS·能耗权重",
        "weights": {
            "alpha_deadline": 0.20,
            "beta_energy": 0.45,
            "gamma_thermal": 0.20,
            "delta_heterogeneity": 0.10,
            "epsilon_migration": 0.05,
        },
    },
    "thermal": {
        "label": "DAMO-ETS·温度权重",
        "weights": {
            "alpha_deadline": 0.25,
            "beta_energy": 0.20,
            "gamma_thermal": 0.35,
            "delta_heterogeneity": 0.15,
            "epsilon_migration": 0.05,
        },
    },
}

DEFAULT_ALGORITHMS = ["greedy", "genetic", "round_robin", "damo_ets"]
MAX_SERVERS = 500
MAX_HOURS = 72
MAX_PLANS = 3


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _round(value: Any, digits: int = 4) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _server_counts(low: int, high: int) -> List[int]:
    if high <= low:
        return [low]
    middle = int(round((low + high) / 2.0))
    values = [low, middle, high]
    result: List[int] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _evaluate(spec: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    simulator = DataCenterSimulator(
        num_servers=spec["num_servers"],
        simulation_hours=base["simulation_hours"],
        seed=base["seed"],
        data_source_override=({"site_id": base["site_id"]} if base.get("site_id") else None),
    )
    if spec.get("weights") and spec["algorithm"] == "damo_ets":
        simulator.damo_ets_scheduler = DAMOETSScheduler(
            simulator.servers, config=dict(spec["weights"]), seed=base["seed"]
        )
    frame = simulator.run_simulation(spec["algorithm"])
    stats = summarize(frame).get(spec["algorithm"]) or {}
    metrics = {
        "avg_pue": _round(stats.get("avg_pue"), 4),
        "avg_energy": _round(stats.get("avg_energy"), 1),
        "max_temp": _round(stats.get("max_temp"), 2),
        "avg_temp": _round(stats.get("avg_temp"), 2),
        "service_rate": _round(stats.get("service_rate"), 4),
        "energy_per_served_load": _round(stats.get("energy_per_served_load"), 4),
        "dropped_tasks": int(stats.get("dropped_tasks") or 0),
        "deadline_violations": int(stats.get("deadline_violations") or 0),
        "total_energy": _round(stats.get("total_energy"), 1),
    }
    return {
        "num_servers": spec["num_servers"],
        "algorithm": spec["algorithm"],
        "algorithm_label": ALGORITHM_LABELS.get(spec["algorithm"], spec["algorithm"]),
        "weights": dict(spec["weights"]) if spec.get("weights") else None,
        "weight_label": spec.get("weight_label"),
        "metrics": metrics,
        "data_source": {
            "site_id": (simulator.run_metadata or {}).get("site_id"),
            "school_name": base.get("school_name"),
            "ambient_source": (simulator.run_metadata or {}).get("ambient_source"),
            "scenario_source": (simulator.run_metadata or {}).get("scenario_source"),
        },
    }


def _metric(row: Dict[str, Any], key: str) -> float:
    value = (row.get("metrics") or {}).get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("inf")
    return number if math.isfinite(number) else float("inf")


def _normalized(value: float, low: float, high: float) -> float:
    if not math.isfinite(value):
        return 1.0
    if high <= low:
        return 0.0
    return min(1.0, max(0.0, (value - low) / (high - low)))


def _score(row: Dict[str, Any], pool: List[Dict[str, Any]], target: str) -> float:
    pue = _metric(row, "avg_pue")
    energy = _metric(row, "energy_per_served_load")
    temp = _metric(row, "max_temp")
    if target == "pue":
        return 0.0 if not math.isfinite(pue) else round(pue, 6)
    if target == "energy":
        return 0.0 if not math.isfinite(energy) else round(energy, 6)
    if target == "temp":
        return 0.0 if not math.isfinite(temp) else round(temp, 6)
    pue_values = [_metric(item, "avg_pue") for item in pool]
    energy_values = [_metric(item, "energy_per_served_load") for item in pool]
    temp_values = [_metric(item, "max_temp") for item in pool]
    pue_score = _normalized(pue, min(pue_values), max(pue_values))
    energy_score = _normalized(energy, min(energy_values), max(energy_values))
    temp_score = _normalized(temp, min(temp_values), max(temp_values))
    return round(0.45 * pue_score + 0.35 * energy_score + 0.20 * temp_score, 6)


def _dominates(left: Dict[str, Any], right: Dict[str, Any]) -> bool:
    keys = ("avg_pue", "energy_per_served_load", "max_temp")
    left_values = [_metric(left, key) for key in keys]
    right_values = [_metric(right, key) for key in keys]
    if any(not math.isfinite(value) for value in left_values):
        return False
    no_worse = all(a <= b for a, b in zip(left_values, right_values))
    strictly_better = any(a < b for a, b in zip(left_values, right_values))
    return no_worse and strictly_better


def _mark_pareto(rows: List[Dict[str, Any]]) -> int:
    count = 0
    for row in rows:
        row["pareto"] = not any(
            other is not row and _dominates(other, row)
            for other in rows
        )
        if row["pareto"]:
            count += 1
    return count


def _pick_top(rows: List[Dict[str, Any]], limit: int = MAX_PLANS) -> List[Dict[str, Any]]:
    ordered = sorted(rows, key=lambda item: item.get("score", float("inf")))
    picked: List[Dict[str, Any]] = []
    used_algorithms = set()
    for row in ordered:
        if row["algorithm"] in used_algorithms:
            continue
        picked.append(row)
        used_algorithms.add(row["algorithm"])
        if len(picked) >= limit:
            break
    used_counts = {row["num_servers"] for row in picked}
    if len(picked) < limit:
        for row in ordered:
            if row in picked or row["num_servers"] in used_counts:
                continue
            picked.append(row)
            used_counts.add(row["num_servers"])
            if len(picked) >= limit:
                break
    if len(picked) < limit:
        for row in ordered:
            if row in picked:
                continue
            picked.append(row)
            if len(picked) >= limit:
                break
    for index, row in enumerate(picked, start=1):
        row["rank"] = index
    return picked


def _plan_text(row: Dict[str, Any]) -> str:
    metrics = row.get("metrics") or {}
    parts = [
        "%d 台 + %s" % (row["num_servers"], row.get("algorithm_label") or row["algorithm"]),
    ]
    if row.get("weight_label"):
        parts[0] += "（%s）" % row["weight_label"]
    if metrics.get("avg_pue") is not None:
        parts.append("平均 PUE %.3f" % metrics["avg_pue"])
    if metrics.get("energy_per_served_load") is not None:
        parts.append("单位能耗 %.2f" % metrics["energy_per_served_load"])
    if metrics.get("max_temp") is not None:
        parts.append("峰值温度 %.1f℃" % metrics["max_temp"])
    if metrics.get("service_rate") is not None:
        parts.append("服务率 %.1f%%" % (metrics["service_rate"] * 100.0))
    return "，".join(parts)


def _explain(best: Optional[Dict[str, Any]], baseline: Optional[Dict[str, Any]],
             top: List[Dict[str, Any]], params: Dict[str, Any]) -> Dict[str, Any]:
    target_label = TARGET_LABELS.get(params["target"], params["target"])
    if not best:
        return {
            "summary": "本轮没有找到满足约束的方案，建议放宽服务率或温度限制后重试。",
            "reasons": [],
            "caution": "所有候选方案都未通过约束筛选。",
        }
    reasons = [
        "目标：%s，服务率门槛 %.1f%%。" % (target_label, params["service_rate_floor"] * 100.0),
        "最优方案：" + _plan_text(best) + "。",
    ]
    if baseline and baseline is not best:
        best_metrics = best.get("metrics") or {}
        base_metrics = baseline.get("metrics") or {}
        changes = []
        if best_metrics.get("avg_pue") is not None and base_metrics.get("avg_pue") is not None:
            delta = best_metrics["avg_pue"] - base_metrics["avg_pue"]
            changes.append("PUE %s %.3f" % ("降低" if delta <= 0 else "升高", abs(delta)))
        if best_metrics.get("energy_per_served_load") is not None and base_metrics.get("energy_per_served_load") is not None:
            delta = best_metrics["energy_per_served_load"] - base_metrics["energy_per_served_load"]
            changes.append("单位能耗 %s %.4f" % ("降低" if delta <= 0 else "升高", abs(delta)))
        if best_metrics.get("max_temp") is not None and base_metrics.get("max_temp") is not None:
            delta = best_metrics["max_temp"] - base_metrics["max_temp"]
            changes.append("峰值温度 %s %.1f℃" % ("降低" if delta <= 0 else "升高", abs(delta)))
        if changes:
            reasons.append("相比基准「%s」，%s。" % (
                "%d 台 + %s" % (baseline["num_servers"], baseline.get("algorithm_label") or baseline["algorithm"]),
                "，".join(changes),
            ))
    if len(top) > 1:
        reasons.append("备选：" + "；".join(_plan_text(row) for row in top[1:]) + "。")
    caution = ""
    if params.get("max_temp") is not None:
        caution = "峰值温度约束为 %.1f℃；如需更低温，可继续收紧温度上限后重跑。" % params["max_temp"]
    return {"summary": "推荐「%s」。" % _plan_text(best), "reasons": reasons, "caution": caution}


class PlanOptimizer:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dcs-optimize")

    def start(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        params = self._validate(raw)
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "status": "queued",
            "progress": 0,
            "done": 0,
            "total": 0,
            "message": "已提交，等待当前仿真任务结束",
            "params": params,
            "result": None,
            "error": "",
            "created_at": _now(),
            "finished_at": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run, job_id)
        return self.snapshot(job_id)

    def active(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            for job in self._jobs.values():
                if job.get("status") in ("queued", "running"):
                    return dict(job)
        return None
    def snapshot(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def apply(self, job_id: str) -> Dict[str, Any]:
        job = self.snapshot(job_id)
        if not job:
            raise ValueError("寻优任务不存在")
        if job.get("status") != "success" or not job.get("result"):
            raise ValueError("寻优尚未完成")
        result = job["result"]
        best = result.get("best")
        if not best:
            raise ValueError("没有可应用的推荐方案")
        params = job["params"]
        applied_weights = None
        if best.get("weights"):
            from src.api.ai_api import _apply_weights
            applied_weights = _apply_weights(best["weights"])
        algorithms = list(params["algorithms"])
        if best["algorithm"] not in algorithms:
            algorithms.append(best["algorithm"])
        run_params = {
            "num_servers": best["num_servers"],
            "simulation_hours": params["simulation_hours"],
            "algorithms": algorithms,
            "run_mode": "standard",
            "server_type": "mixed",
            "parallel_workers": 4,
            "site_id": params.get("site_id") or "",
            "school_name": params.get("school_name") or "",
            "visualization": True,
            "save_results": True,
        }
        manager = get_run_manager()
        run_id = manager.submit(run_params, origin="optimizer")
        return {
            "run_id": run_id,
            "params": run_params,
            "plan": best,
            "applied_weights": applied_weights,
        }

    def _validate(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        low = validate_int(raw.get("server_min"), min_val=4, max_val=MAX_SERVERS, default=48, name="最小服务器数")
        high = validate_int(raw.get("server_max"), min_val=4, max_val=MAX_SERVERS, default=max(80, low), name="最大服务器数")
        if high < low:
            raise ValueError("最大服务器数不能小于最小服务器数")
        hours = validate_int(raw.get("simulation_hours"), min_val=1, max_val=MAX_HOURS, default=24, name="仿真时长")
        target = validate_str(raw.get("target"), max_len=16, pattern=r"^(pue|energy|temp|balanced)$", default="balanced", name="优化目标")
        floor = validate_float(raw.get("service_rate_floor"), min_val=0.8, max_val=1.0, default=0.98, name="服务率门槛")
        max_temp = raw.get("max_temp")
        if max_temp in ("", None):
            max_temp = None
        else:
            max_temp = validate_float(max_temp, min_val=20.0, max_val=100.0, name="温度上限")
        algorithms = validate_algorithms(raw.get("algorithms") or DEFAULT_ALGORITHMS)
        site_id = str(raw.get("site_id") or "").strip()[:64]
        school_name = str(raw.get("school_name") or "").strip()[:128]
        return {
            "server_min": low,
            "server_max": high,
            "simulation_hours": hours,
            "target": target,
            "service_rate_floor": round(floor, 4),
            "max_temp": max_temp,
            "algorithms": algorithms,
            "include_weights": bool(raw.get("include_weights", True)),
            "seed": int(raw.get("seed") or 42),
            "site_id": site_id,
            "school_name": school_name,
        }

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.update(fields)

    def _run(self, job_id: str) -> None:
        job = self.snapshot(job_id)
        if not job:
            return
        params = job["params"]
        manager = get_run_manager()
        started = time.perf_counter()
        try:
            with manager.simulation_lock():
                self._update(job_id, status="running", message="开始评估候选方案", progress=2)
                counts = _server_counts(params["server_min"], params["server_max"])
                middle = counts[len(counts) // 2]
                stage_one: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
                for algorithm in params["algorithms"]:
                    if algorithm == "damo_ets" and params["include_weights"]:
                        for profile_key, profile in WEIGHT_PROFILES.items():
                            stage_one.append((
                                {
                                    "num_servers": middle,
                                    "algorithm": algorithm,
                                    "weights": profile["weights"],
                                    "weight_label": profile["label"],
                                },
                                params,
                            ))
                    stage_one.append((
                        {"num_servers": middle, "algorithm": algorithm},
                        params,
                    ))
                total = len(stage_one) + max(0, len(counts) - 1) * 2
                self._update(job_id, total=total)
                rows: List[Dict[str, Any]] = []
                failed: List[Dict[str, Any]] = []
                for index, (spec, base) in enumerate(stage_one, start=1):
                    self._run_candidate(job_id, spec, base, rows, failed, index, total)
                qualified = [row for row in rows if self._qualified(row, params)]
                pool = qualified or rows
                for row in rows:
                    row["score"] = _score(row, pool, params["target"])
                ranked = sorted([row for row in rows if row.get("score") is not None], key=lambda item: item["score"])
                leaders: List[Dict[str, Any]] = []
                seen = set()
                for row in ranked:
                    key = (row["algorithm"], tuple(sorted((row.get("weights") or {}).items())))
                    if key in seen:
                        continue
                    seen.add(key)
                    leaders.append(row)
                    if len(leaders) >= 2:
                        break
                for count in counts:
                    if count == middle:
                        continue
                    for leader in leaders:
                        spec = {
                            "num_servers": count,
                            "algorithm": leader["algorithm"],
                            "weights": leader.get("weights"),
                            "weight_label": leader.get("weight_label"),
                        }
                        self._run_candidate(job_id, spec, params, rows, failed, len(rows) + 1, total)
                qualified = [row for row in rows if self._qualified(row, params)]
                pool = qualified or rows
                for row in rows:
                    row["score"] = _score(row, pool, params["target"])
                    row["qualified"] = self._qualified(row, params)
                pareto_rows = qualified or rows
                pareto_count = _mark_pareto(pareto_rows)
                top = _pick_top(qualified or rows, MAX_PLANS)
                baseline = self._baseline(rows, middle)
                best = top[0] if top else None
                result = {
                    "rule": self._rule(params),
                    "target": params["target"],
                    "target_label": TARGET_LABELS.get(params["target"], params["target"]),
                    "service_rate_floor": params["service_rate_floor"],
                    "max_temp": params["max_temp"],
                    "scale": {"server_min": params["server_min"], "server_max": params["server_max"], "simulation_hours": params["simulation_hours"]},
                    "site_id": params.get("site_id"),
                    "school_name": params.get("school_name"),
                    "candidates": len(rows) + len(failed),
                    "evaluated": len(rows),
                    "failed": failed,
                    "elapsed_sec": round(time.perf_counter() - started, 2),
                    "pareto_count": pareto_count,
                    "baseline": baseline,
                    "best": best,
                    "top": top,
                }
                result["explanation"] = _explain(best, baseline, top, params)
                self._update(job_id, status="success", progress=100, done=len(rows) + len(failed), message="寻优完成", result=result, finished_at=_now())
        except Exception as exc:
            logger.error("全局寻优失败: %s", exc, exc_info=True)
            self._update(job_id, status="failed", progress=100, message="寻优失败", error=str(exc), finished_at=_now())

    def _run_candidate(self, job_id: str, spec: Dict[str, Any], base: Dict[str, Any],
                       rows: List[Dict[str, Any]], failed: List[Dict[str, Any]],
                       index: int, total: int) -> None:
        started = time.perf_counter()
        try:
            row = _evaluate(spec, base)
            rows.append(row)
            logger.info("寻优候选: %s 台 / %s -> PUE %s, 温度 %s, 服务率 %s", spec["num_servers"], spec["algorithm"], row["metrics"].get("avg_pue"), row["metrics"].get("max_temp"), row["metrics"].get("service_rate"))
        except Exception as exc:
            failed.append({
                "num_servers": spec["num_servers"],
                "algorithm": spec["algorithm"],
                "weight_label": spec.get("weight_label"),
                "error": str(exc),
            })
            logger.warning("寻优候选失败: %s 台 / %s: %s", spec["num_servers"], spec["algorithm"], exc)
        elapsed = time.perf_counter() - started
        done = len(rows) + len(failed)
        progress = int(min(95, max(3, done / max(total, 1) * 95)))
        self._update(job_id, progress=progress, done=done, total=max(total, done), message="已完成 %d/%d 组候选（最近一组 %.1f 秒）" % (done, max(total, done), elapsed))

    def _qualified(self, row: Dict[str, Any], params: Dict[str, Any]) -> bool:
        rate = (row.get("metrics") or {}).get("service_rate")
        if rate is not None and float(rate) < params["service_rate_floor"]:
            return False
        limit = params.get("max_temp")
        temp = (row.get("metrics") or {}).get("max_temp")
        if limit is not None and temp is not None and float(temp) > float(limit):
            return False
        return True

    def _baseline(self, rows: List[Dict[str, Any]], middle: int) -> Optional[Dict[str, Any]]:
        greedy = [row for row in rows if row["algorithm"] == "greedy" and row["num_servers"] == middle]
        if greedy:
            return greedy[0]
        same_count = [row for row in rows if row["num_servers"] == middle]
        return same_count[0] if same_count else (rows[0] if rows else None)

    def _rule(self, params: Dict[str, Any]) -> str:
        limit = params.get("max_temp")
        temp_text = "、峰值温度不高于 %.1f℃" % limit if limit is not None else ""
        return "先筛除服务率低于 %.1f%%%s的方案，再按「%s」排序，并输出 Pareto 候选" % (
            params["service_rate_floor"] * 100.0, temp_text, TARGET_LABELS.get(params["target"], params["target"])
        )


_optimizer: Optional[PlanOptimizer] = None
_optimizer_lock = threading.Lock()


def get_plan_optimizer() -> PlanOptimizer:
    global _optimizer
    if _optimizer is None:
        with _optimizer_lock:
            if _optimizer is None:
                _optimizer = PlanOptimizer()
    return _optimizer





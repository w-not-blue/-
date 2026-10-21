"""
MOEADSchedulerPlugin — pymoo MOEAD 分解多目标调度器 (GitHub Skills 集成)

与 NSGA2 同构（同样的 Problem 定义），但用基于参考向量分解的 MOEAD 算法。

差异:
    - 算法: ``MOEAD(ref_dirs=get_reference_directions("das-dennis", 3, n_partitions),
                    n_neighbors, prob_neighbor_mating)``
    - 解的选择：在 ref_dirs 均匀覆盖下挑与偏好向量夹角最小的解
    - 解码阶段：与 NSGA2 相同（整数编码 → server_id 列表）

依赖:
    pip install pymoo

来源: https://github.com/anyoptimization/pymoo (Apache 2.0)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.plugin.base import PluginBase
from src.plugin.protocol import PluginManifest, PluginResult
from src.plugins._deps import has_pymoo


OVERLOAD_PENALTY = 50.0


class MOEADSchedulerPlugin(PluginBase):
    """pymoo MOEAD 多目标调度器（基于参考向量分解）"""

    # -------------------------------------------------------------------------
    # 清单 / 配置
    # -------------------------------------------------------------------------

    def get_manifest(self) -> PluginManifest:
        return PluginManifest(
            name="MOEADSchedulerPlugin",
            version="1.0.0",
            supported_operations=["schedule"],
            input_schema={
                "n_partitions": {"type": "int", "default": 12, "min": 2},
                "n_neighbors": {"type": "int", "default": 15, "min": 2},
                "prob_neighbor_mating": {"type": "float", "default": 0.7, "min": 0.0, "max": 1.0},
                "n_gen": {"type": "int", "default": 200, "min": 1},
                "selection": {
                    "type": "str",
                    "enum": ["energy_priority", "balanced", "temp_priority"],
                    "default": "balanced",
                },
                "seed": {"type": "int", "default": 42},
            },
            output_schema={
                "assignments": "list",
                "pareto_size": "int",
                "generation": "int",
                "objectives": "dict",
            },
            min_system_version="1.0.0",
            category="algorithm",
        )

    def validate_config(self, config: Dict[str, Any]) -> bool:
        n_partitions = config.get("n_partitions", 12)
        if not isinstance(n_partitions, int) or n_partitions < 2:
            self.logger.error(f"n_partitions 必须 >= 2, 当前={n_partitions}")
            return False
        n_neighbors = config.get("n_neighbors", 15)
        if not isinstance(n_neighbors, int) or n_neighbors < 2:
            self.logger.error(f"n_neighbors 必须 >= 2, 当前={n_neighbors}")
            return False
        n_gen = config.get("n_gen", 200)
        if not isinstance(n_gen, int) or n_gen < 1:
            self.logger.error(f"n_gen 必须 >= 1, 当前={n_gen}")
            return False
        return True

    # -------------------------------------------------------------------------
    # 生命周期
    # -------------------------------------------------------------------------

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        cfg = dict(config or {})
        if not self.validate_config(cfg):
            return False
        self._deps_ok = has_pymoo()
        self._n_partitions: int = int(cfg.get("n_partitions", 12))
        self._n_neighbors: int = int(cfg.get("n_neighbors", 15))
        self._prob_neighbor_mating: float = float(cfg.get("prob_neighbor_mating", 0.7))
        self._n_gen: int = int(cfg.get("n_gen", 200))
        self._selection: str = str(cfg.get("selection", "balanced"))
        self._seed: Optional[int] = cfg.get("seed", 42)
        return True

    def cleanup(self) -> None:
        return None

    # -------------------------------------------------------------------------
    # 执行
    # -------------------------------------------------------------------------

    def execute(self, **kwargs: Any) -> PluginResult:
        if not has_pymoo():
            return PluginResult.fail(
                message="pymoo 未安装。请执行: pip install pymoo>=0.6",
                error_code="DEPENDENCY_MISSING",
            )
        tasks: Optional[List[Any]] = kwargs.get("tasks")
        servers: Optional[List[Any]] = kwargs.get("servers")
        if not tasks or servers is None:
            return PluginResult.fail(
                message="MOEAD 调度需要 tasks 与 servers 参数",
                error_code="MISSING_PARAM",
            )
        try:
            return self._solve(tasks, servers)
        except Exception as e:
            self.logger.error(f"MOEAD 调度失败: {e}", exc_info=True)
            return PluginResult.fail(
                message=f"MOEAD 调度失败: {e}",
                error_code="MOEAD_ERROR",
                error_detail=str(e),
            )

    # -------------------------------------------------------------------------
    # 内部
    # -------------------------------------------------------------------------

    def _solve(self, tasks: List[Any], servers: List[Any]) -> PluginResult:
        from pymoo.core.problem import ElementwiseProblem
        from pymoo.algorithms.moo.moead import MOEAD
        from pymoo.operators.crossover.sbx import SBX
        from pymoo.operators.mutation.pm import PM
        from pymoo.operators.repair.rounding import RoundingRepair
        from pymoo.operators.sampling.rnd import IntegerRandomSampling
        from pymoo.optimize import minimize
        from pymoo.termination import get_termination
        from pymoo.util.ref_dirs import get_reference_directions

        num_tasks = len(tasks)
        num_servers = len(servers)
        if num_tasks == 0 or num_servers == 0:
            return PluginResult.fail(
                message="tasks 与 servers 均不能为空",
                error_code="EMPTY_INPUT",
            )

        task_loads = [float(t.load) for t in tasks]
        task_deadlines = [
            t.deadline if t.deadline is not None else 10**9 for t in tasks
        ]
        max_loads = [float(s.max_load) for s in servers]
        base_powers = [float(s.base_power) for s in servers]
        max_powers = [float(s.max_power) for s in servers]
        init_loads = [float(s.current_load) for s in servers]
        max_loads_safe = [max(ml, 1.0) for ml in max_loads]

        class SchedulingProblem(ElementwiseProblem):
            def __init__(self_inner):
                super().__init__(
                    n_var=num_tasks,
                    n_obj=3,
                    n_ieq_constr=0,
                    xl=0,
                    xu=num_servers - 1,
                )

            def _evaluate(self_inner, x, out, *args, **kwargs):
                assign = [int(round(float(v))) for v in x]
                assign = [max(0, min(num_servers - 1, a)) for a in assign]
                srv_load = list(init_loads)
                for i, s_idx in enumerate(assign):
                    srv_load[s_idx] += task_loads[i]
                total_energy = 0.0
                for j in range(num_servers):
                    lf = srv_load[j] / max_loads_safe[j]
                    total_energy += base_powers[j] + (max_powers[j] - base_powers[j]) * lf
                lfs = [srv_load[j] / max_loads_safe[j] for j in range(num_servers)]
                max_temp_proxy = 25.0 + 60.0 * max(lfs) if lfs else 25.0
                violations = 0
                for i, s_idx in enumerate(assign):
                    if task_deadlines[i] < 10 and (
                        srv_load[s_idx] / max_loads_safe[s_idx] > 0.9
                    ):
                        violations += 1
                overload = sum(
                    max(0.0, srv_load[j] - max_loads[j]) for j in range(num_servers)
                )
                out["F"] = [float(total_energy + overload * OVERLOAD_PENALTY),
                            float(max_temp_proxy), float(violations)]

        # 参考方向 (das-dennis)
        ref_dirs = get_reference_directions("das-dennis", 3, n_partitions=self._n_partitions)
        algorithm = MOEAD(
            ref_dirs=ref_dirs,
            n_neighbors=self._n_neighbors,
            prob_neighbor_mating=self._prob_neighbor_mating,
            crossover=SBX(prob=0.9, eta=3.0, repair=RoundingRepair()),
            mutation=PM(prob=1.0 / max(num_tasks, 1), eta=3.0, repair=RoundingRepair()),
        )
        res = minimize(
            SchedulingProblem(),
            algorithm,
            termination=get_termination("n_gen", self._n_gen),
            seed=self._seed,
            verbose=False,
        )

        if res.X is None or (hasattr(res.X, "shape") and res.X.shape[0] == 0):
            return PluginResult.fail(
                message="MOEAD 未找到可行解",
                error_code="NO_SOLUTION",
            )

        F = res.F
        X = res.X
        if len(F.shape) == 1:
            F = F.reshape(1, -1)
            X = X.reshape(1, -1)
        idx = self._select_by_preference(F, ref_dirs)
        best_x = X[idx]
        assign = [int(round(float(v))) for v in best_x]
        assign = [max(0, min(num_servers - 1, a)) for a in assign]

        assignments: List[Dict[str, Any]] = []
        for i, s_idx in enumerate(assign):
            assignments.append(
                {
                    "task_id": getattr(tasks[i], "task_id", str(i)),
                    "server_id": s_idx,
                    "load": task_loads[i],
                }
            )
        return PluginResult.ok(
            data={
                "assignments": assignments,
                "pareto_size": int(F.shape[0]),
                "generation": self._n_gen,
                "objectives": {
                    "energy": float(F[idx, 0]),
                    "max_temp_proxy": float(F[idx, 1]),
                    "deadline_violations": float(F[idx, 2]),
                },
                "selection": self._selection,
            },
            message=f"MOEAD 调度完成: {len(assignments)} 个任务, pareto={F.shape[0]}",
        )

    def _select_by_preference(self, F: Any, ref_dirs: Any) -> int:
        """
        在 ref_dirs 均匀覆盖下，挑 weighted-sum 最小的解。
        （若 ref_dirs 与 F 维度不一致，则退化为加权和最小）
        """
        import numpy as np
        selection_weights = {
            "energy_priority": (1.0, 0.3, 5.0),
            "balanced": (1.0, 1.0, 10.0),
            "temp_priority": (0.3, 1.0, 5.0),
        }
        w = selection_weights.get(self._selection, (1.0, 1.0, 10.0))
        Fs = np.asarray(F, dtype=float)
        mins = Fs.min(axis=0)
        maxs = Fs.max(axis=0)
        rng = np.where((maxs - mins) > 0, (maxs - mins), 1.0)
        norm = (Fs - mins) / rng
        scores = norm @ np.array(w, dtype=float)
        return int(np.argmin(scores))


__all__ = ["MOEADSchedulerPlugin"]

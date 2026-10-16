"""
DAMO-ETS: Deadline-Aware Multi-Objective Energy-Thermal Scheduler.

Combines:
    * Earliest-Deadline-First (EDF) ordering
    * Per-(task, server) cost aggregation across five objectives
        - deadline urgency
        - incremental energy
        - predicted thermal impact
        - heterogeneous workload/server match
        - cross-type migration penalty
    * Tie-break randomness that is fully controlled by the injected seed

Public surface:
    * DAMOETSScheduler : BaseScheduler subclass used by the simulator
    * deadline_violations : count of tasks whose deadline was missed in the
      last ``schedule_tasks`` call
"""

from typing import Any, Dict, List, Optional, Tuple
import random

from src.algorithms.base import BaseScheduler
from src.models import Server, Task
from src.utils.config import Config
from src.utils.logger import setup_logger

logger = setup_logger("damo_ets")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_get(d: Any, key: str, default: Any = None) -> Any:
    """Return ``d[key]`` whether ``d`` is a dict or a SectionAccessor."""
    if d is None:
        return default
    if isinstance(d, dict):
        return d.get(key, default)
    getter = getattr(d, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except Exception:
            return default
    return getattr(d, key, default)


def _resolve_seed(seed: Optional[int]) -> Optional[int]:
    if seed is not None:
        return int(seed)
    try:
        cfg = Config().get("simulation", {}) or {}
        val = cfg.get("random_seed")
        return int(val) if val is not None else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

class DAMOETSScheduler(BaseScheduler):
    """Multi-objective energy-thermal scheduler with deadline awareness."""

    DEFAULT_CONFIG: Dict[str, Any] = {
        "alpha_deadline": 0.35,
        "beta_energy": 0.25,
        "gamma_thermal": 0.20,
        "delta_heterogeneity": 0.15,
        "epsilon_migration": 0.05,
        "heat_coefficient": 15.0,
        "thermal_safety_factor": 0.95,
        "workload_server_match": {
            "gpu_bound":    ["gpu_server", "high_performance"],
            "cpu_bound":    ["high_performance", "standard"],
            "memory_bound": ["standard"],
            "io_bound":     ["standard"],
        },
    }

    def __init__(
        self,
        servers: List[Server],
        config: Optional[Dict[str, Any]] = None,
        seed: Optional[int] = None,
    ) -> None:
        super().__init__(servers)

        # Merge YAML-driven config with defaults
        yaml_cfg: Dict[str, Any] = {}
        try:
            damo_section = Config().get("damo_ets", {}) or {}
            if isinstance(damo_section, dict):
                yaml_cfg = damo_section
        except Exception:
            yaml_cfg = {}

        merged: Dict[str, Any] = {}
        merged.update(self.DEFAULT_CONFIG)
        merged.update(yaml_cfg or {})
        if config:
            merged.update(config)
        self.config: Dict[str, Any] = merged

        self.match_table: Dict[str, List[str]] = dict(
            self.config.get("workload_server_match") or
            self.DEFAULT_CONFIG["workload_server_match"]
        )

        self.seed = _resolve_seed(seed)
        self.rng = random.Random(self.seed)

        # Track number of deadline violations after each schedule call
        self.deadline_violations: int = 0

        # Last server type assigned — used for migration penalty
        self._last_server_type: Optional[str] = None

    # ------------------------------------------------------------------ utils
    def get_name(self) -> str:
        return "damo_ets"

    def get_deadline_violations(self) -> int:
        """Tasks that were already past their deadline when they were scheduled.

        This is a per-scheduler diagnostic only. The metric reported for
        comparison is computed by the simulator from completion times, so every
        algorithm is measured with the same rule.
        """
        return int(self.deadline_violations)

    def reset(self) -> None:
        self.deadline_violations = 0
        self._last_server_type = None

    # ---------------------------------------------------------- cost function
    def _deadline_urgency(self, task: Task, current_hour: int) -> float:
        if task.deadline is None:
            return 0.0
        remaining = max(1, task.deadline - current_hour)
        # Closer to deadline -> higher urgency, normalised to [0, 1]
        return float(min(1.0, 1.0 / remaining))

    def _energy_impact(self, task: Task, server: Server) -> float:
        """Marginal power of putting this task here, normalised to [0, 1].

        The plant charges base power for every machine that is switched on, so
        the useful signal is the *increase* over what the server draws now, not
        its absolute utilisation. Preferring an empty machine (the previous
        behaviour) actively pushed load onto idle servers and paid their base
        power, which is the opposite of what saves energy.
        """
        before = server.calculate_power()
        projected = min(
            server.max_load,
            server.current_load + task.load,
        )
        load_ratio = projected / max(1.0, server.max_load)
        after = server.base_power + (server.max_power - server.base_power) * load_ratio
        delta = max(0.0, after - before)
        return float(min(1.0, delta / 200.0))

    def _thermal_impact(self, task: Task, server: Server) -> float:
        predicted = server.predicted_temp_after(task.load)
        safe = server.max_temp * self.config.get("thermal_safety_factor", 0.95)
        if predicted >= safe:
            return 1.0
        return float(predicted / safe)

    def _heterogeneity_match(self, task: Task, server: Server) -> float:
        preferred = self.match_table.get(task.workload_class, [])
        try:
            rank = preferred.index(server.server_type)
        except ValueError:
            rank = len(preferred)  # worst rank
        # 1.0 for best match, decreasing to 0
        denom = max(1, len(preferred))
        return float(max(0.0, 1.0 - rank / denom))

    def _migration_penalty(self, server: Server) -> float:
        if self._last_server_type is None or self._last_server_type == server.server_type:
            return 0.0
        return 1.0

    def _cost(self, task: Task, server: Server, current_hour: int) -> float:
        return (
            self.config["alpha_deadline"]     * self._deadline_urgency(task, current_hour)
            + self.config["beta_energy"]      * self._energy_impact(task, server)
            + self.config["gamma_thermal"]    * self._thermal_impact(task, server)
            + self.config["delta_heterogeneity"] * (1.0 - self._heterogeneity_match(task, server))
            + self.config["epsilon_migration"] * self._migration_penalty(server)
        )

    # -------------------------------------------------------------- main API
    def schedule_tasks(
        self,
        tasks: List[Task],
        current_hour: Optional[int] = None,
    ) -> List[Tuple[Task, Optional[Server]]]:
        self.reset()

        if not tasks:
            return []

        current_hour = int(current_hour) if current_hour is not None else 0

        # ---- EDF ordering (tasks with earlier deadline first, None last) ----
        ordered = sorted(
            tasks,
            key=lambda t: (t.deadline is None, t.deadline if t.deadline is not None else 0, -t.priority),
        )

        results: List[Tuple[Task, Optional[Server]]] = []

        for task in ordered:
            chosen: Optional[Server] = None
            best_cost = float("inf")
            best_tie_break = float("inf")
            candidates: List[Tuple[float, float, Server]] = []

            safe_factor = self.config.get("thermal_safety_factor", 0.95)

            for server in self.servers:
                # Hard constraints first
                if server.is_overloaded():
                    continue
                if server.current_load + task.load > server.max_load:
                    continue
                # Reject if predicted temp exceeds safety threshold
                if server.predicted_temp_after(task.load) >= server.max_temp * safe_factor:
                    continue

                cost = self._cost(task, server, current_hour)
                # Tie-break: prefer lower current load, then smaller id
                tie = (server.current_load, server.server_id)
                candidates.append((cost, tie[0] * 1000 + tie[1], server))

            if candidates:
                candidates.sort(key=lambda c: (c[0], c[1]))
                # Inject seeded randomness into tie-break (perturb cost by small
                # noise drawn from rng). Keeps reproducibility intact.
                top_cost = candidates[0][0]
                near_top = [c for c in candidates if c[0] - top_cost < 1e-9]
                if len(near_top) > 1:
                    pick = self.rng.choice(near_top)
                else:
                    pick = candidates[0]
                chosen = pick[2]

            if chosen is not None and chosen.assign_task(task):
                task.start_time = current_hour
                results.append((task, chosen))
                self._last_server_type = chosen.server_type
            else:
                results.append((task, None))

            # Deadline accounting: violations are counted relative to the
            # *assignment hour*, regardless of when the task would finish.
            if task.is_deadline_violated(current_hour):
                self.deadline_violations += 1

        return results

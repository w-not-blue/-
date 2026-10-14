import hashlib
import json
import numpy as np
from typing import List, Dict, Any, Optional, Tuple
from src.models import Task

TaskSpec = Tuple[int, int, str, int, int]


class TaskGenerator:
    DEFAULT_TASK_TYPES: List[Dict[str, Any]] = [
        {"name": "cpu_bound",    "proportion": 0.4, "load_range": [10, 50], "duration_range": [1, 5]},
        {"name": "gpu_bound",    "proportion": 0.3, "load_range": [20, 80], "duration_range": [2, 10]},
        {"name": "memory_bound", "proportion": 0.2, "load_range": [5, 25],  "duration_range": [3, 8]},
        {"name": "io_bound",     "proportion": 0.1, "load_range": [3, 15],  "duration_range": [1, 3]},
    ]

    def __init__(
        self,
        base_rate: int = 10,
        peak_rate: int = 50,
        peak_hours: tuple = (9, 18),
        task_types: Optional[List[Dict[str, Any]]] = None,
        seed: Optional[int] = None,
        sla_slack: float = 1.5,
    ) -> None:
        self.base_rate: int = base_rate
        self.peak_rate: int = peak_rate
        self.peak_hours: tuple = peak_hours
        self.task_types: List[Dict[str, Any]] = task_types or self.DEFAULT_TASK_TYPES
        self.sla_slack: float = float(sla_slack)
        self.rng = np.random.default_rng(seed)
        self.np_rng = np.random.default_rng(seed)

    def get_rate_at_hour(self, hour: int) -> int:
        start, end = self.peak_hours
        if start <= hour < end:
            return self.peak_rate
        return self.base_rate

    def _sample_workload_class(self) -> str:
        names = [t["name"] for t in self.task_types]
        weights = [float(t.get("proportion", 0)) for t in self.task_types]
        total = sum(weights)
        if total <= 0:
            return names[0] if names else "cpu_bound"
        probs = [w / total for w in weights]
        return str(self.rng.choice(names, p=probs))

    def _range_for(self, workload_class: str, key: str) -> tuple:
        for t in self.task_types:
            if t.get("name") == workload_class:
                rng = t.get(key)
                if isinstance(rng, (list, tuple)) and len(rng) == 2:
                    return int(rng[0]), int(rng[1])
        return 5, 30

    def _deadline_offset(self, duration: int) -> int:
        """Deadline relative to arrival: duration scaled by the SLA slack factor.

        ``sla_slack=1.0`` means zero queueing tolerance, so any task that is not
        served immediately misses its deadline.
        """
        scaled = int(round(duration * self.sla_slack))
        return max(duration, scaled, 1)

    def generate_tasks(self, current_time: int) -> List[Task]:
        return [self._materialize(spec, current_time) for spec in self.generate_specs(current_time)]

    def generate_specs(self, current_time: int) -> List[TaskSpec]:
        hour = current_time % 24
        rate = self.get_rate_at_hour(hour)
        num_tasks = int(self.rng.poisson(rate))
        specs: List[TaskSpec] = []

        for _ in range(num_tasks):
            workload_class = self._sample_workload_class()
            lo, hi = self._range_for(workload_class, "load_range")
            load = int(self.rng.integers(lo, hi + 1))
            priority = int(self.rng.integers(1, 6))

            d_lo, d_hi = self._range_for(workload_class, "duration_range")
            duration = int(self.rng.integers(d_lo, d_hi + 1))
            specs.append(
                (load, priority, workload_class, duration, self._deadline_offset(duration))
            )

        return specs

    def build_stream(self, hours: int) -> List[List[TaskSpec]]:
        """Generate the whole scenario once.

        Every algorithm replays this exact stream, which is what makes the
        cross-algorithm comparison controlled instead of four separate runs.
        """
        return [self.generate_specs(hour) for hour in range(hours)]

    @staticmethod
    def _materialize(spec: TaskSpec, arrival_hour: int) -> Task:
        load, priority, workload_class, duration, sla_offset = spec
        task = Task(
            load=load,
            priority=priority,
            deadline=arrival_hour + sla_offset,
            arrival_time=arrival_hour,
            workload_class=workload_class,
            duration=duration,
        )
        task.created_time = arrival_hour
        return task

    def materialize_stream(self, stream: List[List[TaskSpec]]) -> List[List[Task]]:
        """Build fresh Task objects for one algorithm run.

        Tasks carry mutable state (load on a server, start and completion
        times), so each run needs its own instances even though the stream
        values are shared.
        """
        return [
            [self._materialize(spec, hour) for spec in hour_specs]
            for hour, hour_specs in enumerate(stream)
        ]

    @staticmethod
    def stream_digest(stream: List[List[TaskSpec]]) -> str:
        payload = json.dumps(stream, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def generate_daily_profile(self) -> List[Dict]:
        profile = []
        for hour in range(24):
            rate = self.get_rate_at_hour(hour)
            profile.append({
                'hour': hour,
                'rate': rate,
                'is_peak': self.peak_hours[0] <= hour < self.peak_hours[1]
            })
        return profile

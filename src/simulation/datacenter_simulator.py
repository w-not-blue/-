import inspect
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from src.models import Server, EnergyModel, TemperatureModel, Task
from src.algorithms import (
    GreedyScheduler,
    GeneticScheduler,
    RoundRobinScheduler,
    DAMOETSScheduler,
    BaseScheduler,
    PLUGIN_ALGORITHMS,
    PluginSchedulerAdapter,
    is_plugin_algorithm,
)
from src.simulation.metrics import (
    SERVICE_RATE_FLOOR,
    count_deadline_violations,
    rank_algorithms,
    safe_number,
    summarize,
)
from src.simulation.task_generator import TaskGenerator, TaskSpec
from src.utils.config import Config
from src.utils.logger import setup_logger


DEFAULT_ALGORITHMS = ['greedy', 'genetic', 'round_robin', 'damo_ets']


@dataclass
class ServerSnapshot:
    server_id: int
    server_type: str
    max_load: float
    max_temp: float
    base_power: float
    max_power: float
    current_temp: float
    current_load: float
    idle_power_factor: float = 0.6
    dissipation_coefficient: float = 8.0
    thermal_inertia: float = 0.25


def _build_servers(
    num_servers: int,
    server_types_cfg: Optional[List[Dict[str, Any]]],
    idle_power_factor: float = 0.6,
    dissipation_coefficient: float = 8.0,
    thermal_inertia: float = 0.25,
) -> List[Server]:
    """Build a heterogeneous server list using ``server_types`` proportions."""
    thermal = dict(
        idle_power_factor=idle_power_factor,
        dissipation_coefficient=dissipation_coefficient,
        thermal_inertia=thermal_inertia,
    )
    if not server_types_cfg:
        return [Server(server_id=i, **thermal) for i in range(num_servers)]

    proportions = [float(t.get("proportion", 0)) for t in server_types_cfg]
    total = sum(proportions)
    if total <= 0:
        return [Server(server_id=i, **thermal) for i in range(num_servers)]

    servers: List[Server] = []
    sid = 0
    allocated = 0
    for idx, t in enumerate(server_types_cfg):
        if idx == len(server_types_cfg) - 1:
            count = num_servers - allocated
        else:
            count = int(round(num_servers * proportions[idx] / total))
            allocated += count
        for _ in range(count):
            if sid >= num_servers:
                break
            servers.append(Server(
                server_id=sid,
                max_load=float(t.get("max_load", 100)),
                max_temp=float(t.get("max_temp", 85)),
                base_power=float(t.get("base_power", 100)),
                max_power=float(t.get("max_power", 300)),
                server_type=str(t.get("name", "standard")),
                **thermal,
            ))
            sid += 1
        if sid >= num_servers:
            break

    while len(servers) < num_servers:
        servers.append(Server(server_id=len(servers), **thermal))
    return servers


class DataCenterSimulator:
    """Hour-by-hour data centre simulation with controlled algorithm comparison."""

    def __init__(
        self,
        num_servers: Optional[int] = None,
        simulation_hours: Optional[int] = None,
        seed: Optional[int] = None,
        config: Optional[Config] = None,
        data_source_override: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.logger = setup_logger("datacenter_sim")

        cfg_data: Dict[str, Any] = {}
        try:
            cfg = config or Config()
            cfg_data = cfg.to_dict()
        except Exception:
            cfg_data = {}

        if data_source_override:
            merged_source = dict(cfg_data.get("data_source", {}) or {})
            merged_source.update(data_source_override)
            cfg_data["data_source"] = merged_source

        sim_cfg = cfg_data.get("simulation", {}) or {}
        self.num_servers: int = int(
            num_servers if num_servers is not None else sim_cfg.get("num_servers", 10)
        )
        self.simulation_hours: int = int(
            simulation_hours if simulation_hours is not None
            else sim_cfg.get("simulation_hours", 24)
        )
        if seed is None:
            yaml_seed = sim_cfg.get("random_seed")
            seed = int(yaml_seed) if yaml_seed is not None else None
        self.seed = seed

        server_types_cfg = cfg_data.get("server_types")
        if not isinstance(server_types_cfg, list):
            server_types_cfg = None

        task_gen_cfg = cfg_data.get("task_generation", {}) or {}
        task_types_cfg = task_gen_cfg.get("task_types")
        if not isinstance(task_types_cfg, list):
            task_types_cfg = None

        self.sla_slack: float = float(sim_cfg.get("sla_slack", 1.5))
        completion_cfg = cfg_data.get("task_completion", {}) or {}
        self.completion_mode: str = str(completion_cfg.get("mode", "service_based"))
        self.completion_probability: float = float(completion_cfg.get("completion_probability", 0.3))

        cooling_cfg = cfg_data.get("cooling", {}) or {}
        self.cooling_power_coefficient: float = float(cooling_cfg.get("power_coefficient", 500))
        self.cooling_target_temp: float = float(cooling_cfg.get("target_temp", 22))

        energy_cfg = cfg_data.get("energy", {}) or {}
        self.idle_power_factor: float = float(energy_cfg.get("idle_power_factor", 0.6))
        self.dissipation_coefficient: float = float(cooling_cfg.get("dissipation_coefficient", 8.0))
        self.thermal_inertia: float = float(cooling_cfg.get("thermal_inertia", 0.25))
        self.fan_target_temp: float = float(cooling_cfg.get("fan_target_temp", 35))

        self.servers: List[Server] = _build_servers(
            self.num_servers,
            server_types_cfg,
            self.idle_power_factor,
            self.dissipation_coefficient,
            self.thermal_inertia,
        )
        self.task_generator: TaskGenerator = TaskGenerator(
            base_rate=int(task_gen_cfg.get("base_rate", 10)),
            peak_rate=int(task_gen_cfg.get("peak_rate", 50)),
            peak_hours=tuple(task_gen_cfg.get("peak_hours", (9, 18))),
            task_types=task_types_cfg,
            seed=seed,
            sla_slack=self.sla_slack,
        )
        self.energy_model: EnergyModel = EnergyModel(cfg_data.get("energy"))
        self.temp_model: TemperatureModel = TemperatureModel(fan_target_temp=self.fan_target_temp)
        self._build_schedulers()

        self.results: Dict[str, List[Any]] = {
            'time': [],
            'total_load': [],
            'avg_temperature': [],
            'peak_temperature': [],
            'overheated_servers': [],
            'ambient_temperature': [],
            'total_energy': [],
            'pue': [],
            'cooling_power': [],
            'fan_speed': [],
            'algorithm': [],
            'deadline_violations': [],
            'assigned': [],
            'dropped': [],
            'offered_tasks': [],
            'offered_load': [],
            'served_load': [],
            'co2_kg': [],
        }

        self._stream: Optional[List[List[TaskSpec]]] = None
        self._seen_tasks: List[Task] = []
        self.scenario_digest: Optional[str] = None
        self.skipped_algorithms: List[str] = []
        self.raw_results: Optional[pd.DataFrame] = None
        self.statistics: Dict[str, Dict[str, Any]] = {}
        self.ranking: Dict[str, Any] = {}

        self.catalog = None
        self.ambient_series: Optional[List[float]] = None
        self.site_province: Optional[str] = None
        self.emission_region: Optional[str] = None
        self.emission_factor: Optional[float] = None
        self.trace_stream: Optional[List[List[TaskSpec]]] = None
        self.data_source_cfg: Dict[str, Any] = cfg_data.get("data_source", {}) or {}
        self.ambient_offset: int = int(self.data_source_cfg.get("ambient_start_hour", 0) or 0)
        self._load_catalog_data()

        self.run_metadata: Dict[str, Any] = {
            "data_mode": "catalog" if self.ambient_series else "synthetic",
            "ambient_source": "catalog" if self.ambient_series else "synthetic-sine",
            "sla_slack": self.sla_slack,
            "completion_mode": self.completion_mode,
            "service_rate_floor": SERVICE_RATE_FLOOR,
            "emission_source": "catalog" if self.emission_factor else None,
            "site_id": self.data_source_cfg.get("site_id"),
            "site_province": self.site_province,
            "emission_region": self.emission_region,
        }

    # ------------------------------------------------------------------ setup
    def _load_catalog_data(self) -> None:
        mode = str(self.data_source_cfg.get("mode", "synthetic")).lower()
        if mode != "catalog":
            return
        try:
            from src.data.catalog import Catalog, region_for_province
        except Exception as exc:
            self.logger.warning("数据目录模块不可用，回退到合成数据: %s", exc)
            return

        try:
            catalog = Catalog(self.data_source_cfg.get("catalog_db"))
        except Exception as exc:
            self.logger.warning("打开数据目录失败，回退到合成数据: %s", exc)
            return

        if not catalog.available():
            self.logger.warning(
                "未找到可用的数据目录 %s，回退到合成数据（演示前请先执行 tools/etl 下的导入脚本）",
                catalog.db_path,
            )
            return

        self.catalog = catalog
        site_id = self.data_source_cfg.get("site_id")
        self.site_province = catalog.city_province(site_id) if site_id else None
        self.ambient_series = catalog.ambient_series(site_id)

        self.trace_stream = catalog.task_stream(
            self.data_source_cfg.get("trace_id"),
            self.simulation_hours,
        )
        self.emission_region = region_for_province(self.site_province)
        self.emission_factor = catalog.emission_factor(self.emission_region)

        if self.ambient_series:
            self.logger.info("环境温度来自数据目录，共 %d 个小时值", len(self.ambient_series))
        if self.trace_stream:
            self.logger.info("任务流来自数据目录，共 %d 个小时", len(self.trace_stream))

    def build_scenario(self, hours: Optional[int] = None) -> List[List[TaskSpec]]:
        """Freeze the task stream for one scenario.

        Generated once and replayed by every algorithm, so the comparison is
        controlled: all schedulers face the identical demand sequence.
        """
        hours = self.simulation_hours if hours is None else hours
        if self.trace_stream:
            source = self.trace_stream
            self._stream = [list(source[h % len(source)]) for h in range(hours)]
            if hours > len(source):
                self.run_metadata["scenario_cycle_hours"] = len(source)
        else:
            self._stream = self.task_generator.build_stream(hours)
        self.scenario_digest = TaskGenerator.stream_digest(self._stream)
        self.run_metadata["scenario_digest"] = self.scenario_digest
        self.run_metadata["scenario_tasks"] = int(sum(len(h) for h in self._stream))
        self.run_metadata["scenario_source"] = "catalog" if self.trace_stream else "synthetic"
        return self._stream

    def describe_data_sources(self) -> Dict[str, Any]:
        sources = dict(self.run_metadata)
        if self.catalog is not None:
            sources["provenance"] = self.catalog.provenance()
        return sources

    # ----------------------------------------------------------------- helpers
    def _build_schedulers(self) -> None:
        greedy_cfg: Dict[str, Any] = {}
        try:
            greedy_cfg = Config().get("greedy", {}) or {}
        except Exception:
            greedy_cfg = {}
        self.greedy_scheduler: GreedyScheduler = GreedyScheduler(
            self.servers,
            temperature_weight=float(greedy_cfg.get("temperature_weight", 0.3)),
        )
        self.genetic_scheduler: GeneticScheduler = GeneticScheduler(self.servers, seed=self.seed)
        self.round_robin_scheduler: RoundRobinScheduler = RoundRobinScheduler(self.servers)
        self.damo_ets_scheduler: DAMOETSScheduler = DAMOETSScheduler(self.servers, seed=self.seed)
        self.temp_model = TemperatureModel(
            fan_target_temp=getattr(self, "fan_target_temp", 35)
        )

    def create_snapshot(self) -> List[ServerSnapshot]:
        return [ServerSnapshot(
            server_id=s.server_id,
            server_type=s.server_type,
            max_load=s.max_load,
            max_temp=s.max_temp,
            base_power=s.base_power,
            max_power=s.max_power,
            current_temp=s.current_temp,
            current_load=s.current_load,
            idle_power_factor=s.idle_power_factor,
            dissipation_coefficient=s.dissipation_coefficient,
            thermal_inertia=s.thermal_inertia,
        ) for s in self.servers]

    def restore_from_snapshot(self, snapshot: List[ServerSnapshot]) -> None:
        self.servers = [Server(
            server_id=snap.server_id,
            max_load=snap.max_load,
            max_temp=snap.max_temp,
            base_power=snap.base_power,
            max_power=snap.max_power,
            initial_temp=snap.current_temp,
            server_type=snap.server_type,
            idle_power_factor=snap.idle_power_factor,
            dissipation_coefficient=snap.dissipation_coefficient,
            thermal_inertia=snap.thermal_inertia,
        ) for snap in snapshot]
        for i, snap in enumerate(snapshot):
            self.servers[i].current_load = snap.current_load
        self._build_schedulers()

    def _update_schedulers(self) -> None:
        self._build_schedulers()

    def _resolve_scheduler(self, algorithm: str) -> BaseScheduler:
        if algorithm == 'greedy':
            return self.greedy_scheduler
        if algorithm == 'genetic':
            return self.genetic_scheduler
        if algorithm == 'round_robin':
            return self.round_robin_scheduler
        if algorithm == 'damo_ets':
            return self.damo_ets_scheduler
        if is_plugin_algorithm(algorithm):
            spec = PLUGIN_ALGORITHMS[algorithm]
            return PluginSchedulerAdapter(self.servers, str(spec["plugin"]), spec.get("config"))
        raise ValueError(f"Unknown algorithm: {algorithm}")

    def _ambient_at(self, hour: int, series: Optional[List[float]] = None) -> float:
        series = self.ambient_series if series is None else series
        if series:
            return float(series[(self.ambient_offset + hour) % len(series)])
        return float(15 + 10 * np.sin((hour - 6) * np.pi / 12))

    def _release_finished_tasks(self, hour: int, rng: np.random.Generator) -> None:
        for server in self.servers:
            for task in server.tasks[:]:
                if self.completion_mode == "service_based":
                    task.service_hour(1.0)
                    if task.is_service_complete():
                        server.remove_task(task)
                        task.completed_time = hour
                elif rng.random() < self.completion_probability:
                    server.remove_task(task)
                    task.completed_time = hour

    # -------------------------------------------------------------------- core
    def run_simulation(
        self,
        algorithm: str = 'greedy',
        task_stream: Optional[List[List[TaskSpec]]] = None,
    ) -> pd.DataFrame:
        self.logger.info(f"Starting simulation with {algorithm} algorithm")
        scheduler = self._resolve_scheduler(algorithm)

        if hasattr(scheduler, "reset"):
            scheduler.reset()

        stream = self._stream if task_stream is None else task_stream
        if stream is None:
            stream = self.build_scenario()
        scenario = self.task_generator.materialize_stream(stream)
        supports_hour = 'current_hour' in inspect.signature(scheduler.schedule_tasks).parameters

        rng = np.random.default_rng(self.seed)
        self.results = {k: [] for k in self.results}
        self._seen_tasks = []

        for hour in range(self.simulation_hours):
            ambient_temp = self._ambient_at(hour)
            if hour < len(scenario):
                new_tasks = scenario[hour]
            else:
                new_tasks = self.task_generator.generate_tasks(hour)
            self._seen_tasks.extend(new_tasks)

            if supports_hour:
                placements = scheduler.schedule_tasks(new_tasks, current_hour=hour)  # type: ignore[call-arg]
            else:
                placements = scheduler.schedule_tasks(new_tasks)

            assigned = sum(1 for _task, _server in placements if _server is not None)
            dropped = len(placements) - assigned

            hottest = max((s.current_temp for s in self.servers), default=ambient_temp)
            fan_speed = self.temp_model.adjust_fan_speed(
                self.fan_target_temp, reference_temp=hottest
            )
            cooling_throughput = fan_speed * self.cooling_power_coefficient
            self.temp_model.update_room_temperature(
                self.servers, ambient_temp, cooling_throughput
            )

            for server in self.servers:
                server.update_temperature(ambient_temp, fan_speed)

            # Measure while the work is still resident: releasing first would
            # report zero load (and base-only power) for every task that fits
            # inside one hour, which is exactly what a sub-hourly trace produces.
            total_load = sum(s.current_load for s in self.servers)
            served_load = sum(task.load for task, server in placements if server is not None)
            temperatures = [s.current_temp for s in self.servers] or [ambient_temp]
            avg_temp = float(np.mean(temperatures))
            peak_temp = float(np.max(temperatures))
            overheated = sum(1 for s in self.servers if s.is_overheated())
            cooling_power = self.energy_model.calculate_cooling_energy(
                self.servers, ambient_temp, self.cooling_target_temp, hottest
            )
            total_energy = self.energy_model.calculate_total_energy(
                self.servers, ambient_temp, self.cooling_target_temp, hottest
            )
            pue = self.energy_model.get_pue(
                self.servers, ambient_temp, self.cooling_target_temp, hottest
            )

            self._release_finished_tasks(hour, rng)

            self.results['time'].append(hour)
            self.results['total_load'].append(total_load)
            self.results['avg_temperature'].append(avg_temp)
            self.results['peak_temperature'].append(peak_temp)
            self.results['overheated_servers'].append(overheated)
            self.results['ambient_temperature'].append(ambient_temp)
            self.results['total_energy'].append(total_energy)
            self.results['pue'].append(pue)
            self.results['cooling_power'].append(cooling_power)
            self.results['fan_speed'].append(fan_speed)
            self.results['algorithm'].append(algorithm)
            self.results['deadline_violations'].append(
                count_deadline_violations(self._seen_tasks, hour)
            )
            self.results['assigned'].append(assigned)
            self.results['dropped'].append(dropped)
            self.results['offered_tasks'].append(len(new_tasks))
            self.results['offered_load'].append(sum(t.load for t in new_tasks))
            self.results['served_load'].append(served_load)
            self.results['co2_kg'].append(
                total_energy * self.emission_factor / 1000.0 if self.emission_factor else None
            )

        self.logger.info(f"Simulation completed with {algorithm} algorithm")
        frame = pd.DataFrame(self.results)
        if not self.emission_factor and 'co2_kg' in frame.columns:
            frame = frame.drop(columns=['co2_kg'])
        return frame

    def run_comparison(self, algorithms: Optional[List[str]] = None) -> pd.DataFrame:
        if algorithms is None:
            algorithms = list(DEFAULT_ALGORITHMS)

        self.results = {k: [] for k in self.results}
        if self._stream is None:
            self.build_scenario()

        baseline_snapshot = self.create_snapshot()

        dfs = []
        skipped: List[str] = []
        for algo in algorithms:
            self.restore_from_snapshot(baseline_snapshot)
            try:
                df = self.run_simulation(algo)
            except Exception as exc:
                self.logger.error(f"算法 {algo} 执行失败, 已跳过: {exc}")
                skipped.append(algo)
                self.results = {k: [] for k in self.results}
                continue
            dfs.append(df)
            self.results = {k: [] for k in self.results}

        self.skipped_algorithms = skipped
        if not dfs:
            raise RuntimeError("所有算法均执行失败, 请检查插件依赖与配置")

        self.raw_results = pd.concat(dfs, ignore_index=True)
        self.statistics = summarize(self.raw_results)
        self.ranking = rank_algorithms(self.statistics)
        return self.raw_results

    def reset_servers(self) -> None:
        self.servers = [Server(i) for i in range(self.num_servers)]
        self._update_schedulers()

    def get_statistics(self, df: Optional[pd.DataFrame] = None) -> Dict[str, Dict[str, Any]]:
        if df is None:
            if self.statistics:
                return self.statistics
            if self.raw_results is not None:
                df = self.raw_results
            else:
                raise ValueError("尚无仿真结果, 请先运行 run_comparison 或 run_simulation")
        return summarize(df)

    def get_ranking(self, df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
        stats = self.get_statistics(df)
        self.ranking = rank_algorithms(stats)
        return self.ranking

    def _first_hour_column(self, df: pd.DataFrame, column: str) -> Dict[str, Optional[float]]:
        values: Dict[str, Optional[float]] = {}
        if df is None or df.empty or column not in df.columns:
            return values
        for algo in df["algorithm"].unique():
            algo_df = df[df["algorithm"] == algo].sort_values("time")
            values[algo] = safe_number(algo_df.iloc[0][column])
        return values

    def first_hour_loads(self, df: pd.DataFrame) -> Dict[str, Optional[float]]:
        """Served load at hour 0; differs only by each scheduler's own choices."""
        return self._first_hour_column(df, "total_load")

    def first_hour_offered(self, df: pd.DataFrame) -> Dict[str, Optional[float]]:
        """Offered demand at hour 0. Equal values prove a controlled run."""
        offered = self._first_hour_column(df, "offered_load")
        if offered:
            return offered
        return self.first_hour_loads(df)

    def control_signature(self, df: pd.DataFrame) -> Dict[str, Optional[float]]:
        return self.first_hour_offered(df)

    def is_controlled(self, df: pd.DataFrame) -> bool:
        values = list(self.control_signature(df).values())
        return len(values) > 0 and len(set(values)) == 1

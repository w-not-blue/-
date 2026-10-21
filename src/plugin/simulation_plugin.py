"""
仿真插件 v2.0 — 数据中心智能调度仿真

继承自 PluginBase v2.0 标准接口，实现:
    - PluginManifest 完整清单
    - validate_config 配置验证
    - pre_execute 参数预处理
    - execute 返回 PluginResult
    - post_execute 指标收集

支持模式:
    - standard: 标准单机仿真
    - distributed: 分布式并行仿真
    - large_scale: 大规模压力测试
"""

from typing import Dict, Any, Optional, List
import os
import pandas as pd

from src.plugin.base import PluginBase
from src.plugin.protocol import PluginResult, PluginManifest
from src.algorithms import PLUGIN_ALGORITHMS
from src.simulation import DataCenterSimulator
from src.simulation.distributed_simulator import DistributedSimulator
from src.visualization import (
    plot_energy_comparison, plot_temperature_trend,
    plot_load_distribution, plot_pue_comparison, plot_cooling_power,
    plot_service_rate
)
from src.simulation.metrics import summarize
from src.utils import save_simulation_data, export_results_to_excel, save_run_metadata
from src.utils.logger import setup_logger


class SimulationPlugin(PluginBase):

    def get_manifest(self) -> PluginManifest:
        return PluginManifest(
            name="SimulationPlugin",
            version="2.0.0",
            supported_operations=["run_comparison"],
            input_schema={
                "num_servers": {"type": "int", "min": 1, "max": 1000, "default": 10},
                "simulation_hours": {"type": "int", "min": 1, "max": 720, "default": 24},
                "algorithms": {"type": "list", "items": "str", "default": ["greedy","genetic","round_robin"]},
                "parallel_workers": {"type": "int", "min": 1, "max": 32, "default": 4},
                "run_mode": {"type": "str", "enum": ["standard","distributed","large_scale"], "default": "standard"},
                "server_type": {"type": "str", "enum": ["mixed","standard","high_performance","gpu_server"], "default": "mixed"},
                "visualization": {"type": "bool", "default": True},
                "save_results": {"type": "bool", "default": True}
            },
            output_schema={
                "success": "bool",
                "data": {"statistics": "dict", "records_count": "int", "algorithms": "list"},
                "message": "str",
                "execution_time_ms": "float"
            },
            min_system_version="2.0.0",
            category="executor"
        )

    def validate_config(self, config: Dict[str, Any]) -> bool:
        num_servers = config.get("num_servers", 10)
        if not isinstance(num_servers, int) or num_servers < 1 or num_servers > 1000:
            self.logger.error(f"num_servers 必须在 [1, 1000] 范围内，当前值: {num_servers}")
            return False
        sim_hours = config.get("simulation_hours", 24)
        if not isinstance(sim_hours, int) or sim_hours < 1 or sim_hours > 720:
            self.logger.error(f"simulation_hours 必须在 [1, 720] 范围内，当前值: {sim_hours}")
            return False
        workers = config.get("parallel_workers", 4)
        if workers < 1 or workers > 32:
            self.logger.error(f"parallel_workers 必须在 [1, 32] 范围内，当前值: {workers}")
            return False
        return True

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        try:
            cfg = config or {}
            self.num_servers = cfg.get("num_servers", 10)
            self.simulation_hours = cfg.get("simulation_hours", 24)
            self.parallel_workers = cfg.get("parallel_workers", 4)
            self.run_mode = cfg.get("run_mode", "standard")
            self.server_type = cfg.get("server_type", "mixed")
            self.site_id = cfg.get("site_id") or None
            self.results_dir = cfg.get("results_dir", "data")
            self.reports_dir = cfg.get("reports_dir", "reports")
            self.simulator = None
            self.distributed_simulator = None

            os.makedirs(self.results_dir, exist_ok=True)
            os.makedirs(self.reports_dir, exist_ok=True)

            if self.run_mode in ("distributed", "large_scale"):
                self.distributed_simulator = DistributedSimulator(
                    num_servers=self.num_servers,
                    parallel_workers=self.parallel_workers
                )
            else:
                self.simulator = DataCenterSimulator(
                    num_servers=self.num_servers,
                    simulation_hours=self.simulation_hours,
                    data_source_override=(
                        {"site_id": self.site_id} if self.site_id else None
                    ),
                )
            return True
        except Exception as e:
            self.logger.error(f"SimulationPlugin 初始化失败: {e}")
            return False

    def pre_execute(self, **kwargs) -> Optional[PluginResult]:
        algorithms = kwargs.get("algorithms", ["greedy", "genetic", "round_robin"])
        valid_algos = {"greedy", "genetic", "round_robin", "damo_ets"}.union(PLUGIN_ALGORITHMS.keys())
        for algo in algorithms:
            if algo not in valid_algos:
                return PluginResult.fail(
                    message=f"不支持的算法: '{algo}'，有效值: {sorted(valid_algos)}",
                    error_code="INVALID_ALGORITHM"
                )
        return None

    def execute(self, **kwargs) -> PluginResult:
        algorithms = kwargs.get("algorithms", ["greedy", "genetic", "round_robin"])
        run_visualization = kwargs.get("visualization", True)
        save_results = kwargs.get("save_results", True)
        include_series = kwargs.get("include_series", False)

        try:
            if self.run_mode in ("distributed", "large_scale"):
                df = self._run_distributed_comparison(algorithms)
            else:
                df = self.simulator.run_comparison(algorithms=algorithms)

            stats = self._calculate_statistics(df)
            warnings: List[str] = []
            skipped = getattr(self.simulator, "skipped_algorithms", []) if self.simulator else []
            if skipped:
                warnings.append("以下算法执行失败已跳过: " + ", ".join(skipped))

            if save_results:
                csv_path = save_simulation_data(df)
                excel_path = export_results_to_excel(df)

            if run_visualization:
                plots = self._generate_plots(df)
        except Exception as e:
            return PluginResult.fail(
                message=f"仿真执行异常: {e}",
                error_code="SIMULATION_ERROR",
                error_detail=str(e)
            )

        payload = {
            "statistics": stats,
            "records_count": len(df),
            "algorithms": algorithms,
            "run_mode": self.run_mode,
            "server_count": self.num_servers,
            "simulation_hours": self.simulation_hours
        }
        if include_series:
            payload["series"] = df.to_dict("records")
        if save_results:
            payload["csv_path"] = csv_path
            payload["excel_path"] = excel_path
        if run_visualization:
            payload["plots"] = plots

        if self.simulator is not None:
            payload["first_hour_offered"] = self.simulator.control_signature(df)
            payload["first_hour_served"] = self.simulator.first_hour_loads(df)
            payload["controlled"] = self.simulator.is_controlled(df)
            payload["ranking"] = self.simulator.get_ranking(df)
            payload["data_source"] = self.simulator.run_metadata
            if save_results:
                save_run_metadata({
                    **self.simulator.run_metadata,
                    "algorithms": algorithms,
                    "num_servers": self.num_servers,
                    "simulation_hours": self.simulation_hours,
                    "controlled": payload["controlled"],
                })

        return PluginResult.ok(
            data=payload,
            message=f"仿真完成 ({self.run_mode} 模式)",
            warnings=warnings,
            metrics={
                "server_count": self.num_servers,
                "simulation_hours": self.simulation_hours,
                "algorithm_count": len(algorithms),
                "data_points": len(df)
            }
        )

    def post_execute(self, result: PluginResult, **kwargs) -> PluginResult:
        if result.success:
            self.logger.info(
                f"仿真完成: {result.data.get('records_count', 0)} 条记录, "
                f"{len(result.data.get('algorithms', []))} 个算法"
            )
        return result

    def cleanup(self) -> None:
        self.simulator = None
        self.distributed_simulator = None

    def _run_distributed_comparison(self, algorithms: List[str]) -> pd.DataFrame:
        if not self.distributed_simulator:
            raise RuntimeError("分布式仿真器未初始化")
        results = self.distributed_simulator.run_parallel_comparison(
            algorithms=algorithms, hours=self.simulation_hours
        )
        all_data = []
        for algo, algo_results in results.items():
            for hr in algo_results:
                all_data.append({
                    "time": hr["hour"], "algorithm": hr["algorithm"],
                    "total_load": hr["total_load"], "avg_temperature": hr["avg_temperature"],
                    "total_energy": hr["total_energy"], "pue": hr["pue"]
                })
        return pd.DataFrame(all_data)

    def _calculate_statistics(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Same metric definitions as the standalone simulator, one code path."""
        return summarize(df)

    def _generate_plots(self, df) -> Dict[str, str]:
        """Best-effort plotting: a missing column skips one chart, not the run."""
        os.makedirs(self.reports_dir, exist_ok=True)
        targets = [
            ("energy", "energy_comparison.png", "total_energy", plot_energy_comparison),
            ("temperature", "temperature_trend.png", "avg_temperature", plot_temperature_trend),
            ("load", "load_distribution.png", "total_load", plot_load_distribution),
            ("pue", "pue_comparison.png", "pue", plot_pue_comparison),
            ("cooling", "cooling_power.png", "cooling_power", plot_cooling_power),
            ("service", "service_rate.png", "assigned", plot_service_rate),
        ]

        plots: Dict[str, str] = {}
        for key, filename, column, painter in targets:
            if column not in df.columns:
                self.logger.warning(f"结果缺少 {column} 列, 已跳过 {filename}")
                continue
            path = os.path.join(self.reports_dir, filename)
            try:
                painter(df, path)
                plots[key] = path
            except Exception as exc:
                self.logger.warning(f"生成 {filename} 失败: {exc}")
        return plots

    def run_single_simulation(self, algorithm: str = "greedy") -> Dict[str, Any]:
        if not self.simulator and not self.distributed_simulator:
            return {"success": False, "error": "插件未初始化"}
        try:
            if self.distributed_simulator:
                results = self.distributed_simulator.run_distributed_simulation(
                    hours=self.simulation_hours, algorithm=algorithm
                )
                df = pd.DataFrame([{
                    "time": r["hour"], "algorithm": r["algorithm"],
                    "total_load": r["total_load"], "avg_temperature": r["avg_temperature"],
                    "total_energy": r["total_energy"], "pue": r["pue"]
                } for r in results])
            else:
                df = self.simulator.run_simulation(algorithm=algorithm)
            return {"success": True, "statistics": self._calculate_statistics(df), "records_count": len(df)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def run_large_scale_test(self, server_counts: List[int]) -> Dict[str, Any]:
        if not self.distributed_simulator:
            return {"success": False, "error": "分布式仿真器未初始化"}
        try:
            results = self.distributed_simulator.run_large_scale_test(
                server_counts=server_counts, hours=self.simulation_hours
            )
            return {"success": True, "results": results, "test_hours": self.simulation_hours}
        except Exception as e:
            return {"success": False, "error": str(e)}

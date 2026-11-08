"""
RLSchedulerPlugin — Stable Baselines3 PPO 强化学习调度器 (GitHub Skills 集成)

继承 ``PluginBase`` v1.0，实现:

    * ``train``     — 用 PPO 在 DataCenterGymEnv 上训练，保存模型到 ``model_path``
    * ``predict``   — 加载已训练模型，对一批 ``tasks`` 选出 server_id
    * ``schedule``  — predict 的别名，方便直接 ``algorithms=["rl_ppo"]`` 调用
    * ``save``/``load`` — 显式的 SB3 模型持久化入口

依赖:
    pip install gymnasium stable-baselines3

依赖缺失时会返回 ``PluginResult.fail(error_code="DEPENDENCY_MISSING")`` 而不崩溃。

来源: https://github.com/DLR-RM/stable-baselines3 (MIT)
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from src.plugin.base import PluginBase
from src.plugin.protocol import PluginManifest, PluginResult
from src.plugins._deps import has_gymnasium, has_sb3


class RLSchedulerPlugin(PluginBase):
    """Stable Baselines3 PPO 调度器插件"""

    # -------------------------------------------------------------------------
    # 清单 / 配置
    # -------------------------------------------------------------------------

    def get_manifest(self) -> PluginManifest:
        return PluginManifest(
            name="RLSchedulerPlugin",
            version="1.0.0",
            supported_operations=["train", "predict", "schedule", "save", "load"],
            input_schema={
                "algorithms": {"type": "list", "default": ["rl_ppo"]},
                "train": {"type": "bool", "default": False},
                "total_timesteps": {"type": "int", "default": 10000, "min": 1},
                "model_path": {"type": "str", "default": "data/policies/ppo_datacenter.zip"},
                "learning_rate": {"type": "float", "default": 3e-4},
                "n_steps": {"type": "int", "default": 128},
                "batch_size": {"type": "int", "default": 32},
                "num_servers": {"type": "int", "default": 10, "min": 1},
                "simulation_hours": {"type": "int", "default": 24, "min": 1},
                "seed": {"type": "int", "default": 42},
            },
            output_schema={
                "model_path": "str",
                "assignments": "list",
                "training_time_s": "float",
            },
            min_system_version="1.0.0",
            category="algorithm",
        )

    def validate_config(self, config: Dict[str, Any]) -> bool:
        num_servers = config.get("num_servers", 10)
        if not isinstance(num_servers, int) or num_servers < 1:
            self.logger.error(f"num_servers 必须 >= 1, 当前={num_servers}")
            return False
        sim_hours = config.get("simulation_hours", 24)
        if not isinstance(sim_hours, int) or sim_hours < 1:
            self.logger.error(f"simulation_hours 必须 >= 1, 当前={sim_hours}")
            return False
        return True

    # -------------------------------------------------------------------------
    # 生命周期
    # -------------------------------------------------------------------------

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        cfg = dict(config or {})
        if not self.validate_config(cfg):
            return False

        # 依赖检查（若缺失则初始化失败 — 符合测试契约）
        if not (has_gymnasium() and has_sb3()):
            self.logger.warning(
                "stable-baselines3 / gymnasium 未安装，RLSchedulerPlugin 初始化失败。"
                "请执行: pip install stable-baselines3 gymnasium"
            )
            self._deps_ok = False
            return False

        self._deps_ok = True
        self._num_servers: int = int(cfg.get("num_servers", 10))
        self._simulation_hours: int = int(cfg.get("simulation_hours", 24))
        self._seed: int = int(cfg.get("seed", 42))
        self._learning_rate: float = float(cfg.get("learning_rate", 3e-4))
        self._n_steps: int = int(cfg.get("n_steps", 128))
        self._batch_size: int = int(cfg.get("batch_size", 32))
        self._default_model_path: str = str(
            cfg.get("model_path", "data/policies/ppo_datacenter.zip")
        )

        self._env = None
        self._model = None
        return True

    def cleanup(self) -> None:
        self._env = None
        self._model = None

    # -------------------------------------------------------------------------
    # 执行
    # -------------------------------------------------------------------------

    def execute(self, **kwargs: Any) -> PluginResult:
        # 依赖缺失 → 直接降级返回
        if not (has_gymnasium() and has_sb3()):
            return PluginResult.fail(
                message=(
                    "stable-baselines3 / gymnasium 未安装。请先执行: "
                    "pip install stable-baselines3 gymnasium"
                ),
                error_code="DEPENDENCY_MISSING",
            )

        algorithms: List[str] = list(kwargs.get("algorithms") or ["rl_ppo"])
        is_train: bool = bool(kwargs.get("train", False))
        total_timesteps: int = int(kwargs.get("total_timesteps", 10000))
        model_path: Optional[str] = kwargs.get("model_path") or self._default_model_path
        learning_rate: float = float(kwargs.get("learning_rate", self._learning_rate))
        tasks: Optional[List[Any]] = kwargs.get("tasks")
        servers: Optional[List[Any]] = kwargs.get("servers")

        if is_train:
            return self._train_model(
                total_timesteps=total_timesteps,
                model_path=model_path,
                learning_rate=learning_rate,
            )
        return self._predict_and_schedule(
            tasks=tasks, servers=servers, model_path=model_path
        )

    # -------------------------------------------------------------------------
    # 内部：训练 / 推理
    # -------------------------------------------------------------------------

    def _train_model(
        self, total_timesteps: int, model_path: str, learning_rate: float
    ) -> PluginResult:
        try:
            from stable_baselines3 import PPO
            from src.plugins.datacenter_gym_env import DataCenterGymEnv

            self._env = DataCenterGymEnv(
                num_servers=self._num_servers,
                simulation_hours=self._simulation_hours,
                seed=self._seed,
            )
            self._model = PPO(
                "MlpPolicy",
                self._env,
                learning_rate=learning_rate,
                n_steps=self._n_steps,
                batch_size=self._batch_size,
                verbose=0,
                seed=self._seed,
            )
            start = time.perf_counter()
            self._model.learn(total_timesteps=total_timesteps)
            elapsed = round(time.perf_counter() - start, 3)

            os.makedirs(os.path.dirname(model_path) or ".", exist_ok=True)
            self._model.save(model_path)

            return PluginResult.ok(
                data={
                    "model_path": model_path,
                    "training_time_s": elapsed,
                    "total_timesteps": total_timesteps,
                },
                message=f"PPO 训练完成: {total_timesteps} steps, {elapsed}s",
            )
        except Exception as e:
            self.logger.error(f"训练失败: {e}", exc_info=True)
            return PluginResult.fail(
                message=f"训练失败: {e}",
                error_code="TRAIN_ERROR",
                error_detail=str(e),
            )

    def _predict_and_schedule(
        self,
        tasks: Optional[List[Any]],
        servers: Optional[List[Any]],
        model_path: Optional[str],
    ) -> PluginResult:
        if not tasks or servers is None:
            return PluginResult.fail(
                message="推理模式需要 tasks 与 servers 参数",
                error_code="MISSING_PARAM",
            )
        if not model_path or not os.path.exists(model_path):
            return PluginResult.fail(
                message=f"模型文件不存在: {model_path}",
                error_code="MODEL_NOT_FOUND",
            )
        try:
            from stable_baselines3 import PPO
            from src.plugins.datacenter_gym_env import DataCenterGymEnv

            model = PPO.load(model_path)
            env = DataCenterGymEnv(
                num_servers=self._num_servers,
                simulation_hours=self._simulation_hours,
                seed=self._seed,
            )
            assignments: List[Dict[str, Any]] = []
            obs, _info = env.reset()
            for task in tasks:
                # 取 env 内部 _current_task = task，然后用 model.predict
                env._current_task = task  # type: ignore[attr-defined]
                obs = env._get_obs()  # type: ignore[attr-defined]
                action, _state = model.predict(obs, deterministic=True)
                server_id = int(action)
                if 0 <= server_id < len(servers):
                    server = servers[server_id]
                    if server.assign_task(task):
                        assignments.append(
                            {
                                "task_id": task.task_id,
                                "server_id": server_id,
                                "load": task.load,
                            }
                        )
                        continue
                # 默认分配：负载最低的服务器
                fallback = min(
                    range(len(servers)),
                    key=lambda i: servers[i].current_load / max(servers[i].max_load, 1.0),
                )
                if servers[fallback].assign_task(task):
                    assignments.append(
                        {
                            "task_id": task.task_id,
                            "server_id": fallback,
                            "load": task.load,
                            "fallback": True,
                        }
                    )
                else:
                    assignments.append(
                        {"task_id": task.task_id, "server_id": None, "rejected": True}
                    )
            return PluginResult.ok(
                data={
                    "assignments": assignments,
                    "model_path": model_path,
                    "total_tasks": len(assignments),
                },
                message=f"PPO 推理完成: {len(assignments)} 个任务",
            )
        except Exception as e:
            self.logger.error(f"推理失败: {e}", exc_info=True)
            return PluginResult.fail(
                message=f"推理失败: {e}",
                error_code="PREDICT_ERROR",
                error_detail=str(e),
            )


__all__ = ["RLSchedulerPlugin"]

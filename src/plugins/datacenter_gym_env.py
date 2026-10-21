"""
Gymnasium Env 适配器 — DataCenterSimulator → gymnasium.Env

把 ``DataCenterSimulator`` 包装为符合 ``gymnasium.Env`` 接口规范的环境，
供 Stable Baselines3 PPO 训练使用。

State (observation) 编码 (归一化到 [0, 1]):
    - 服务器特征 (num_servers * 3): [load_ratio, temp_ratio, type_norm] × N
    - 当前任务特征 (4):           [load_norm, priority_norm, deadline_norm, wc_oh(1)]

Action space:
    - Discrete(num_servers)  — 选择把当前任务分配到哪台服务器

Reward:
    reward = -Δenergy - 0.5*Δtemp - 10*deadline_violation + 0.1*balance

依赖:
    pip install gymnasium

来源: https://github.com/Farama-Foundation/Gymnasium (MIT)
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from src.plugins._deps import has_gymnasium

if has_gymnasium():
    import gymnasium as gym
    from gymnasium import spaces
    import numpy as np
else:
    gym = None  # type: ignore
    spaces = None  # type: ignore
    np = None  # type: ignore

from src.simulation import DataCenterSimulator
from src.models import Task


class DataCenterGymEnv:
    """
    DataCenter Gymnasium Environment

    与 ``gymnasium.Env`` 兼容（非继承，因为无 gym 时需要软降级）。

    用法::

        env = DataCenterGymEnv(num_servers=10, simulation_hours=24, seed=42)
        obs, info = env.reset()
        for _ in range(1000):
            action = env.action_space.sample()
            obs, reward, terminated, truncated, info = env.step(action)
            if terminated or truncated:
                break

    注意:
        - 如果 gymnasium 未安装，构造时直接抛 ``ImportError``，调用方需提前探测
        - 不继承 ``gym.Env`` 是为了保持模块在无依赖时也能 ``import``
    """

    metadata = {"render_modes": []}

    # 服务器类型 one-hot 索引映射（与 Server.server_type 字符串对应）
    SERVER_TYPES = ["standard", "high_performance", "gpu_server"]
    WORKLOAD_CLASSES = ["cpu_bound", "memory_bound", "io_bound"]

    def __init__(
        self,
        num_servers: int = 10,
        simulation_hours: int = 24,
        seed: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not has_gymnasium():
            raise ImportError(
                "gymnasium 未安装。请运行: pip install gymnasium>=1.0"
            )
        if num_servers < 1:
            raise ValueError(f"num_servers 必须 >= 1, 当前={num_servers}")

        self.num_servers: int = num_servers
        self.simulation_hours: int = simulation_hours
        self._seed_value: Optional[int] = seed
        self._config: Optional[Dict[str, Any]] = config
        self._current_time: int = 0
        self._current_task: Optional[Task] = None
        self._step_count: int = 0
        self._max_steps: int = simulation_hours * num_servers  # heuristic horizon

        # observation = servers (num_servers * 3) + task (4)
        self.observation_dim: int = num_servers * 3 + 4
        self.observation_space = spaces.Box(
            low=0.0,
            high=1.0,
            shape=(self.observation_dim,),
            dtype=np.float32,
        )
        self.action_space = spaces.Discrete(num_servers)

        # simulator / servers 状态在 reset() 时初始化
        self.simulator: Optional[DataCenterSimulator] = None
        self._prev_total_power: float = 0.0
        self._prev_max_temp: float = 25.0

    # -------------------------------------------------------------------------
    # Gymnasium API
    # -------------------------------------------------------------------------

    def reset(
        self,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple["np.ndarray", Dict[str, Any]]:
        if seed is not None:
            self._seed_value = seed
        self.simulator = DataCenterSimulator(
            num_servers=self.num_servers,
            simulation_hours=self.simulation_hours,
            seed=self._seed_value,
            config=self._config,
        )
        self._current_time = 0
        self._step_count = 0
        self._current_task = self._generate_task(self._current_time)

        self._prev_total_power = sum(s.calculate_power() for s in self.simulator.servers)
        self._prev_max_temp = max(s.current_temp for s in self.simulator.servers)
        return self._get_obs(), self._get_info()

    def step(self, action: int) -> Tuple["np.ndarray", float, bool, bool, Dict[str, Any]]:
        assert self.simulator is not None, "请先调用 reset()"
        if not self.action_space.contains(int(action)):
            raise ValueError(f"action 必须在 [0, {self.num_servers})")

        server = self.simulator.servers[int(action)]
        task = self._current_task

        # 分配任务（如果超载，则视为失败放置 — 仍记入状态）
        assigned = False
        deadline_violation = 0
        if task is not None:
            deadline_violation = 1 if task.is_deadline_violated(self._current_time) else 0
            assigned = server.assign_task(task)

        # 更新温度
        for s in self.simulator.servers:
            s.update_temperature(ambient_temp=25.0, cooling_efficiency=0.5)

        # 统计差量
        total_power = sum(s.calculate_power() for s in self.simulator.servers)
        max_temp = max(s.current_temp for s in self.simulator.servers)
        delta_energy = (total_power - self._prev_total_power) / 1000.0  # 归一化
        delta_temp = (max_temp - self._prev_max_temp) / 100.0

        # 负载均衡奖励（标准差越小越好）
        loads = [s.current_load / max(s.max_load, 1.0) for s in self.simulator.servers]
        load_std = float(np.std(loads)) if len(loads) > 0 else 0.0
        balance_bonus = 0.1 * (1.0 - min(load_std, 1.0))

        reward = -max(delta_energy, 0.0) - 0.5 * max(delta_temp, 0.0) - 10.0 * deadline_violation + balance_bonus
        # 关键决策：未分配成功的任务要重罚
        if not assigned:
            reward -= 5.0

        self._prev_total_power = total_power
        self._prev_max_temp = max_temp
        self._step_count += 1
        self._current_time += 1

        # 推进到下一任务 / 终止
        terminated = False
        truncated = False
        if self._step_count >= self._max_steps or self._current_time >= self.simulation_hours:
            truncated = True
        else:
            self._current_task = self._generate_task(self._current_time)
            if self._current_task is None:
                terminated = True

        return self._get_obs(), float(reward), terminated, truncated, self._get_info()

    def render(self) -> None:  # pragma: no cover - 不支持渲染
        return None

    def close(self) -> None:
        self.simulator = None

    # -------------------------------------------------------------------------
    # 内部辅助
    # -------------------------------------------------------------------------

    def _generate_task(self, current_time: int) -> Optional[Task]:
        """生成一个当前时刻的任务。简单随机生成（与仿真器 TaskGenerator 行为近似）。"""
        if self.simulator is None or self.simulator.task_generator is None:
            return None
        try:
            tasks = self.simulator.task_generator.generate_tasks(current_time)
        except Exception:
            return None
        if not tasks:
            return None
        return tasks[0]

    def _get_obs(self) -> "np.ndarray":
        if self.simulator is None:
            return np.zeros(self.observation_dim, dtype=np.float32)
        servers = self.simulator.servers
        srv_feats: List[float] = []
        for s in servers:
            srv_feats.append(min(s.current_load / max(s.max_load, 1.0), 1.0))
            srv_feats.append(min(s.current_temp / max(s.max_temp, 1.0), 1.0))
            srv_feats.append(self._type_norm(s.server_type))
        task_feats: List[float] = [0.0, 0.0, 0.0, 0.0]
        if self._current_task is not None:
            t = self._current_task
            task_feats[0] = min(t.load / 50.0, 1.0)
            task_feats[1] = min(t.priority / 3.0, 1.0) if t.priority else 0.0
            task_feats[2] = (
                1.0 if t.deadline is None else min(t.deadline / 100.0, 1.0)
            )
            task_feats[3] = 1.0 if t.workload_class == "cpu_bound" else 0.0
        obs = np.array(srv_feats + task_feats, dtype=np.float32)
        return obs

    def _get_info(self) -> Dict[str, Any]:
        if self.simulator is None:
            return {}
        servers = self.simulator.servers
        return {
            "current_time": self._current_time,
            "step_count": self._step_count,
            "total_load": float(sum(s.current_load for s in servers)),
            "max_temp": float(max(s.current_temp for s in servers)),
            "total_power": float(sum(s.calculate_power() for s in servers)),
            "current_task": (
                None if self._current_task is None else self._current_task.task_id
            ),
        }

    def _type_norm(self, server_type: str) -> float:
        if server_type in self.SERVER_TYPES:
            return float(self.SERVER_TYPES.index(server_type)) / max(len(self.SERVER_TYPES) - 1, 1)
        return 0.0

    @property
    def unwrapped(self):
        return self

    @property
    def np_random(self) -> "np.random.Generator":
        return np.random.default_rng(self._seed_value)


__all__ = ["DataCenterGymEnv"]

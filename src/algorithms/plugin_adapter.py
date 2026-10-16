"""插件算法适配器

把「插件形态的第三方调度算法」适配成核心调度器接口，使其能够进入
DataCenterSimulator 的常规仿真与横向对比回路，而不需要修改插件本身。
"""
from typing import Dict, List, Optional, Tuple

from src.algorithms.base import BaseScheduler
from src.models import Server, Task
from src.utils.logger import setup_logger

logger = setup_logger("plugin_adapter")

PLUGIN_ALGORITHMS: Dict[str, Dict[str, object]] = {
    "nsga2": {
        "plugin": "NSGA2SchedulerPlugin",
        "config": {"pop_size": 12, "n_gen": 10, "selection": "balanced", "seed": 42},
    },
    "moead": {
        "plugin": "MOEADSchedulerPlugin",
        "config": {"n_partitions": 4, "n_neighbors": 5, "n_gen": 10, "selection": "balanced", "seed": 42},
    },
}


def is_plugin_algorithm(name: str) -> bool:
    return name in PLUGIN_ALGORITHMS


class PluginSchedulerAdapter(BaseScheduler):
    """把插件的 assignments 结果翻译成核心调度器的 (task, server) 语义。"""

    def __init__(self, servers: List[Server], plugin_name: str,
                 config: Optional[Dict[str, object]] = None) -> None:
        super().__init__(servers)
        self.plugin_name = plugin_name
        self.config = dict(config or {})
        self._manager = None

    def get_name(self) -> str:
        return self.plugin_name

    def _ensure_plugin(self):
        if self._manager is not None:
            return self._manager
        from src.plugin import PluginManager

        manager = PluginManager()
        manager.discover_plugins(plugin_dir="src/plugins")
        if self.plugin_name not in manager._plugin_classes:
            raise RuntimeError("插件未找到: %s" % self.plugin_name)
        if not manager.load_plugin(self.plugin_name, self.config):
            raise RuntimeError("插件加载失败: %s" % self.plugin_name)
        self._manager = manager
        return manager

    def _fallback(self, tasks: List[Task]) -> List[Tuple[Task, Optional[Server]]]:
        from src.algorithms.greedy_algorithm import GreedyScheduler

        try:
            return GreedyScheduler(self.servers).schedule_tasks(tasks)
        except Exception:
            return [(task, None) for task in tasks]

    def schedule_tasks(self, tasks: List[Task]) -> List[Tuple[Task, Optional[Server]]]:
        if not tasks:
            return []

        try:
            manager = self._ensure_plugin()
            result = manager.execute_plugin(self.plugin_name, tasks=tasks, servers=self.servers)
            if not result.success:
                raise RuntimeError("插件调度失败: %s [%s]" % (result.message, result.error_code))

            assignments = (result.data or {}).get("assignments", [])
            by_id = {task.task_id: task for task in tasks}
            placements: List[Tuple[Task, Optional[Server]]] = []
            placed = set()

            for item in assignments:
                task = by_id.get(str(item.get("task_id")))
                if task is None or task.task_id in placed:
                    continue
                index = int(item.get("server_id", -1))
                server = self.servers[index] if 0 <= index < len(self.servers) else None
                if server is not None and not server.is_overloaded() and not server.is_overheated():
                    if server.assign_task(task):
                        placements.append((task, server))
                        placed.add(task.task_id)
                        continue

                placements.append((task, None))
                placed.add(task.task_id)

            pending = [task for task, server in placements if server is None]
            if pending:
                placements = [(task, server) for task, server in placements if server is not None]
                for task in pending:
                    candidates = [
                        server for server in self.servers
                        if not server.is_overloaded() and not server.is_overheated()
                    ]
                    target = min(candidates, key=lambda server: server.current_load, default=None)
                    if target is not None and target.assign_task(task):
                        placements.append((task, target))
                    else:
                        placements.append((task, None))

            return placements
        except Exception as exc:
            logger.warning("插件 %s 调度失败，回退贪心: %s", self.plugin_name, exc)
            return self._fallback(tasks)

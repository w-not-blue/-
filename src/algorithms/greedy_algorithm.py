import numpy as np
from typing import List, Tuple, Optional
from src.models import Task, Server
from src.algorithms.base import BaseScheduler

class GreedyScheduler(BaseScheduler):
    def __init__(self, servers: List[Server], temperature_weight: float = 0.3) -> None:
        super().__init__(servers)
        self.temp_weight: float = temperature_weight
        self.load_weight: float = 1 - temperature_weight

    def select_best_server(self, task: Task) -> Server:
        scores: List[float] = []
        for server in self.servers:
            if server.is_overloaded() or server.is_overheated():
                scores.append(float('-inf'))
                continue

            load_score = 1 - server.current_load / server.max_load
            temp_score = 1 - server.current_temp / server.max_temp
            combined_score = self.load_weight * load_score + self.temp_weight * temp_score
            scores.append(combined_score)

        best_idx = np.argmax(scores)
        return self.servers[best_idx]

    def schedule_task(self, task: Task) -> Tuple[Task, Optional[Server]]:
        server = self.select_best_server(task)
        if server and not server.is_overloaded() and not server.is_overheated():
            if server.assign_task(task):
                return (task, server)
        return (task, None)

    def schedule_tasks(self, tasks: List[Task]) -> List[Tuple[Task, Optional[Server]]]:
        sorted_tasks = sorted(tasks, key=lambda t: (t.priority, t.load), reverse=True)
        return [self.schedule_task(task) for task in sorted_tasks]

    def batch_schedule(self, tasks: List[Task]) -> List[Tuple[Task, Optional[Server]]]:
        return self.schedule_tasks(tasks)

    def get_name(self) -> str:
        return "greedy"
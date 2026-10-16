from typing import List, Tuple, Optional
from src.models import Task, Server
from src.algorithms.base import BaseScheduler

class RoundRobinScheduler(BaseScheduler):
    def __init__(self, servers: List[Server]) -> None:
        super().__init__(servers)
        self._next_idx: int = 0

    def schedule_tasks(self, tasks: List[Task]) -> List[Tuple[Task, Optional[Server]]]:
        results = []
        for task in tasks:
            attempts = 0
            while attempts < len(self.servers):
                server = self.servers[self._next_idx % len(self.servers)]
                self._next_idx += 1
                if not server.is_overloaded() and not server.is_overheated():
                    if server.assign_task(task):
                        results.append((task, server))
                        break
                attempts += 1
            else:
                results.append((task, None))
        return results

    def get_name(self) -> str:
        return "round_robin"
from abc import ABC, abstractmethod
from typing import List, Tuple, Optional
from src.models import Task, Server

class BaseScheduler(ABC):
    def __init__(self, servers: List[Server]) -> None:
        self.servers = servers

    @abstractmethod
    def schedule_tasks(self, tasks: List[Task]) -> List[Tuple[Task, Optional[Server]]]:
        pass

    @abstractmethod
    def get_name(self) -> str:
        pass
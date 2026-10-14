import uuid
from typing import Optional

class Task:
    def __init__(
        self,
        load: int = 10,
        priority: int = 1,
        deadline: Optional[int] = None,
        arrival_time: int = 0,
        workload_class: str = "cpu_bound",
        duration: int = 0,
    ) -> None:
        self.task_id: str = str(uuid.uuid4())[:8]
        self.load: int = load
        self.priority: int = priority
        self.deadline: Optional[int] = deadline
        self.arrival_time: int = arrival_time
        self.workload_class: str = workload_class
        self.duration: int = int(duration)
        self.remaining_hours: float = float(duration)
        self.created_time: int = 0
        self.start_time: Optional[int] = None
        self.completed_time: Optional[int] = None

    def is_completed(self) -> bool:
        return self.completed_time is not None

    def get_waiting_time(self, current_time: int) -> int:
        if self.start_time is None:
            return current_time - self.created_time
        return self.start_time - self.created_time

    def is_deadline_violated(self, current_time: int) -> bool:
        """Whether the deadline had already passed at ``current_time``.

        This is an assignment-time check. The authoritative SLA metric is
        computed after completion via ``missed_deadline``.
        """
        if self.deadline is None:
            return False
        return current_time > self.deadline

    def missed_deadline(self, last_hour: int) -> bool:
        """Whether the task failed to meet its deadline within the horizon.

        A task counts as a miss when it finished after its deadline, or when it
        never finished although the deadline has already elapsed.
        """
        if self.deadline is None:
            return False
        if self.completed_time is not None:
            return self.completed_time > self.deadline
        return self.deadline <= last_hour

    def service_hour(self, amount: float = 1.0) -> float:
        """Consume one hour of execution service and return what is left."""
        self.remaining_hours = max(0.0, self.remaining_hours - amount)
        return self.remaining_hours

    def is_service_complete(self) -> bool:
        return self.remaining_hours <= 0.0

    @property
    def slack_hours(self) -> Optional[int]:
        if self.deadline is None:
            return None
        return self.deadline - self.arrival_time

    def __repr__(self) -> str:
        return (
            f"Task({self.task_id}, load={self.load}, priority={self.priority}, "
            f"class={self.workload_class}, deadline={self.deadline}, "
            f"duration={self.duration})"
        )

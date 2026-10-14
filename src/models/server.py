from typing import List, Optional
from src.models.task import Task

HEAT_TO_POWER_RATIO = 0.8
DEFAULT_DISSIPATION = 8.0
DEFAULT_THERMAL_INERTIA = 0.25


class Server:
    """A single machine.

    Thermal behaviour is a first-order lag towards an equilibrium that depends
    on the generated heat, the outdoor temperature and how hard the fans run:

        equilibrium = ambient + heat / (dissipation * cooling_efficiency)

    With the nominal coefficients this keeps a fully loaded standard server
    around 45 C at 25 C ambient, which is a realistic band; the previous
    linear model pinned every loaded server at ``max_temp`` within hours.
    """

    def __init__(
        self,
        server_id: int,
        max_load: float = 100,
        max_temp: float = 85,
        base_power: float = 100,
        max_power: float = 300,
        initial_temp: float = 25,
        server_type: str = "standard",
        idle_power_factor: float = 0.6,
        dissipation_coefficient: float = DEFAULT_DISSIPATION,
        thermal_inertia: float = DEFAULT_THERMAL_INERTIA,
    ) -> None:
        self.server_id: int = server_id
        self.max_load: float = max_load
        self.max_temp: float = max_temp
        self.base_power: float = base_power
        self.max_power: float = max_power
        self.idle_power_factor: float = float(idle_power_factor)
        self.dissipation_coefficient: float = float(dissipation_coefficient)
        self.thermal_inertia: float = float(thermal_inertia)
        self.current_temp: float = initial_temp
        self.current_load: float = 0
        self.server_type: str = server_type
        self.last_ambient: float = initial_temp
        self.last_fan_speed: float = 0.5
        self.tasks: List[Task] = []

    # -------------------------------------------------------------- workload
    def assign_task(self, task: Task) -> bool:
        if self.current_load + task.load <= self.max_load:
            self.current_load += task.load
            self.tasks.append(task)
            return True
        return False

    def remove_task(self, task: Task) -> bool:
        if task in self.tasks:
            self.tasks.remove(task)
            self.current_load -= task.load
            return True
        return False

    def is_overloaded(self) -> bool:
        return self.current_load >= self.max_load * 0.95

    def is_overheated(self) -> bool:
        return self.current_temp >= self.max_temp * 0.95

    def utilization(self) -> float:
        return self.current_load / self.max_load

    # ----------------------------------------------------------------- power
    def calculate_power(self) -> float:
        """Instantaneous draw.

        An idle server still draws a fraction of its base power, which is what
        makes consolidation (fewer servers kept busy) actually save energy.
        """
        if self.current_load <= 0:
            return self.base_power * self.idle_power_factor
        load_factor = self.current_load / self.max_load
        return self.base_power + (self.max_power - self.base_power) * load_factor

    def heat_generation(self, load: Optional[float] = None) -> float:
        current = self.current_load if load is None else load
        load_factor = current / self.max_load
        return float(load_factor * (self.max_power - self.base_power) * HEAT_TO_POWER_RATIO)

    # ------------------------------------------------------------ temperature
    def equilibrium_temp(self, ambient_temp: float, cooling_efficiency: float) -> float:
        efficiency = max(0.05, float(cooling_efficiency))
        return ambient_temp + self.heat_generation() / (
            self.dissipation_coefficient * efficiency
        )

    def update_temperature(self, ambient_temp: float, cooling_efficiency: float = 0.5) -> None:
        self.last_ambient = ambient_temp
        self.last_fan_speed = cooling_efficiency
        target = self.equilibrium_temp(ambient_temp, cooling_efficiency)
        self.current_temp = min(
            self.max_temp,
            max(min(ambient_temp, target), self.current_temp + self.thermal_inertia * (target - self.current_temp)),
        )

    def predicted_temp_after(self, additional_load: float) -> float:
        """Conservative upper bound on the temperature after adding a task.

        Uses half the nominal dissipation so the estimate errs on the safe side.
        """
        heat = self.heat_generation(self.current_load + additional_load)
        degraded = self.dissipation_coefficient * 0.5
        target = self.last_ambient + heat / degraded
        return float(min(self.max_temp, max(self.current_temp, target)))

    def __repr__(self) -> str:
        return (
            f"Server(id={self.server_id}, type={self.server_type}, "
            f"load={self.current_load:.1f}, temp={self.current_temp:.1f})"
        )

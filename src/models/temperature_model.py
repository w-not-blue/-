import numpy as np
from typing import List, Optional
from src.models.server import Server

class TemperatureModel:
    """Room-level bookkeeping and the fan speed control law."""

    def __init__(
        self,
        room_volume: float = 100,
        air_flow_rate: float = 10,
        initial_temp: float = 25,
        fan_target_temp: float = 35,
    ) -> None:
        self.room_volume: float = room_volume
        self.air_flow_rate: float = air_flow_rate
        self.current_temp: float = initial_temp
        self.target_temp: float = fan_target_temp

    def update_room_temperature(self, servers: List[Server], ambient_temp: float, cooling_power: float) -> float:
        total_heat = sum(s.calculate_power() * 0.8 for s in servers)
        heat_removed = cooling_power * 0.9
        heat_change = total_heat - heat_removed
        temp_change = heat_change / (1000 * 1.2 * self.room_volume)
        self.current_temp = max(
            ambient_temp - 5,
            min(ambient_temp + 20, self.current_temp + temp_change)
        )
        return self.current_temp

    def get_cooling_demand(self) -> float:
        delta = self.current_temp - self.target_temp
        if delta <= 0:
            return 0.0
        return delta * 100

    def adjust_fan_speed(
        self,
        target_temp: Optional[float] = None,
        reference_temp: Optional[float] = None,
    ) -> float:
        """Fan speed in [0.2, 1.0] driven by the hottest machine.

        ``reference_temp`` lets the caller feed in the peak server temperature;
        without it the room temperature is used.
        """
        if target_temp is not None:
            self.target_temp = float(target_temp)

        current = self.current_temp if reference_temp is None else float(reference_temp)
        delta = current - self.target_temp

        if delta < -2:
            return 0.2
        elif delta < 0:
            return 0.4
        elif delta < 2:
            return 0.6
        elif delta < 5:
            return 0.8
        else:
            return 1.0

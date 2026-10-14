import numpy as np
from typing import Any, Dict, List, Optional
from src.models.server import Server

class EnergyModel:
    """IT + cooling power model.

    Cooling is derived from the heat the IT load actually produces, scaled by
    the outdoor temperature. A part-load term keeps the chillers from looking
    perfectly efficient when the room is nearly empty, which is what makes PUE
    depend on the scheduling decision instead of only on the weather.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        cfg = config
        if cfg is None:
            try:
                from src.utils.config import Config
                cfg = Config().get("energy", {}) or {}
            except Exception:
                cfg = {}

        self.cop_base: float = float(cfg.get("cop_base", 2.5))
        self.cop_delta_coefficient: float = float(cfg.get("cop_delta_coefficient", 0.1))
        self.heat_to_power_ratio: float = float(cfg.get("heat_to_power_ratio", 0.8))
        self.pue_base: float = float(cfg.get("pue_base", 1.8))
        self.target_temp: float = float(cfg.get("target_temp", 22))
        self.part_load_penalty: float = float(cfg.get("part_load_penalty", 0.5))
        self.temp_penalty_coefficient: float = float(cfg.get("temp_penalty_coefficient", 1.0))
        self.temp_reference: float = float(cfg.get("temp_reference", 35.0))
        self.cooling_efficiency_base: float = 0.5

    # ------------------------------------------------------------------- IT
    def server_power(self, server: Server) -> float:
        return server.calculate_power()

    def calculate_server_energy(self, server: Server) -> float:
        return server.calculate_power()

    def total_it_power(self, servers: List[Server]) -> float:
        return float(sum(s.calculate_power() for s in servers))

    # -------------------------------------------------------------- cooling
    def get_cop(self, ambient_temp: float, target_temp: Optional[float] = None) -> float:
        target = self.target_temp if target_temp is None else float(target_temp)
        delta_temp = max(0.0, target - ambient_temp)
        return self.cop_base + delta_temp * self.cop_delta_coefficient

    def rated_heat(self, servers: List[Server]) -> float:
        if not servers:
            return 0.0
        return float(
            sum(max(0.0, s.max_power - s.base_power) for s in servers) * self.heat_to_power_ratio
        )

    def heat_load_ratio(self, servers: List[Server]) -> float:
        rated = self.rated_heat(servers)
        if rated <= 0:
            return 1.0
        heat = self.total_it_power(servers) * self.heat_to_power_ratio
        return float(min(1.0, max(0.0, heat / rated)))

    def temperature_lift_factor(self, peak_temp: Optional[float]) -> float:
        """Chillers work harder when the hot aisle runs hotter."""
        if peak_temp is None:
            return 1.0
        reference = max(1.0, self.temp_reference)
        lift = max(0.0, (float(peak_temp) - reference) / reference)
        return float(1.0 + self.temp_penalty_coefficient * lift)

    def calculate_cooling_energy(
        self,
        servers: List[Server],
        ambient_temp: float,
        target_temp: Optional[float] = None,
        peak_temp: Optional[float] = None,
    ) -> float:
        total_heat = self.total_it_power(servers) * self.heat_to_power_ratio
        cop = max(1e-6, self.get_cop(ambient_temp, target_temp))
        ideal = total_heat / cop
        ratio = self.heat_load_ratio(servers)
        partial = 1.0 + self.part_load_penalty * (1.0 - ratio)
        return float(ideal * partial * self.temperature_lift_factor(peak_temp))

    def calculate_total_energy(
        self,
        servers: List[Server],
        ambient_temp: float,
        target_temp: Optional[float] = None,
        peak_temp: Optional[float] = None,
    ) -> float:
        return self.total_it_power(servers) + self.calculate_cooling_energy(
            servers, ambient_temp, target_temp, peak_temp
        )

    def get_pue(
        self,
        servers: List[Server],
        ambient_temp: float,
        target_temp: Optional[float] = None,
        peak_temp: Optional[float] = None,
    ) -> Optional[float]:
        """Return the PUE, or None when there is no IT load to divide by.

        Returning None keeps "the room only draws standby power" visible in the
        output instead of masking it as a plausible-looking 1.8.
        """
        it_power = self.total_it_power(servers)
        if it_power <= 0:
            return None
        total = it_power + self.calculate_cooling_energy(
            servers, ambient_temp, target_temp, peak_temp
        )
        return float(total / it_power)

    def get_dcie(self, servers: List[Server], ambient_temp: float) -> Optional[float]:
        pue = self.get_pue(servers, ambient_temp)
        if pue is None:
            return None
        return float(1.0 / pue)

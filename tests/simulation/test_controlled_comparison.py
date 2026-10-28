"""Tests for the controlled comparison guarantees.

These lock in the properties that make the algorithm numbers comparable:
one task stream per scenario, an SLA metric that can actually move, and a
ranking rule that refuses to reward an algorithm for serving less work.
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd

from src.simulation import DataCenterSimulator
from src.simulation.metrics import SERVICE_RATE_FLOOR, rank_algorithms, summarize, safe_number
from src.simulation.task_generator import TaskGenerator

ALGORITHMS = ["greedy", "genetic", "round_robin", "damo_ets"]


class _StubConfig:
    """Minimal config object so tests do not depend on the YAML files."""

    def __init__(self, overrides=None):
        self._data = {
            "simulation": {"random_seed": 42, "sla_slack": 1.5},
            "task_generation": {"base_rate": 6, "peak_rate": 20, "peak_hours": [9, 18]},
            "task_completion": {"mode": "service_based", "completion_probability": 0.3},
            "cooling": {"power_coefficient": 500, "target_temp": 22, "fan_target_temp": 35,
                        "dissipation_coefficient": 8.0, "thermal_inertia": 0.25},
            "energy": {"idle_power_factor": 0.6, "part_load_penalty": 0.5},
            "genetic": {"population_size": 12, "generations": 8, "enable_parallel": False},
            "greedy": {"temperature_weight": 0.3},
            "data_source": {"mode": "synthetic"},
        }
        if overrides:
            for section, values in overrides.items():
                self._data.setdefault(section, {}).update(values)

    def to_dict(self):
        return self._data


def _simulator(servers=40, hours=8, seed=42, overrides=None):
    return DataCenterSimulator(
        num_servers=servers,
        simulation_hours=hours,
        seed=seed,
        config=_StubConfig(overrides),
    )


def test_all_algorithms_see_the_same_offered_demand():
    """The control guarantee is about demand, not about what each algorithm served."""
    sim = _simulator()
    df = sim.run_comparison(algorithms=ALGORITHMS)
    offered = sim.control_signature(df)
    assert set(offered) == set(ALGORITHMS)
    assert len(set(offered.values())) == 1, offered
    assert sim.is_controlled(df)


def test_scenario_stream_is_reproducible():
    first = TaskGenerator(seed=7, sla_slack=1.5).build_stream(6)
    second = TaskGenerator(seed=7, sla_slack=1.5).build_stream(6)
    assert first == second
    assert TaskGenerator.stream_digest(first) == TaskGenerator.stream_digest(second)


def test_materialised_tasks_are_independent_instances():
    generator = TaskGenerator(seed=3)
    stream = generator.build_stream(3)
    run_a = generator.materialize_stream(stream)
    run_b = generator.materialize_stream(stream)
    assert run_a[0][0] is not run_b[0][0]
    run_a[0][0].completed_time = 99
    assert run_b[0][0].completed_time is None


def test_deadline_violations_are_reachable():
    tight = _simulator(servers=12, hours=12, overrides={"simulation": {"sla_slack": 1.0}})
    df = tight.run_comparison(algorithms=ALGORITHMS)
    stats = tight.get_statistics(df)
    assert any(entry["deadline_violations"] > 0 for entry in stats.values())

    loose = _simulator(servers=60, hours=12, overrides={"simulation": {"sla_slack": 4.0}})
    df_loose = loose.run_comparison(algorithms=ALGORITHMS)
    loose_stats = loose.get_statistics(df_loose)
    assert all(entry["deadline_violation_rate"] <= 1.0 for entry in loose_stats.values())


def test_summary_reports_service_rate_and_energy_intensity():
    sim = _simulator(servers=60)
    df = sim.run_comparison(algorithms=["greedy", "round_robin"])
    stats = sim.get_statistics(df)
    for entry in stats.values():
        assert 0.0 <= entry["service_rate"] <= 1.0
        assert entry["energy_per_served_load"] is not None
        assert entry["energy_per_served_load"] > 0
        assert entry["assigned_tasks"] + entry["dropped_tasks"] > 0


def test_ranking_excludes_algorithms_below_the_service_floor():
    stats = {
        "honest": {"service_rate": 0.99, "energy_per_served_load": 5.0, "max_temp": 40.0},
        "slacker": {"service_rate": 0.40, "energy_per_served_load": 3.0, "max_temp": 35.0},
    }
    ranking = rank_algorithms(stats)
    assert ranking["best"] == "honest"
    assert [item["algorithm"] for item in ranking["excluded"]] == ["slacker"]
    assert "服务率" in ranking["excluded"][0]["reason"]


def test_ranking_orders_by_energy_intensity():
    stats = {
        "a": {"service_rate": 1.0, "energy_per_served_load": 7.0, "max_temp": 40.0},
        "b": {"service_rate": 1.0, "energy_per_served_load": 5.0, "max_temp": 42.0},
    }
    ranking = rank_algorithms(stats)
    assert ranking["best"] == "b"
    assert [item["algorithm"] for item in ranking["eligible"]] == ["b", "a"]


def test_ranking_reports_no_winner_when_nothing_qualifies():
    stats = {"a": {"service_rate": 0.5, "energy_per_served_load": 5.0, "max_temp": 40.0}}
    ranking = rank_algorithms(stats)
    assert ranking["best"] is None
    assert "不给出能耗排名" in ranking["note"]


def test_cooling_cost_rises_with_the_hot_aisle():
    sim = _simulator(servers=10, hours=4)
    for server in sim.servers:
        server.current_load = server.max_load
    cool = sim.energy_model.calculate_cooling_energy(sim.servers, 25.0, 22.0, peak_temp=30.0)
    hot = sim.energy_model.calculate_cooling_energy(sim.servers, 25.0, 22.0, peak_temp=50.0)
    assert hot > cool


def test_algorithms_differ_in_energy_intensity():
    """Scheduling choices must be able to move the energy figure."""
    sim = _simulator(servers=24, hours=12)
    df = sim.run_comparison(algorithms=["greedy", "round_robin", "damo_ets"])
    stats = sim.get_statistics(df)
    intensities = [
        entry["energy_per_served_load"] for entry in stats.values()
        if entry["energy_per_served_load"] is not None
    ]
    assert len(intensities) >= 2
    assert len(set(intensities)) > 1, intensities


def test_safe_number_maps_non_numbers_to_none():
    assert safe_number(float("nan")) is None
    assert safe_number(None) is None
    assert safe_number("abc") is None
    assert safe_number(2.5) == 2.5


def test_summarize_tolerates_missing_columns():
    df = pd.DataFrame([
        {"time": 0, "algorithm": "x", "total_energy": 100.0, "total_load": 10.0},
        {"time": 1, "algorithm": "x", "total_energy": 120.0, "total_load": 12.0},
    ])
    stats = summarize(df)
    assert stats["x"]["avg_energy"] == 110.0
    assert stats["x"]["service_rate"] == 1.0
    assert stats["x"]["deadline_violations"] == 0


def test_pue_responds_to_ambient_temperature():
    sim = _simulator(servers=10, hours=6)
    warm = sim.energy_model.get_pue(sim.servers, 30.0, 22.0)
    cool = sim.energy_model.get_pue(sim.servers, 10.0, 22.0)
    assert warm is not None and cool is not None
    assert warm > cool


def test_pue_is_none_without_it_load():
    sim = _simulator(servers=0, hours=2)
    assert sim.energy_model.get_pue([], 25.0, 22.0) is None


def test_config_missing_key_raises_attribute_error():
    from src.utils.config import Config
    config = Config()
    try:
        config.definitely_not_a_key
    except AttributeError:
        return
    raise AssertionError("Config 未知键应当抛 AttributeError, 否则 hasattr 永远为真")


def test_service_floor_constant_is_documented():
    assert 0.0 < SERVICE_RATE_FLOOR <= 1.0

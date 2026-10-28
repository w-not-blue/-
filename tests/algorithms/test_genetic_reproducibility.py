"""Reproducibility tests for the GeneticScheduler."""

import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.algorithms import GeneticScheduler, GeneticAlgorithm
from src.models import Server, Task


def _make_servers(count: int = 6) -> list:
    return [Server(server_id=i) for i in range(count)]


def _make_tasks(count: int = 8) -> list:
    return [
        Task(
            load=10 + (i % 3) * 5,
            priority=(i % 5) + 1,
            workload_class="cpu_bound" if i % 2 == 0 else "gpu_bound",
        )
        for i in range(count)
    ]


def test_genetic_algorithm_reproducible_with_seed():
    """GeneticAlgorithm.evolve() with the same seed returns the same best assignment."""
    tasks = _make_tasks(8)
    servers_a = _make_servers(6)
    servers_b = _make_servers(6)

    ga_a = GeneticAlgorithm(servers_a, tasks, seed=42)
    ga_b = GeneticAlgorithm(servers_b, tasks, seed=42)

    best_a, fit_a = ga_a.evolve()
    best_b, fit_b = ga_b.evolve()

    assert best_a == best_b, "Best individuals must match with same seed"
    assert abs(fit_a - fit_b) < 1e-9


def test_genetic_scheduler_reproducible_via_full_run():
    """GeneticScheduler.schedule_tasks returns the same set of (task, server_id) pairs."""
    tasks = _make_tasks(8)
    servers_a = _make_servers(6)
    servers_b = _make_servers(6)

    s1 = GeneticScheduler(servers_a, seed=7)
    s2 = GeneticScheduler(servers_b, seed=7)

    res1 = s1.schedule_tasks(tasks)
    res2 = s2.schedule_tasks(tasks)

    pairs1 = [(t.task_id, s.server_id if s else None) for t, s in res1]
    pairs2 = [(t.task_id, s.server_id if s else None) for t, s in res2]
    assert pairs1 == pairs2


def test_genetic_different_seed_produces_different_results():
    """Sanity check: different seeds should usually give different results."""
    tasks = _make_tasks(10)
    s1 = GeneticScheduler(_make_servers(8), seed=1)
    s2 = GeneticScheduler(_make_servers(8), seed=2)
    res1 = s1.schedule_tasks(tasks)
    res2 = s2.schedule_tasks(tasks)
    pairs1 = [(t.task_id, s.server_id if s else None) for t, s in res1]
    pairs2 = [(t.task_id, s.server_id if s else None) for t, s in res2]
    assert pairs1 != pairs2

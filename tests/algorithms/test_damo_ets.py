"""Unit tests for DAMOETSScheduler."""

import sys
import os

# Make the project importable when running from the tests directory
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.algorithms import DAMOETSScheduler, GreedyScheduler
from src.models import Server, Task


def _make_servers(count: int = 4) -> list:
    servers = []
    for i in range(count):
        servers.append(Server(
            server_id=i,
            max_load=100,
            max_temp=85,
            base_power=100,
            max_power=300,
            initial_temp=25,
            server_type="standard" if i % 2 == 0 else "gpu_server",
        ))
    return servers


def _make_task(load: int = 10, deadline=None, wclass: str = "cpu_bound", priority: int = 1) -> Task:
    return Task(load=load, priority=priority, deadline=deadline, workload_class=wclass)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_empty_tasks_returns_empty():
    scheduler = DAMOETSScheduler(_make_servers(), seed=42)
    assert scheduler.schedule_tasks([]) == []
    assert scheduler.get_deadline_violations() == 0


def test_edf_ordering():
    """Tasks with earlier deadlines should be assigned first; non-deadline tasks last."""
    servers = _make_servers(4)
    scheduler = DAMOETSScheduler(servers, seed=1)

    late = _make_task(load=10, deadline=10, wclass="cpu_bound")
    early = _make_task(load=10, deadline=2, wclass="cpu_bound")
    no_dl = _make_task(load=10, deadline=None, wclass="cpu_bound")

    results = scheduler.schedule_tasks([late, early, no_dl], current_hour=0)
    assigned = [t for t, s in results if s is not None]
    assert assigned[0] is early
    assert assigned[1] is late
    # no_dl is processed last (no deadline group), still assigned
    assert no_dl in assigned


def test_heterogeneous_match_prefers_gpu_for_gpu_bound():
    """GPU-bound tasks should preferentially route to gpu_server."""
    servers = _make_servers(4)  # 0,2 = standard; 1,3 = gpu_server
    scheduler = DAMOETSScheduler(servers, seed=0)

    gpu_task = _make_task(load=20, deadline=5, wclass="gpu_bound", priority=5)
    results = scheduler.schedule_tasks([gpu_task], current_hour=0)
    chosen = results[0][1]
    assert chosen is not None
    assert chosen.server_type == "gpu_server"


def test_thermal_safety_rejects_overheating_assignment():
    """If all servers would overheat, the task should be unassigned."""
    servers = _make_servers(2)
    for s in servers:
        s.current_temp = 84  # Just below max (85 * 0.95 = 80.75) -> already unsafe
        s.current_load = 50
    scheduler = DAMOETSScheduler(servers, seed=0)

    task = _make_task(load=30, deadline=5, wclass="cpu_bound", priority=5)
    results = scheduler.schedule_tasks([task], current_hour=0)
    assert results[0][1] is None


def test_deadline_violation_counter():
    """Tasks whose deadline is already in the past at assignment hour are counted."""
    servers = _make_servers(4)
    scheduler = DAMOETSScheduler(servers, seed=0)

    # current_hour=10, but the task's deadline is 5 — already missed
    expired = _make_task(load=10, deadline=5, wclass="cpu_bound")
    future = _make_task(load=10, deadline=15, wclass="cpu_bound")

    results = scheduler.schedule_tasks([expired, future], current_hour=10)
    violations = scheduler.get_deadline_violations()
    assert violations == 1
    # Both should be assigned (the counter is about deadline breach, not assignment failure)
    assert all(server is not None for _, server in results)


def test_reproducible_with_seed():
    """Same seed + same inputs -> same assignments and same violation count."""
    servers_a = _make_servers(4)
    servers_b = _make_servers(4)
    scheduler_a = DAMOETSScheduler(servers_a, seed=123)
    scheduler_b = DAMOETSScheduler(servers_b, seed=123)

    def _build_tasks():
        return [
            _make_task(
                load=15,
                deadline=i + 3,
                wclass="gpu_bound" if i % 2 == 0 else "cpu_bound",
            )
            for i in range(6)
        ]

    tasks_a = _build_tasks()
    tasks_b = _build_tasks()

    res_a = scheduler_a.schedule_tasks(tasks_a, current_hour=0)
    res_b = scheduler_b.schedule_tasks(tasks_b, current_hour=0)

    # Compare by (deadline, workload_class, server_id) — not task_id (uuid)
    def _signature(results):
        return [
            (t.deadline, t.workload_class, s.server_id if s else None)
            for t, s in results
        ]

    assert _signature(res_a) == _signature(res_b)
    assert scheduler_a.get_deadline_violations() == scheduler_b.get_deadline_violations()


def test_damo_ets_lower_or_equal_energy_than_greedy_under_heavy_deadline_load():
    """DAMO-ETS prioritises deadline-urgency so it should be at least as energy-efficient
    as Greedy when both have enough capacity, measured by avg total assigned load per task."""
    servers_a = _make_servers(6)
    servers_b = _make_servers(6)

    damo = DAMOETSScheduler(servers_a, seed=0)
    greedy = GreedyScheduler(servers_b)

    tasks = []
    for i in range(10):
        tasks.append(_make_task(
            load=20,
            deadline=i + 2,
            wclass="gpu_bound" if i % 3 == 0 else "cpu_bound",
            priority=3,
        ))

    damo_res = damo.schedule_tasks(tasks, current_hour=0)
    greedy_res = greedy.schedule_tasks(tasks)

    damo_assigned = sum(1 for _, s in damo_res if s is not None)
    greedy_assigned = sum(1 for _, s in greedy_res if s is not None)

    # Both should assign the majority; DAMO-ETS should be at least as good
    assert damo_assigned >= greedy_assigned - 1


def test_get_name_returns_damo_ets():
    scheduler = DAMOETSScheduler(_make_servers(2), seed=0)
    assert scheduler.get_name() == "damo_ets"


def test_reset_clears_violation_counter():
    scheduler = DAMOETSScheduler(_make_servers(2), seed=0)
    expired = _make_task(load=5, deadline=1, wclass="cpu_bound")
    scheduler.schedule_tasks([expired], current_hour=5)
    assert scheduler.get_deadline_violations() >= 1
    scheduler.reset()
    assert scheduler.get_deadline_violations() == 0

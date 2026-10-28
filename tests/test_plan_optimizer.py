import time

from src.services.plan_optimizer import PlanOptimizer, _server_counts


def test_server_counts_include_bounds():
    assert _server_counts(10, 10) == [10]
    counts = _server_counts(10, 30)
    assert counts[0] == 10
    assert counts[-1] == 30
    assert len(counts) == len(set(counts))


def test_small_global_optimization_runs():
    optimizer = PlanOptimizer()
    job = optimizer.start({
        "server_min": 12,
        "server_max": 16,
        "simulation_hours": 2,
        "algorithms": ["greedy", "round_robin"],
        "target": "pue",
        "service_rate_floor": 0.98,
        "site_id": "北京市",
        "include_weights": False,
    })
    deadline = time.time() + 60
    current = optimizer.snapshot(job["job_id"])
    while current["status"] not in ("success", "failed") and time.time() < deadline:
        time.sleep(0.2)
        current = optimizer.snapshot(job["job_id"])
    assert current["status"] == "success", current.get("error")
    result = current["result"]
    assert result["top"]
    assert len(result["top"]) <= 3
    assert result["best"]["metrics"]["service_rate"] >= 0.98
    assert all(plan["metrics"].get("avg_pue") is not None for plan in result["top"])

"""Controlled algorithm comparison experiment.

Every algorithm replays the same frozen task stream for a scenario, so the
resulting numbers are comparable instead of four unrelated runs. The CSV carries
the service rate next to the energy figures, and the ranking applies the shared
rule from ``src.simulation.metrics``.

Usage (from the project root, the directory that contains ``src/``):
    python experiments/compare_algorithms.py [--seed 42] [--output PATH]
"""

import argparse
import csv
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.simulation import DataCenterSimulator  # noqa: E402
from src.simulation.metrics import SERVICE_RATE_FLOOR, rank_algorithms  # noqa: E402

ALGORITHMS = ["greedy", "genetic", "round_robin", "damo_ets"]

SCENARIOS = [
    {"num_servers": 40, "sim_hours": 24},
    {"num_servers": 80, "sim_hours": 24},
]

FIELDNAMES = [
    "algorithm", "num_servers", "sim_hours", "seed", "scenario_digest",
    "avg_energy", "peak_temp", "p95_temp", "avg_pue", "total_load",
    "assigned", "dropped", "service_rate", "energy_per_served_load",
    "deadline_violations", "deadline_violation_rate",
    "eligible", "rank",
]


def run_one(num_servers: int, sim_hours: int, seed: int) -> dict:
    print(f"  -> running {num_servers} servers x {sim_hours}h (seed={seed})", flush=True)
    sim = DataCenterSimulator(
        num_servers=num_servers,
        simulation_hours=sim_hours,
        seed=seed,
    )
    df = sim.run_comparison(algorithms=ALGORITHMS)
    controlled = sim.is_controlled(df)

    ranking = sim.ranking or rank_algorithms(sim.get_statistics(df))
    ranks = {item["algorithm"]: item["rank"] for item in ranking.get("eligible", [])}

    rows = []
    for algo, metrics in sim.get_statistics(df).items():
        rows.append({
            "algorithm": algo,
            "num_servers": num_servers,
            "sim_hours": sim_hours,
            "seed": seed,
            "scenario_digest": sim.scenario_digest,
            "avg_energy": round(metrics["avg_energy"], 4) if metrics["avg_energy"] is not None else "",
            "peak_temp": round(metrics["max_temp"], 4) if metrics["max_temp"] is not None else "",
            "p95_temp": round(metrics["p95_temp"], 4) if metrics["p95_temp"] is not None else "",
            "avg_pue": round(metrics["avg_pue"], 4) if metrics["avg_pue"] is not None else "",
            "total_load": round(metrics["total_load"], 2),
            "assigned": metrics["assigned_tasks"],
            "dropped": metrics["dropped_tasks"],
            "service_rate": metrics["service_rate"],
            "energy_per_served_load": metrics["energy_per_served_load"],
            "deadline_violations": metrics["deadline_violations"],
            "deadline_violation_rate": metrics["deadline_violation_rate"],
            "eligible": "yes" if algo in ranks else "no",
            "rank": ranks.get(algo, ""),
        })
    return {
        "rows": rows,
        "ranking": ranking,
        "controlled": controlled,
        "metadata": sim.run_metadata,
    }


def write_csv(rows: list, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


def write_ranking(payload: dict, path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
    print(f"Wrote ranking trace to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Controlled algorithm comparison")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument(
        "--output",
        type=str,
        default=os.path.join(HERE, "results", "compare.csv"),
        help="Output CSV path",
    )
    parser.add_argument("--servers", type=int, nargs="+", default=None,
                        help="Override the server counts to test")
    args = parser.parse_args()

    scenarios = list(SCENARIOS)
    if args.servers:
        scenarios = [{"num_servers": n, "sim_hours": 24} for n in args.servers]

    print("=" * 60)
    print("Algorithm comparison experiment (controlled)")
    print(f"  algorithms : {ALGORITHMS}")
    print(f"  scenarios  : {scenarios}")
    print(f"  seed       : {args.seed}")
    print(f"  rule       : service rate >= {SERVICE_RATE_FLOOR:.0%}, then W per served load")
    print("=" * 60)

    all_rows: list = []
    traces: list = []
    t0 = time.time()
    for sc in scenarios:
        try:
            outcome = run_one(sc["num_servers"], sc["sim_hours"], args.seed)
        except Exception as exc:
            print(f"  !! scenario {sc} failed: {exc}")
            continue
        all_rows.extend(outcome["rows"])
        traces.append({
            "scenario": sc,
            "controlled": outcome["controlled"],
            "ranking": outcome["ranking"],
            "metadata": outcome["metadata"],
        })
        if not outcome["controlled"]:
            print("  !! WARNING: algorithms did not all see the same first-hour load")

    elapsed = time.time() - t0
    print(f"Total time: {elapsed:.2f}s")

    write_csv(all_rows, args.output)
    write_ranking(
        {
            "seed": args.seed,
            "service_rate_floor": SERVICE_RATE_FLOOR,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "traces": traces,
        },
        os.path.join(os.path.dirname(args.output) or ".", "compare_ranking.json"),
    )

    print("\nControlled comparison result:")
    for trace in traces:
        sc = trace["scenario"]
        best = trace["ranking"].get("best")
        label = best if best else "无合格算法"
        print(f"  {sc['num_servers']}svr x {sc['sim_hours']}h -> {label}"
              f"  (controlled={trace['controlled']})")


if __name__ == "__main__":
    main()

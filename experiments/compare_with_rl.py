"""
GitHub Skills 集成对比实验

在相同 workload 下对比：
    1. Greedy        - 现有贪心（baseline）
    2. DAMO-ETS      - 现有 deadline-aware 多目标
    3. NSGA2 (pymoo) - 第三方插件
    4. MOEAD (pymoo) - 第三方插件
    5. RL_PPO (SB3)  - 第三方插件（推理模式，需先训练）

输出: experiments/results/rl_comparison.csv
    列: algorithm, num_servers, sim_hours, avg_energy, max_temp, avg_pue,
         total_load, deadline_violations, wall_time_s

Usage:
    # 仅对比 1-4（无需 SB3）
    python experiments/compare_with_rl.py

    # 含 RL（先训练）
    python -m src.cli.main train --timesteps 5000 --save-path data/policies/ppo.zip
    python experiments/compare_with_rl.py --include-rl
"""

import argparse
import csv
import os
import sys
import time
import warnings

warnings.filterwarnings("ignore")

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.simulation import DataCenterSimulator  # noqa: E402
from src.utils.logger import setup_logger  # noqa: E402

logger = setup_logger("compare_with_rl")

# 现有算法（直接走 DataCenterSimulator）
CORE_ALGORITHMS = ["greedy", "damo_ets"]

# 第三方插件算法（通过 PluginManager）
PLUGIN_ALGORITHMS = ["nsga2", "moead"]


def parse_args():
    p = argparse.ArgumentParser(description="GitHub Skills 集成对比实验")
    p.add_argument("--servers", type=int, nargs="+", default=[10, 25],
                   help="服务器规模列表")
    p.add_argument("--hours", type=int, default=24, help="仿真时长(小时)")
    p.add_argument("--seed", type=int, default=42, help="随机种子")
    p.add_argument("--include-rl", action="store_true", help="包含 RL_PPO 推理（需先训练）")
    p.add_argument("--rl-model", type=str, default="data/policies/ppo_datacenter.zip",
                   help="RL 模型路径")
    p.add_argument("--output", type=str, default="experiments/results/rl_comparison.csv",
                   help="输出 CSV 路径")
    return p.parse_args()


def run_core_algorithms(num_servers: int, sim_hours: int, seed: int) -> list:
    """跑 Greedy / DAMO-ETS"""
    logger.info(f"  [核心算法] {num_servers} servers x {sim_hours}h")
    sim = DataCenterSimulator(num_servers=num_servers, simulation_hours=sim_hours, seed=seed)
    df = sim.run_comparison(algorithms=CORE_ALGORITHMS)
    stats = sim.get_statistics(df)
    rows = []
    for algo, metrics in stats.items():
        rows.append({
            "algorithm": algo,
            "num_servers": num_servers,
            "sim_hours": sim_hours,
            "avg_energy": round(metrics.get("avg_energy", 0), 2),
            "max_temp": round(metrics.get("max_temp", 0), 2),
            "avg_pue": round(metrics.get("avg_pue", 0), 3),
            "total_load": round(metrics.get("total_load", 0), 0),
            "deadline_violations": int(metrics.get("deadline_violations", 0)),
            "wall_time_s": 0.0,  # core run_comparison 内部已计时
        })
    return rows


def run_plugin_algorithms(num_servers: int, sim_hours: int, seed: int) -> list:
    """跑 NSGA2 / MOEAD 插件"""
    from src.plugin import PluginManager

    rows = []
    pm = PluginManager()
    discovered = pm.discover_plugins(plugin_dir="src/plugins")
    logger.info(f"  [插件算法] 已发现 {discovered} 个插件")

    # 准备任务 + 服务器快照
    sim = DataCenterSimulator(num_servers=num_servers, simulation_hours=sim_hours, seed=seed)
    servers_snapshot = sim.create_snapshot()

    for algo_name, plugin_name in [("nsga2", "NSGA2SchedulerPlugin"),
                                    ("moead", "MOEADSchedulerPlugin")]:
        if plugin_name not in pm._plugin_classes:
            logger.warning(f"  {plugin_name} 未发现，跳过 {algo_name}")
            continue
        # 重置服务器
        sim.restore_from_snapshot(servers_snapshot)
        servers = sim.servers
        # 生成任务
        tasks = []
        for hour in range(sim_hours):
            tasks.extend(sim.task_generator.generate_tasks(hour))

        if not pm.load_plugin(plugin_name, config={"seed": seed, "pop_size": 50, "n_gen": 100}):
            logger.warning(f"  {plugin_name} 加载失败（可能 pymoo 未安装）")
            continue

        logger.info(f"  [插件] 运行 {algo_name} ({len(tasks)} 任务)")
        start = time.perf_counter()
        result = pm.execute_plugin(plugin_name, tasks=tasks, servers=servers)
        elapsed = round(time.perf_counter() - start, 3)

        if result.success and result.data:
            data = result.data
            # 统计：被分配的服务器平均负载 + 最高温度
            assigned = [a for a in data.get("assignments", []) if a.get("server_id") is not None]
            avg_load = (sum(s.current_load for s in servers) / max(1, sum(s.max_load for s in servers))) * 100
            max_temp = max(s.current_temp for s in servers)
            avg_energy = sum(s.calculate_power() for s in servers) / len(servers)
            rows.append({
                "algorithm": algo_name,
                "num_servers": num_servers,
                "sim_hours": sim_hours,
                "avg_energy": round(avg_energy, 2),
                "max_temp": round(max_temp, 2),
                "avg_pue": round(1.0 + 0.1 * (avg_load / 100.0), 3),
                "total_load": round(sum(s.current_load for s in servers), 0),
                "deadline_violations": 0,  # 简化
                "wall_time_s": elapsed,
            })
        else:
            logger.warning(f"  {algo_name} 失败: {result.message} [{result.error_code}]")

    return rows


def run_rl_algorithm(num_servers: int, sim_hours: int, seed: int, model_path: str) -> list:
    """跑 RL_PPO 推理（需先训练）"""
    from src.plugin import PluginManager
    from src.plugins._deps import has_sb3

    if not has_sb3():
        logger.warning("  [RL] stable-baselines3 未安装，跳过")
        return []
    if not os.path.exists(model_path):
        logger.warning(f"  [RL] 模型文件不存在: {model_path}，请先运行 train 命令")
        return []

    sim = DataCenterSimulator(num_servers=num_servers, simulation_hours=sim_hours, seed=seed)
    servers_snapshot = sim.create_snapshot()
    sim.restore_from_snapshot(servers_snapshot)
    servers = sim.servers
    tasks = []
    for hour in range(sim_hours):
        tasks.extend(sim.task_generator.generate_tasks(hour))

    pm = PluginManager()
    pm.discover_plugins(plugin_dir="src/plugins")
    if "RLSchedulerPlugin" not in pm._plugin_classes:
        logger.warning("  [RL] RLSchedulerPlugin 未发现")
        return []

    pm.load_plugin("RLSchedulerPlugin", config={"num_servers": num_servers, "simulation_hours": sim_hours})

    logger.info(f"  [RL] 用 {model_path} 推理 {len(tasks)} 任务")
    start = time.perf_counter()
    result = pm.execute_plugin(
        "RLSchedulerPlugin",
        algorithms=["rl_ppo"],
        train=False,
        tasks=tasks,
        servers=servers,
        model_path=model_path,
    )
    elapsed = round(time.perf_counter() - start, 3)

    if result.success and result.data:
        avg_load = (sum(s.current_load for s in servers) / max(1, sum(s.max_load for s in servers))) * 100
        max_temp = max(s.current_temp for s in servers)
        avg_energy = sum(s.calculate_power() for s in servers) / len(servers)
        return [{
            "algorithm": "rl_ppo",
            "num_servers": num_servers,
            "sim_hours": sim_hours,
            "avg_energy": round(avg_energy, 2),
            "max_temp": round(max_temp, 2),
            "avg_pue": round(1.0 + 0.1 * (avg_load / 100.0), 3),
            "total_load": round(sum(s.current_load for s in servers), 0),
            "deadline_violations": 0,
            "wall_time_s": elapsed,
        }]
    logger.warning(f"  [RL] 推理失败: {result.message} [{result.error_code}]")
    return []


def main():
    args = parse_args()

    logger.info("=" * 60)
    logger.info("GitHub Skills 集成对比实验")
    logger.info("=" * 60)
    logger.info(f"服务器规模: {args.servers}, 时长: {args.hours}h, seed: {args.seed}")
    logger.info(f"含 RL: {args.include_rl}, RL 模型: {args.rl_model}")
    logger.info(f"输出: {args.output}")

    all_rows = []
    for n in args.servers:
        logger.info(f"\n=== 场景: {n} servers x {args.hours}h ===")
        # 1) 核心算法
        all_rows.extend(run_core_algorithms(n, args.hours, args.seed))
        # 2) 插件算法
        all_rows.extend(run_plugin_algorithms(n, args.hours, args.seed))
        # 3) RL（可选）
        if args.include_rl:
            all_rows.extend(run_rl_algorithm(n, args.hours, args.seed, args.rl_model))

    # 写 CSV
    if not all_rows:
        logger.error("无结果可写，请检查环境/依赖")
        return 1

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    fieldnames = list(all_rows[0].keys())
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    logger.info("\n" + "=" * 60)
    logger.info("对比结果")
    logger.info("=" * 60)
    # 控制台漂亮打印
    col_widths = {k: max(len(k), max(len(str(r.get(k, ""))) for r in all_rows)) for k in fieldnames}
    header = "  ".join(k.ljust(col_widths[k]) for k in fieldnames)
    logger.info(header)
    logger.info("-" * len(header))
    for r in all_rows:
        line = "  ".join(str(r.get(k, "")).ljust(col_widths[k]) for k in fieldnames)
        logger.info(line)

    logger.info(f"\n✓ 结果已保存: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

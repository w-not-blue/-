from src.simulation import DataCenterSimulator
from src.visualization import (
    plot_energy_comparison,
    plot_temperature_trend,
    plot_load_distribution,
    plot_pue_comparison,
    plot_cooling_power,
    plot_summary_stats
)
from src.utils import (
    save_simulation_data,
    export_results_to_excel,
    save_run_metadata,
    setup_logger,
)
import argparse
import os
from datetime import datetime

from src.services import run_store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校园数据中心智能调度系统 - 仿真主入口")
    parser.add_argument("--num-servers", type=int, default=None,
                        help="服务器数量（默认取 config/default.yaml 的 simulation.num_servers）")
    parser.add_argument("--hours", type=int, default=None,
                        help="仿真小时数（默认取 config/default.yaml 的 simulation.simulation_hours）")
    parser.add_argument(
        "--algorithms",
        nargs="+",
        default=["greedy", "genetic", "round_robin", "damo_ets"],
        help="要参与对比的算法列表",
    )
    parser.add_argument("--seed", type=int, default=None, help="随机种子（保证可复现）")
    return parser.parse_args()


def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "--"
    return f"{value:.{digits}f}{suffix}"


def main() -> None:
    args = parse_args()

    logger = setup_logger("main")
    logger.info("=" * 60)
    logger.info("校园/小型数据中心智能散热与算力调度系统")
    logger.info("=" * 60)
    if args.seed is not None:
        logger.info(f"随机种子: {args.seed}")

    try:
        simulator = DataCenterSimulator(
            num_servers=args.num_servers,
            simulation_hours=args.hours,
            seed=args.seed,
        )
        meta = simulator.run_metadata
        logger.info(f"数据来源: {meta.get('ambient_source')} / 任务流: {meta.get('scenario_source')}")
        logger.info(f"SLA 松弛系数: {meta.get('sla_slack')} | 任务完成模型: {meta.get('completion_mode')}")

        logger.info(f"\n[1/5] 运行算法对比仿真 (algorithms={args.algorithms}) ...")
        df = simulator.run_comparison(algorithms=args.algorithms)

        offered = simulator.control_signature(df)
        controlled = simulator.is_controlled(df)
        logger.info(f"[2/5] 受控性检查: 各算法首小时到达负载 {list(offered.values())} -> "
                    f"{'同一任务流快照' if controlled else '警告: 任务流不一致'}")

        logger.info("[3/5] 保存仿真数据...")
        csv_path = save_simulation_data(df)
        excel_path = export_results_to_excel(df)
        metadata_path = save_run_metadata({
            **simulator.run_metadata,
            "num_servers": simulator.num_servers,
            "simulation_hours": simulator.simulation_hours,
            "seed": simulator.seed,
            "algorithms": args.algorithms,
            "first_hour_offered": offered,
            "first_hour_served": simulator.first_hour_loads(df),
            "controlled": controlled,
        })
        logger.info(f"  - CSV: {csv_path}")
        logger.info(f"  - Excel: {excel_path}")
        logger.info(f"  - 运行元数据: {metadata_path}")

        logger.info("\n[4/5] 生成可视化图表...")
        reports_dir = 'reports'
        os.makedirs(reports_dir, exist_ok=True)

        plot_energy_comparison(df, os.path.join(reports_dir, 'energy_comparison.png'))
        plot_temperature_trend(df, os.path.join(reports_dir, 'temperature_trend.png'))
        plot_load_distribution(df, os.path.join(reports_dir, 'load_distribution.png'))
        plot_pue_comparison(df, os.path.join(reports_dir, 'pue_comparison.png'))
        plot_cooling_power(df, os.path.join(reports_dir, 'cooling_power.png'))

        logger.info("  - energy_comparison.png")
        logger.info("  - temperature_trend.png")
        logger.info("  - load_distribution.png")
        logger.info("  - pue_comparison.png")
        logger.info("  - cooling_power.png")

        logger.info("\n[5/5] 统计指标与排名...")
        stats = simulator.get_statistics(df)
        ranking = simulator.get_ranking(df)

        try:
            run_id = run_store.create_run({
                "num_servers": simulator.num_servers,
                "simulation_hours": simulator.simulation_hours,
                "algorithms": args.algorithms,
                "site_id": simulator.run_metadata.get("site_id"),
            }, origin="cli")
            run_metadata = {
                **simulator.run_metadata,
                "run_id": run_id,
                "origin": "cli",
                "num_servers": simulator.num_servers,
                "simulation_hours": simulator.simulation_hours,
                "seed": simulator.seed,
                "algorithms": args.algorithms,
                "first_hour_offered": offered,
                "first_hour_served": simulator.first_hour_loads(df),
                "controlled": controlled,
            }
            save_run_metadata(run_metadata)
            run_store.save_results(run_id, df, run_metadata, stats)
            finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            run_store.update_run(
                run_id, status="success", progress=100,
                started_at=finished_at, finished_at=finished_at,
            )
            logger.info(f"  - 运行编号: {run_id}")
        except Exception as run_exc:
            logger.warning(f"运行历史写入失败: {run_exc}")

        logger.info("\n" + "=" * 60)
        logger.info("仿真结果统计")
        logger.info("=" * 60)
        for algo, metrics in stats.items():
            logger.info(f"\n【{algo.upper()}算法】")
            logger.info(f"  平均能耗: {_fmt(metrics['avg_energy'], 2, ' W')}")
            logger.info(f"  峰值温度: {_fmt(metrics['max_temp'], 2, ' °C')}"
                        f"  (95 分位 {_fmt(metrics['p95_temp'], 2, ' °C')})")
            logger.info(f"  平均PUE: {_fmt(metrics['avg_pue'], 4)}")
            logger.info(f"  总处理负载: {_fmt(metrics['total_load'], 0)}")
            logger.info(f"  任务服务率: {_fmt((metrics['service_rate'] or 0) * 100, 2, '%')}"
                        f"  (分配 {metrics['assigned_tasks']} / 丢弃 {metrics['dropped_tasks']})")
            logger.info(f"  单位服务负载能耗: {_fmt(metrics['energy_per_served_load'], 4, ' W/负载')}")
            logger.info(f"  累计 deadline 违约: {metrics['deadline_violations']}"
                        f"  ({_fmt((metrics['deadline_violation_rate'] or 0) * 100, 2, '%')})")

        logger.info("\n" + "=" * 60)
        logger.info("受控排名")
        logger.info("=" * 60)
        logger.info(f"规则: {ranking['rule']}")
        for item in ranking['eligible']:
            logger.info(f"  #{item['rank']} {item['algorithm']}: "
                        f"{_fmt(item['energy_per_served_load'], 4, ' W/负载')} "
                        f"(服务率 {_fmt(item['service_rate'] * 100, 2, '%')})")
        for item in ranking['excluded']:
            logger.info(f"  排除 {item['algorithm']}: {item['reason']}")
        logger.info(f"结论: {ranking['note']}")

        logger.info("\n" + "=" * 60)
        logger.info("仿真完成！结果已保存到 reports/ 和 data/ 目录")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"仿真过程发生错误: {str(e)}", exc_info=True)
        raise


if __name__ == "__main__":
    main()

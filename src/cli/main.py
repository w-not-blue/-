import argparse
import json
import os
from src.plugin import PluginManager
from src.plugin.simulation_plugin import SimulationPlugin
from src.api.app import run as run_api
from src.utils.logger import setup_logger
from src.utils.config import Config
from src.adapters import AutoAdapter, LocalAdapter, RemoteAdapter
try:
    from src.adapters import CudaAdapter
except ImportError:
    CudaAdapter = None
try:
    from src.adapters import AscendAdapter
except ImportError:
    AscendAdapter = None
import platform
import psutil
from typing import Dict, Any

logger = setup_logger("cli")
config = Config()

def run_simulation(args):
    """运行仿真命令（支持环境自动检测）"""
    adapter = _select_adapter(args)
    
    if not adapter.initialize():
        logger.error("适配器初始化失败")
        return

    logger.info(f"使用适配器: {adapter.get_type().value}")
    logger.info(f"  能力: {adapter.get_capability().description}")
    
    algorithms = args.algorithms.split(',') if args.algorithms else ['greedy', 'genetic', 'round_robin']
    
    params = {
        'num_servers': args.servers,
        'simulation_hours': args.hours,
        'parallel_workers': args.workers,
        'run_mode': args.mode,
        'algorithms': algorithms,
        'visualization': not args.no_plot,
        'save_results': not args.no_save
    }
    
    logger.info(f"参数: servers={args.servers}, hours={args.hours}, workers={args.workers}, mode={args.mode}")
    logger.info(f"算法: {algorithms}")
    
    result = adapter.execute(**params)
    
    _display_result(result)

def _select_adapter(args) -> 'BaseAdapter':
    """根据参数和环境选择适配器"""
    adapter_map = {
        'auto': AutoAdapter,
        'local': LocalAdapter,
        'remote': RemoteAdapter,
    }
    if CudaAdapter is not None:
        adapter_map['cuda'] = CudaAdapter
    if AscendAdapter is not None:
        adapter_map['ascend'] = AscendAdapter
    
    adapter_type = args.adapter or 'auto'
    adapter_cls = adapter_map.get(adapter_type, AutoAdapter)
    adapter = adapter_cls()
    
    if adapter_type == 'auto':
        logger.info("正在检测环境...")
        status = adapter.get_status()
        logger.info(f"可用适配器: {len(status.get('available_adapters', []))} 个")
        for a in status.get('available_adapters', []):
            logger.info(f"  - {a['name']}: {'可用' if a['available'] else '不可用'}")
    
    return adapter

def _display_payload(result: Any) -> Dict[str, Any]:
    """Accept either a PluginResult or a plain dict and expose a dict."""
    if hasattr(result, 'success') and hasattr(result, 'data'):
        payload = dict(result.data) if isinstance(result.data, dict) else {}
        payload['success'] = bool(result.success)
        if not payload.get('success'):
            payload['error'] = result.message or result.error_code or '未知错误'
        return payload
    if isinstance(result, dict):
        return result
    return {'success': False, 'error': '返回类型无法识别'}


def _display_result(result: Any) -> None:
    """显示仿真结果"""
    result = _display_payload(result)
    if result.get('success'):
        logger.info("=" * 60)
        logger.info("仿真结果统计")
        logger.info("=" * 60)
        
        for algo, metrics in result.get('statistics', {}).items():
            logger.info(f"【{algo.upper()}算法】")
            logger.info(f"  平均能耗: {metrics.get('avg_energy', 0):.2f} W")
            logger.info(f"  最高温度: {metrics.get('max_temp', 0):.2f} °C")
            logger.info(f"  平均PUE: {metrics.get('avg_pue', 0):.2f}")
            logger.info(f"  总处理负载: {metrics.get('total_load', 0):.0f}")
        
        logger.info(f"总记录数: {result.get('records_count', 0)}")
        
        if 'csv_path' in result:
            logger.info(f"数据文件: {result['csv_path']}")
        if 'excel_path' in result:
            logger.info(f"Excel文件: {result['excel_path']}")
        if 'plots' in result:
            logger.info("图表文件:")
            for name, path in result['plots'].items():
                logger.info(f"  - {path}")
        if 'adapter_type' in result:
            logger.info(f"适配器: {result['adapter_type']}")
    else:
        logger.error(f"仿真失败: {result.get('error', 'Unknown error')}")

def run_api_server(args):
    """启动API服务器"""
    run_api(host=args.host, port=args.port, debug=args.debug)

def list_algorithms(args):
    """列出可用算法"""
    algorithms = [
        {'name': 'greedy', 'description': '贪心调度算法', 'type': '实时调度'},
        {'name': 'genetic', 'description': '遗传优化算法', 'type': '全局优化'},
        {'name': 'round_robin', 'description': '轮询调度算法', 'type': '基准算法'},
        {'name': 'damo_ets', 'description': 'DAMO-ETS 多目标能效调度', 'type': '多目标优化'},
        # 第三方插件（GitHub Skills 集成）
        {'name': 'nsga2', 'description': 'pymoo NSGA2 多目标调度（能耗+温度+deadline）', 'type': '多目标优化', 'plugin': 'NSGA2SchedulerPlugin', 'source': 'github.com/anyoptimization/pymoo'},
        {'name': 'moead', 'description': 'pymoo MOEAD 分解多目标调度', 'type': '多目标优化', 'plugin': 'MOEADSchedulerPlugin', 'source': 'github.com/anyoptimization/pymoo'},
    ]
    logger.info(json.dumps(algorithms, indent=2, ensure_ascii=False))

def detect_environment(args):
    """环境检测命令"""
    info = config.get_runtime_info()
    
    logger.info("=" * 60)
    logger.info("系统环境检测报告")
    logger.info("=" * 60)
    logger.info(f"操作系统:    {info['os']} {info['os_release']} ({info['arch']})")
    logger.info(f"Python版本:  {info['python_version']}")
    logger.info(f"CPU核心:     {info['cpu_physical']} 物理 / {info['cpu_logical']} 逻辑")
    logger.info(f"内存:        {info['total_memory_gb']} GB (可用: {info['available_memory_gb']} GB)")
    logger.info(f"Docker容器:  {'是' if info.get('is_docker') else '否'}")
    logger.info(f"Kubernetes:  {'是' if info.get('is_k8s') else '否'}")
    logger.info(f"GPU/CUDA:    {'可用' if info.get('has_cuda') else '不可用'} ({info.get('cuda_count', 0)} 个)")
    logger.info(f"昇腾NPU:     {'可用' if info.get('has_ascend') else '不可用'}")
    logger.info("推荐配置:")
    logger.info(f"  并行工作数: {config.get_optimal_workers()}")
    configured_mode = config.get('simulation', {}).get('run_mode', 'standard')
    logger.info(f"  运行模式:   {configured_mode} (来自配置)")

    adapters = {
        '本地CPU': LocalAdapter(),
        '远程服务': RemoteAdapter(),
    }
    if CudaAdapter is not None:
        adapters['NVIDIA CUDA'] = CudaAdapter()
    if AscendAdapter is not None:
        adapters['华为昇腾'] = AscendAdapter()
    
    logger.info("可用适配器:")
    for name, adapter in adapters.items():
        available = "✓ 可用" if adapter.is_available() else "✗ 不可用"
        cap = adapter.get_capability()
        logger.info(f"  {name}: {available}")
        logger.info(f"    {cap.description}")

def benchmark_adapters(args):
    """适配器基准测试命令"""
    logger.info("适配器基准测试")
    logger.info("=" * 60)
    
    adapters_to_test = []
    
    local = LocalAdapter()
    if local.is_available():
        adapters_to_test.append(('本地CPU', local))
    
    remote = RemoteAdapter()
    if args.remote_url:
        remote_config = {'api_url': args.remote_url}
        if remote.initialize(remote_config):
            adapters_to_test.append(('远程服务', remote))
    
    if CudaAdapter is not None:
        cuda = CudaAdapter()
        if cuda.is_available():
            adapters_to_test.append(('NVIDIA CUDA', cuda))
    
    if AscendAdapter is not None:
        ascend = AscendAdapter()
        if ascend.is_available():
            adapters_to_test.append(('华为昇腾', ascend))
    
    if not adapters_to_test:
        logger.warning("没有可用的适配器进行测试")
        return

    for name, adapter in adapters_to_test:
        adapter.initialize({
            'num_servers': args.bench_servers,
            'simulation_hours': args.bench_hours,
            'parallel_workers': 2
        })
        logger.info(f"测试 {name}...")
        import time
        start = time.time()
        result = adapter.execute(
            num_servers=args.bench_servers,
            simulation_hours=args.bench_hours,
            algorithms=['greedy'],
            visualization=False,
            save_results=False
        )
        elapsed = time.time() - start
        logger.info(f"  耗时: {elapsed:.2f}s")
        bench = _display_payload(result)
        logger.info(f"  结果: {'成功' if bench.get('success') else '失败'}")
        adapter.cleanup()

def train_rl_agent(args):
    """训练 RL 调度器（GitHub Skills 集成 — Stable Baselines3 PPO）"""
    from src.plugins._deps import has_gymnasium, has_sb3, get_dependency_status

    logger.info("=" * 60)
    logger.info("RL 调度器训练 (GitHub Skills 集成)")
    logger.info("=" * 60)
    logger.info(f"依赖探测: gymnasium={has_gymnasium()}, stable-baselines3={has_sb3()}")
    if not has_gymnasium() or not has_sb3():
        logger.error("依赖缺失，请先执行: pip install gymnasium stable-baselines3")
        return

    from src.plugin import PluginManager

    pm = PluginManager()
    discovered = pm.discover_plugins(plugin_dir="src/plugins")
    logger.info(f"自动发现插件: {discovered} 个")
    for name in pm._plugin_classes.keys():
        logger.info(f"  - {name}")

    if "RLSchedulerPlugin" not in pm._plugin_classes:
        logger.error("未发现 RLSchedulerPlugin，请检查 src/plugins/rl_ppo_scheduler.py")
        return

    config = {
        "num_servers": args.servers,
        "simulation_hours": args.hours,
        "model_path": args.save_path,
    }
    if not pm.load_plugin("RLSchedulerPlugin", config=config):
        logger.error("RLSchedulerPlugin 加载失败")
        return

    logger.info(
        f"开始训练 PPO agent: servers={args.servers}, hours={args.hours}, "
        f"timesteps={args.timesteps}, save={args.save_path}"
    )
    result = pm.execute_plugin(
        "RLSchedulerPlugin",
        algorithms=["rl_ppo"],
        train=True,
        total_timesteps=args.timesteps,
        model_path=args.save_path,
        learning_rate=args.lr,
        seed=args.seed,
    )

    if result.success:
        logger.info("✓ 训练完成")
        logger.info(f"  模型路径: {result.data.get('model_path')}")
        logger.info(f"  训练耗时: {result.data.get('training_time_s')} s")
        logger.info(f"  训练步数: {result.data.get('total_timesteps')}")
        logger.info(f"  推理: pm.execute_plugin('RLSchedulerPlugin', train=False, model_path='{args.save_path}')")
    else:
        logger.error(f"训练失败: {result.message} [{result.error_code}]")
        if result.error_detail:
            logger.error(f"  详情: {result.error_detail}")


def check_deps(args):
    """检查 GitHub Skills 可选依赖状态"""
    from src.plugins._deps import get_dependency_status
    status = get_dependency_status()
    logger.info("=" * 60)
    logger.info("GitHub Skills 可选依赖状态")
    logger.info("=" * 60)
    for dep_name, info in status.items():
        avail = "✓ 可用" if info["available"] else "✗ 缺失"
        version = info.get("version") or "—"
        logger.info(f"  {dep_name}: {avail}  (version={version})")
        if not info["available"]:
            logger.info(f"    安装: {info['install_hint']}")


def create_parser():
    """创建命令行解析器"""
    parser = argparse.ArgumentParser(
        description='校园数据中心智能散热与算力调度系统 - CLI工具 v2.0',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    run_parser = subparsers.add_parser('run', help='运行仿真')
    run_parser.add_argument('--servers', type=int, default=10, help='服务器数量 (1-1000)')
    run_parser.add_argument('--hours', type=int, default=24, help='仿真时长/小时 (1-720)')
    run_parser.add_argument('--workers', type=int, default=4, help='并行工作器数')
    run_parser.add_argument('--mode', type=str, default='standard',
                           choices=['standard', 'distributed', 'large_scale'],
                           help='运行模式')
    run_parser.add_argument('--algorithms', type=str, default='greedy,genetic,round_robin',
                           help='算法列表(逗号分隔)')
    run_parser.add_argument('--adapter', type=str, default='auto',
                           choices=['auto', 'local', 'remote', 'cuda', 'ascend'],
                           help='适配器类型')
    run_parser.add_argument('--no-plot', action='store_true', help='跳过绘图')
    run_parser.add_argument('--no-save', action='store_true', help='跳过保存')
    run_parser.set_defaults(func=run_simulation)

    api_parser = subparsers.add_parser('api', help='启动API服务')
    api_parser.add_argument('--host', type=str, default='0.0.0.0', help='绑定地址')
    api_parser.add_argument('--port', type=int, default=5000, help='端口号')
    api_parser.add_argument('--debug', action='store_true', help='调试模式')
    api_parser.set_defaults(func=run_api_server)

    list_parser = subparsers.add_parser('list', help='列出可用算法')
    list_parser.set_defaults(func=list_algorithms)

    env_parser = subparsers.add_parser('env', help='环境检测')
    env_parser.set_defaults(func=detect_environment)

    bench_parser = subparsers.add_parser('benchmark', help='适配器基准测试')
    bench_parser.add_argument('--bench-servers', type=int, default=10, help='测试服务器数')
    bench_parser.add_argument('--bench-hours', type=int, default=4, help='测试仿真时长')
    bench_parser.add_argument('--remote-url', type=str, help='远程API地址')
    bench_parser.set_defaults(func=benchmark_adapters)

    # 检查 GitHub Skills 可选依赖
    deps_parser = subparsers.add_parser('deps', help='检查 GitHub Skills 可选依赖')
    deps_parser.set_defaults(func=check_deps)

    return parser

def cli():
    """CLI入口函数"""
    parser = create_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        logger.info("可用命令:")
        logger.info("  run       运行仿真（支持自动环境检测）")
        logger.info("  api       启动API服务")
        logger.info("  list      列出可用算法")
        logger.info("  env       检测运行环境")
        logger.info("  benchmark 适配器基准测试")
        logger.info("  deps      检查可选依赖")
        return

    try:
        args.func(args)
    except Exception as e:
        logger.error(f"命令执行失败: {str(e)}", exc_info=True)
        return 1
    return 0

if __name__ == '__main__':
    import sys
    sys.exit(cli())

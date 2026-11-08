"""
校园数据中心智能散热与算力调度系统 - SDK客户端 v1.0

提供简洁的API接口，支持多环境自适应对接。

支持的接入方式:
1. 本地模式    - 直接调用本地插件（自动检测依赖可用性）
2. 远程模式    - 通过REST API调用远程服务（含连接池+重试）
3. 混合模式    - 本地优先，远程兜底（自动故障转移）

环境自适应:
- CPU核心数检测 → 自动调整并行度
- GPU/昇腾NPU检测 → 硬件加速策略
- 操作系统识别 → 平台特定优化
- Python版本检测 → 兼容性适配

示例用法:
    # 自动检测环境
    sdk = DataCenterSimulatorSDK(auto_detect=True)
    
    # 本地模式
    sdk = DataCenterSimulatorSDK(mode='local')
    result = sdk.run_simulation(num_servers=10, algorithms=['greedy', 'genetic'])
    
    # 远程模式（含重试）
    sdk = DataCenterSimulatorSDK(mode='remote', api_url="http://localhost:5000")
    result = sdk.run_simulation(num_servers=10)
    
    # 混合模式（自动故障转移）
    sdk = DataCenterSimulatorSDK(mode='hybrid', api_url="http://localhost:5000")
"""

import requests
import platform
import psutil
from typing import Dict, Any, List, Optional, Union, Callable
from dataclasses import dataclass, field
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from src.plugin import PluginManager
from src.plugin.simulation_plugin import SimulationPlugin
from src.utils.logger import setup_logger
import os

logger = setup_logger("sdk")

@dataclass
class EnvironmentInfo:
    """环境信息数据结构"""
    os_name: str = ""
    os_version: str = ""
    python_version: str = ""
    cpu_cores: int = 1
    cpu_logical: int = 1
    total_memory_gb: float = 0
    available_memory_gb: float = 0
    has_gpu: bool = False
    gpu_count: int = 0
    has_ascend_npu: bool = False
    is_docker: bool = False
    is_kubernetes: bool = False
    platform_arch: str = ""

class EnvironmentDetector:
    """环境探测器 - 自动检测运行时环境"""

    @staticmethod
    def detect() -> EnvironmentInfo:
        info = EnvironmentInfo()
        
        info.os_name = platform.system()
        info.os_version = platform.version()
        info.python_version = platform.python_version()
        info.platform_arch = platform.machine()
        
        try:
            info.cpu_cores = psutil.cpu_count(logical=False) or 1
            info.cpu_logical = psutil.cpu_count(logical=True) or 1
        except Exception:
            info.cpu_cores = 1
            info.cpu_logical = 1
        
        try:
            mem = psutil.virtual_memory()
            info.total_memory_gb = mem.total / (1024 ** 3)
            info.available_memory_gb = mem.available / (1024 ** 3)
        except Exception:
            info.total_memory_gb = 0
            info.available_memory_gb = 0
        
        info.has_gpu = EnvironmentDetector._detect_gpu()
        info.gpu_count = EnvironmentDetector._count_gpu()
        info.has_ascend_npu = EnvironmentDetector._detect_ascend()
        info.is_docker = EnvironmentDetector._detect_docker()
        info.is_kubernetes = EnvironmentDetector._detect_kubernetes()
        
        return info

    @staticmethod
    def _detect_gpu() -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except ImportError:
            return False

    @staticmethod
    def _count_gpu() -> int:
        try:
            import torch
            return torch.cuda.device_count() if torch.cuda.is_available() else 0
        except ImportError:
            return 0

    @staticmethod
    def _detect_ascend() -> bool:
        try:
            import torch_npu
            return torch_npu.npu.is_available()
        except ImportError:
            pass
        if os.path.exists('/usr/local/Ascend'):
            return True
        return False

    @staticmethod
    def _detect_docker() -> bool:
        return os.path.exists('/.dockerenv') or 'docker' in open('/proc/1/cgroup', 'r').read() if os.path.exists('/proc/1/cgroup') else False

    @staticmethod
    def _detect_kubernetes() -> bool:
        return bool(os.environ.get('KUBERNETES_SERVICE_HOST'))

class RetryableSession:
    """可重试的HTTP会话"""

    def __init__(self, max_retries: int = 3, backoff_factor: float = 0.5,
                 status_forcelist: List[int] = None):
        self.session = requests.Session()
        
        if status_forcelist is None:
            status_forcelist = [429, 500, 502, 503, 504]
        
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist,
            allowed_methods=["GET", "POST", "PUT"]
        )
        
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20
        )
        
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            'User-Agent': f'DataCenterSchedulerSDK/2.0 ({platform.system()}; Python/{platform.python_version()})'
        })

    def post(self, url: str, json_data: Dict = None, timeout: int = 120) -> requests.Response:
        return self.session.post(url, json=json_data, timeout=timeout)

    def get(self, url: str, timeout: int = 30) -> requests.Response:
        return self.session.get(url, timeout=timeout)

    def close(self):
        self.session.close()

class DataCenterSimulatorSDK:
    """
    数据中心仿真SDK v1.0
    
    支持三种运行模式:
    - local: 纯本地调用
    - remote: 纯远程API调用
    - hybrid: 本地优先，远程兜底
    """

    def __init__(
        self,
        mode: str = 'auto',
        api_url: Optional[str] = None,
        max_retries: int = 3,
        timeout: int = 300,
        auto_detect: bool = True
    ):
        """
        初始化SDK
        
        Args:
            mode: 运行模式 ('auto', 'local', 'remote', 'hybrid')
            api_url: 远程API地址（remote/hybrid模式需要）
            max_retries: 远程请求最大重试次数
            timeout: 请求超时秒数
            auto_detect: 是否自动检测环境
        """
        self.max_retries = max_retries
        self.timeout = timeout
        self.plugin_manager = None
        self.session: Optional[RetryableSession] = None
        
        if auto_detect:
            self.env_info = EnvironmentDetector.detect()
            logger.info(f"Environment detected: {self.env_info}")
        else:
            self.env_info = EnvironmentInfo()
        
        self._resolve_mode(mode, api_url)
        
        if self._local_mode or self._is_hybrid():
            self.plugin_manager = PluginManager()
            self.plugin_manager.register_plugin(SimulationPlugin)
            logger.info("SDK initialized in LOCAL/HYBRID mode with plugin manager")
        
        if self._remote_mode or self._is_hybrid():
            if not api_url:
                api_url = "http://localhost:5000"
            self.api_url = api_url.rstrip('/')
            self.session = RetryableSession(max_retries=max_retries)
            logger.info(f"SDK initialized with remote endpoint: {self.api_url}")

    def _resolve_mode(self, mode: str, api_url: Optional[str]) -> None:
        if mode == 'auto':
            if api_url:
                self._mode = 'hybrid'
            else:
                self._mode = 'local'
        else:
            self._mode = mode
        
        self._local_mode = self._mode == 'local'
        self._remote_mode = self._mode == 'remote'

    def _is_hybrid(self) -> bool:
        return self._mode == 'hybrid'

    def _auto_parallel_workers(self) -> int:
        cores = self.env_info.cpu_cores
        if cores <= 2:
            return 1
        elif cores <= 4:
            return 2
        elif cores <= 8:
            return 4
        else:
            return min(cores // 2, 32)

    def get_environment_info(self) -> Dict[str, Any]:
        """获取环境检测信息"""
        return {
            'os': self.env_info.os_name,
            'os_version': self.env_info.os_version,
            'python_version': self.env_info.python_version,
            'cpu_cores': self.env_info.cpu_cores,
            'cpu_logical': self.env_info.cpu_logical,
            'memory_gb': round(self.env_info.total_memory_gb, 2),
            'has_gpu': self.env_info.has_gpu,
            'gpu_count': self.env_info.gpu_count,
            'has_ascend_npu': self.env_info.has_ascend_npu,
            'is_docker': self.env_info.is_docker,
            'is_kubernetes': self.env_info.is_kubernetes,
            'platform_arch': self.env_info.platform_arch,
            'recommended_workers': self._auto_parallel_workers()
        }

    def run_simulation(
        self,
        num_servers: int = 10,
        simulation_hours: int = 24,
        algorithms: List[str] = None,
        parallel_workers: Optional[int] = None,
        run_mode: str = 'standard',
        visualization: bool = True,
        save_results: bool = True
    ) -> Dict[str, Any]:
        """
        运行仿真
        
        Args:
            num_servers: 服务器数量 (1-1000)
            simulation_hours: 仿真时长（小时）(1-720)
            algorithms: 算法列表
            parallel_workers: 并行工作器数（None=自动检测）
            run_mode: 运行模式 ('standard', 'distributed', 'large_scale')
            visualization: 是否生成可视化图表
            save_results: 是否保存结果
        
        Returns:
            仿真结果字典
        """
        if algorithms is None:
            algorithms = ['greedy', 'genetic', 'round_robin']
        
        if parallel_workers is None:
            parallel_workers = self._auto_parallel_workers()
        
        config = {
            'num_servers': num_servers,
            'simulation_hours': simulation_hours,
            'parallel_workers': parallel_workers,
            'run_mode': run_mode,
        }

        if self._is_hybrid():
            return self._run_hybrid(config, algorithms, visualization, save_results)
        elif self._local_mode:
            return self._run_local(config, algorithms, visualization, save_results)
        else:
            return self._run_remote(config, algorithms)

    def _run_hybrid(
        self, config: Dict[str, Any], algorithms: List[str],
        visualization: bool, save_results: bool
    ) -> Dict[str, Any]:
        try:
            return self._run_local(config, algorithms, visualization, save_results)
        except Exception as e:
            logger.warning(f"Local execution failed ({e}), falling back to remote")
            if self.session:
                return self._run_remote(config, algorithms)
            return {'success': False, 'error': f'Local failed and remote not available: {e}'}

    def _run_local(
        self, config: Dict[str, Any], algorithms: List[str],
        visualization: bool, save_results: bool
    ) -> Dict[str, Any]:
        try:
            self.plugin_manager.load_plugin('SimulationPlugin', config)
            result = self.plugin_manager.execute_plugin(
                'SimulationPlugin',
                algorithms=algorithms,
                visualization=visualization,
                save_results=save_results
            )
            self.plugin_manager.unload_plugin('SimulationPlugin')
            return result
        except Exception as e:
            logger.error(f"Local simulation error: {str(e)}")
            return {'success': False, 'error': str(e)}

    def _run_remote(
        self, config: Dict[str, Any], algorithms: List[str]
    ) -> Dict[str, Any]:
        try:
            url = f"{self.api_url}/api/v1/simulation"
            payload = {
                'num_servers': config.get('num_servers', 10),
                'simulation_hours': config.get('simulation_hours', 24),
                'parallel_workers': config.get('parallel_workers', 4),
                'run_mode': config.get('run_mode', 'standard'),
                'algorithms': algorithms
            }
            
            response = self.session.post(url, json_data=payload, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Remote API error: {str(e)}")
            return {'success': False, 'error': str(e)}

    def get_metrics(self) -> Dict[str, Any]:
        if self._is_hybrid():
            return self._try_local_then_remote('get_metrics')
        return self._get_metrics_local() if self._local_mode else self._get_metrics_remote()

    def _get_metrics_local(self) -> Dict[str, Any]:
        from src.utils import load_simulation_data
        import pandas as pd
        
        df = load_simulation_data()
        if df is None:
            return {'success': False, 'message': 'No data found'}
        
        result = {}
        for algo in df['algorithm'].unique():
            algo_df = df[df['algorithm'] == algo]
            result[algo] = {
                'avg_energy': float(algo_df['total_energy'].mean()),
                'max_temp': float(algo_df['avg_temperature'].max()),
                'avg_pue': float(algo_df['pue'].mean()),
                'total_load': float(algo_df['total_load'].sum())
            }
        return {'success': True, 'data': result}

    def _get_metrics_remote(self) -> Dict[str, Any]:
        try:
            response = self.session.get(f"{self.api_url}/api/v1/metrics")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {'success': False, 'error': str(e)}

    def get_raw_data(self) -> Dict[str, Any]:
        if self._local_mode or self._is_hybrid():
            return self._get_raw_data_local()
        return self._get_raw_data_remote()

    def _get_raw_data_local(self) -> Dict[str, Any]:
        from src.utils import load_simulation_data
        df = load_simulation_data()
        if df is None:
            return {'success': False, 'message': 'No data found'}
        return {'success': True, 'data': df.to_dict('records')}

    def _get_raw_data_remote(self) -> Dict[str, Any]:
        try:
            response = self.session.get(f"{self.api_url}/api/v1/data")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {'success': False, 'error': str(e)}

    def _try_local_then_remote(self, method: str) -> Dict[str, Any]:
        try:
            local_result = getattr(self, f'_{method}_local')()
            if local_result.get('success'):
                return local_result
        except Exception:
            pass
        try:
            return getattr(self, f'_{method}_remote')()
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def list_algorithms(self) -> List[Dict[str, str]]:
        return [
            {'name': 'greedy', 'description': '贪心调度算法', 'type': '实时调度'},
            {'name': 'genetic', 'description': '遗传优化算法', 'type': '全局优化'},
            {'name': 'round_robin', 'description': '轮询调度算法', 'type': '基准算法'},
            # 第三方插件（GitHub Skills 集成）
            {'name': 'nsga2', 'description': 'pymoo NSGA2 多目标调度（能耗+温度+deadline）',
             'type': '多目标优化', 'plugin': 'NSGA2SchedulerPlugin',
             'source': 'github.com/anyoptimization/pymoo'},
            {'name': 'moead', 'description': 'pymoo MOEAD 分解多目标调度',
             'type': '多目标优化', 'plugin': 'MOEADSchedulerPlugin',
             'source': 'github.com/anyoptimization/pymoo'},
        ]

    def list_plugins(self) -> Dict[str, Any]:
        if self._local_mode or self._is_hybrid():
            plugins = []
            for name, plugin_class in self.plugin_manager._plugin_classes.items():
                is_loaded = name in self.plugin_manager._plugin_instances
                manifest = plugin_class().get_manifest()
                plugins.append({
                    'name': name, 'version': manifest.version,
                    'description': ",".join(manifest.supported_operations), 'loaded': is_loaded
                })
            return {'success': True, 'plugins': plugins}
        try:
            response = self.session.get(f"{self.api_url}/api/v1/plugins")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def load_plugin(self, plugin_name: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._local_mode or self._is_hybrid():
            success = self.plugin_manager.load_plugin(plugin_name, config or {})
            if success:
                info = self.plugin_manager.get_plugin_info(plugin_name)
                return {'success': True, 'message': 'Plugin loaded', 'metadata': info.to_dict() if info else {}}
            return {'success': False, 'message': 'Failed to load plugin'}
        try:
            response = self.session.post(f"{self.api_url}/api/v1/plugins/{plugin_name}/load", json_data=config or {})
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def unload_plugin(self, plugin_name: str) -> Dict[str, Any]:
        if self._local_mode or self._is_hybrid():
            self.plugin_manager.unload_plugin(plugin_name)
            return {'success': True, 'message': 'Plugin unloaded'}
        try:
            response = self.session.post(f"{self.api_url}/api/v1/plugins/{plugin_name}/unload")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def health_check(self) -> bool:
        if self._local_mode:
            return True
        if self._is_hybrid():
            local_ok = self.plugin_manager is not None
            remote_ok = False
            if self.session:
                try:
                    response = self.session.get(f"{self.api_url}/health", timeout=5)
                    remote_ok = response.status_code == 200
                except Exception:
                    pass
            return local_ok or remote_ok
        try:
            response = self.session.get(f"{self.api_url}/health", timeout=5)
            return response.status_code == 200
        except Exception:
            return False

    def get_system_status(self) -> Dict[str, Any]:
        env = self.get_environment_info()
        loaded = len(self.plugin_manager._plugin_instances) if self.plugin_manager else 0
        registered = len(self.plugin_manager._plugin_classes) if self.plugin_manager else 0
        
        return {
            'success': True,
            'status': {
                'mode': self._mode,
                'loaded_plugins': loaded,
                'registered_plugins': registered,
                'status': 'healthy' if self.health_check() else 'degraded'
            },
            'environment': env
        }

    def close(self):
        if self.plugin_manager:
            for name in list(self.plugin_manager._plugin_instances.keys()):
                self.plugin_manager.unload_plugin(name)
        if self.session:
            self.session.close()
        logger.info("SDK closed")

    def __repr__(self) -> str:
        return f"DataCenterSimulatorSDK(mode={self._mode}, api_url={self.api_url if hasattr(self, 'api_url') else None})"

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
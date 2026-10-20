import platform
import psutil
from typing import Dict, Any, Optional
from src.adapters.base import (
    BaseAdapter, AdapterType, AdapterCapability, normalise_result
)
from src.utils.logger import setup_logger
from src.utils.config import Config

logger = setup_logger("local_adapter")

class LocalAdapter(BaseAdapter):
    """
    本地CPU适配器
    
    适用于:
    - 本地开发环境
    - 单机部署
    - 小规模仿真(<100台服务器)
    
    参考项目: joblib, concurrent.futures
    """

    def __init__(self):
        self._available = True
        self._initialized = False
        self._config = {}
        self._num_workers = 2

    def get_type(self) -> AdapterType:
        return AdapterType.LOCAL

    def get_capability(self) -> AdapterCapability:
        cores = psutil.cpu_count(logical=False) or 4
        return AdapterCapability(
            adapter_type=AdapterType.LOCAL,
            max_servers=100,
            supports_parallel=True,
            supports_gpu=False,
            supports_npu=False,
            supports_distributed=False,
            priority=10,
            description=f"本地CPU适配器 ({cores}核心)"
        )

    def is_available(self) -> bool:
        return self._available

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        try:
            self._config = config or {}
            cores = self._config.get('parallel_workers', psutil.cpu_count(logical=False) or 4)
            self._num_workers = min(cores, 32)
            self._initialized = True
            logger.info(f"LocalAdapter initialized with {self._num_workers} workers")
            return True
        except Exception as e:
            logger.error(f"LocalAdapter init failed: {e}")
            return False

    def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            from src.plugin import PluginManager
            from src.plugin.simulation_plugin import SimulationPlugin
            
            pm = PluginManager()
            pm.register_plugin(SimulationPlugin)
            
            config = {
                'num_servers': kwargs.get('num_servers', 10),
                'simulation_hours': kwargs.get('simulation_hours', 24),
                'parallel_workers': self._num_workers,
                'run_mode': kwargs.get('run_mode', 'standard')
            }
            
            pm.load_plugin('SimulationPlugin', config)
            result = pm.execute_plugin('SimulationPlugin',
                algorithms=kwargs.get('algorithms', ['greedy', 'genetic', 'round_robin']),
                visualization=kwargs.get('visualization', True),
                save_results=kwargs.get('save_results', True)
            )
            pm.unload_plugin('SimulationPlugin')

            return normalise_result(result)
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_status(self) -> Dict[str, Any]:
        return {
            'type': 'local',
            'available': self._available,
            'initialized': self._initialized,
            'workers': self._num_workers,
            'os': platform.system(),
            'cpu_count': psutil.cpu_count()
        }

    def cleanup(self) -> None:
        self._initialized = False
        logger.info("LocalAdapter cleaned up")

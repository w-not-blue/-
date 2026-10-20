from typing import Dict, Any, Optional
from src.adapters.base import BaseAdapter, AdapterType, AdapterCapability, normalise_result
from src.utils.logger import setup_logger

logger = setup_logger("remote_adapter")

class RemoteAdapter(BaseAdapter):
    """
    远程服务适配器
    
    适用于:
    - 分布式部署
    - 云端调用
    - 跨语言客户端
    
    参考项目: Flask-RESTful, FastAPI, gRPC
    """

    def __init__(self):
        self._available = True
        self._initialized = False
        self._api_url = "http://localhost:5000"
        self._session = None

    def get_type(self) -> AdapterType:
        return AdapterType.REMOTE

    def get_capability(self) -> AdapterCapability:
        return AdapterCapability(
            adapter_type=AdapterType.REMOTE,
            max_servers=1000,
            supports_parallel=True,
            supports_gpu=True,
            supports_npu=True,
            supports_distributed=True,
            priority=20,
            description="远程服务适配器 (REST API)"
        )

    def is_available(self) -> bool:
        if not self._available:
            return False
        try:
            import requests
            return True
        except ImportError:
            logger.warning("requests not installed, RemoteAdapter unavailable")
            return False

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        try:
            self._config = config or {}
            self._api_url = self._config.get('api_url', 'http://localhost:5000').rstrip('/')
            
            import requests
            from urllib3.util.retry import Retry
            from requests.adapters import HTTPAdapter
            
            session = requests.Session()
            retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
            adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
            session.mount('http://', adapter)
            session.mount('https://', adapter)
            
            self._session = session
            self._initialized = True
            logger.info(f"RemoteAdapter initialized: {self._api_url}")
            return True
        except Exception as e:
            logger.error(f"RemoteAdapter init failed: {e}")
            return False

    def execute(self, **kwargs) -> Dict[str, Any]:
        try:
            url = f"{self._api_url}/api/v1/simulation"
            payload = {
                'num_servers': kwargs.get('num_servers', 10),
                'simulation_hours': kwargs.get('simulation_hours', 24),
                'parallel_workers': kwargs.get('parallel_workers', 4),
                'run_mode': kwargs.get('run_mode', 'standard'),
                'algorithms': kwargs.get('algorithms', ['greedy', 'genetic', 'round_robin'])
            }
            
            response = self._session.post(url, json=payload, timeout=300)
            response.raise_for_status()
            return normalise_result(response.json())
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def get_status(self) -> Dict[str, Any]:
        online = False
        if self._session:
            try:
                resp = self._session.get(f"{self._api_url}/health", timeout=5)
                online = resp.status_code == 200
            except Exception:
                pass
        
        return {
            'type': 'remote',
            'available': self._available,
            'initialized': self._initialized,
            'api_url': self._api_url,
            'online': online
        }

    def cleanup(self) -> None:
        if self._session:
            self._session.close()
        self._initialized = False
        logger.info("RemoteAdapter cleaned up")

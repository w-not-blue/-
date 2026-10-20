import yaml
import platform
import psutil
import os
from pathlib import Path
from typing import Any, Dict, Optional, List

class Config:
    """支持环境感知的配置管理器"""

    _instance: Optional['Config'] = None

    def __new__(cls, config_path: Optional[str] = None, env: Optional[str] = None) -> 'Config':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def __init__(self, config_path: Optional[str] = None, env: Optional[str] = None):
        if not self._loaded:
            self._load(config_path, env)

    def _load(self, config_path: Optional[str], env: Optional[str]) -> None:
        env_config: Optional[Path] = None

        if config_path is None:
            config_dir = Path(__file__).parent.parent.parent / "config"
            base_config = config_dir / "default.yaml"

            env = env or os.environ.get('DCS_ENV', 'development')
            env_config = config_dir / f"{env}.yaml"
        else:
            base_config = Path(config_path)
            env = env or os.environ.get('DCS_ENV')
            if env:
                env_config = base_config.parent / f"{env}.yaml"

        with open(base_config, 'r', encoding='utf-8') as f:
            loaded = yaml.safe_load(f)
        self._data: Dict[str, Any] = loaded if isinstance(loaded, dict) else {}

        if env_config is not None:
            try:
                with open(env_config, 'r', encoding='utf-8') as f:
                    env_data = yaml.safe_load(f)
            except FileNotFoundError:
                env_data = None
            if isinstance(env_data, dict):
                self._deep_merge(self._data, env_data)

        self._apply_environment_overrides()
        self._detected_env = self._detect_runtime_env()
        self._loaded = True

    def _deep_merge(self, base: dict, override: dict) -> None:
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _apply_environment_overrides(self) -> None:
        env_map = {
            'DCS_NUM_SERVERS': ('simulation', 'num_servers', int),
            'DCS_SIM_HOURS': ('simulation', 'simulation_hours', int),
            'DCS_PARALLEL_WORKERS': ('simulation', 'parallel_workers', int),
            'DCS_DATA_DIR': ('output', 'data_dir', str),
            'DCS_REPORTS_DIR': ('output', 'reports_dir', str),
            'DCS_ENABLE_VIZ': ('output', 'enable_visualization', lambda x: x.lower() == 'true'),
            'DCS_POPULATION_SIZE': ('genetic', 'population_size', int),
            'DCS_GENERATIONS': ('genetic', 'generations', int),
            'DCS_AMBIENT_TEMP': ('environment', 'ambient_temp_base', float),
            'DCS_API_URL': ('api', 'api_url', str),
        }

        for env_var, (section, key, converter) in env_map.items():
            value = os.environ.get(env_var)
            if value is not None:
                try:
                    if section not in self._data:
                        self._data[section] = {}
                    self._data[section][key] = converter(value)
                except (ValueError, TypeError):
                    pass

    def _detect_runtime_env(self) -> Dict[str, Any]:
        info = {
            'os': platform.system(),
            'os_release': platform.release(),
            'arch': platform.machine(),
            'python_version': platform.python_version(),
            'is_docker': os.path.exists('/.dockerversion'),
            'is_k8s': bool(os.environ.get('KUBERNETES_SERVICE_HOST')),
        }

        try:
            info['cpu_physical'] = psutil.cpu_count(logical=False)
            info['cpu_logical'] = psutil.cpu_count(logical=True)
        except Exception:
            info['cpu_physical'] = 1
            info['cpu_logical'] = 1

        try:
            mem = psutil.virtual_memory()
            info['total_memory_gb'] = round(mem.total / (1024 ** 3), 1)
            info['available_memory_gb'] = round(mem.available / (1024 ** 3), 1)
        except Exception:
            info['total_memory_gb'] = 0

        try:
            import torch
            info['has_cuda'] = torch.cuda.is_available()
            info['cuda_count'] = torch.cuda.device_count() if info['has_cuda'] else 0
        except ImportError:
            info['has_cuda'] = False
            info['cuda_count'] = 0

        try:
            import torch_npu
            info['has_ascend'] = torch_npu.npu.is_available()
        except ImportError:
            info['has_ascend'] = os.path.exists('/usr/local/Ascend')

        return info

    def get_runtime_info(self) -> Dict[str, Any]:
        return self._detected_env

    def get_optimal_workers(self) -> int:
        cores = self._detected_env.get('cpu_physical', 4)
        if cores >= 16:
            return 8
        elif cores >= 8:
            return 4
        elif cores >= 4:
            return 2
        return 1

    def __getattr__(self, key: str) -> Any:
        if key.startswith('_'):
            raise AttributeError(key)
        data = self.__dict__.get('_data')
        if not isinstance(data, dict) or key not in data:
            raise AttributeError(key)
        val = data[key]
        if isinstance(val, dict):
            class SectionAccessor:
                def __init__(self, data):
                    self._data = data

                def __getattr__(self, k):
                    return self._data.get(k)

                def get(self, k, default=None):
                    return self._data.get(k, default)

                def __contains__(self, k):
                    return k in self._data

                def __repr__(self):
                    return str(self._data)

            return SectionAccessor(val)
        return val

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def to_dict(self) -> Dict[str, Any]:
        return self._data.copy()

    def reload(self, config_path: Optional[str] = None, env: Optional[str] = None) -> None:
        self._loaded = False
        Config._instance = None
        self._load(config_path, env)

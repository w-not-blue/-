from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

def normalise_result(value):
    """Convert any adapter return value into a flat dict.

    Adapters used to disagree: some returned ``PluginResult`` objects, some a
    raw API envelope with the business payload nested under ``data``, and some
    a plain dict. Callers then had to guess, which is how the CLI ended up
    reporting "records: 0" for a successful run.
    """
    if hasattr(value, 'success') and hasattr(value, 'data'):
        payload = dict(value.data) if isinstance(value.data, dict) else {}
        payload['success'] = bool(value.success)
        if value.message:
            payload.setdefault('message', value.message)
        if value.error_code:
            payload.setdefault('error_code', value.error_code)
        if value.warnings:
            payload.setdefault('warnings', list(value.warnings))
        if not payload['success'] and 'error' not in payload:
            payload['error'] = value.message or value.error_code or '未知错误'
        return payload

    if isinstance(value, dict):
        inner = value.get('data')
        if isinstance(inner, dict) and 'statistics' not in value:
            merged = dict(inner)
            merged.setdefault('success', value.get('success', True))
            for key in ('message', 'warnings', 'error_code', 'execution_time_ms', 'metrics', 'timestamp'):
                if key in value and key not in merged:
                    merged[key] = value[key]
            return merged
        return value

    return {'success': False, 'error': '返回类型无法识别: {0}'.format(type(value).__name__)}


class AdapterType(Enum):
    LOCAL = "local"
    REMOTE = "remote"
    CUDA = "cuda"
    ASCEND = "ascend"
    AUTO = "auto"

@dataclass
class AdapterCapability:
    adapter_type: AdapterType
    max_servers: int = 100
    supports_parallel: bool = False
    supports_gpu: bool = False
    supports_npu: bool = False
    supports_distributed: bool = False
    priority: int = 0
    description: str = ""

class BaseAdapter(ABC):
    """
    适配器基类
    
    所有平台/硬件适配器必须实现此接口。
    参考项目: stevedore (OpenStack), NVIDIA Triton Inference Server
    """

    @abstractmethod
    def get_type(self) -> AdapterType:
        pass

    @abstractmethod
    def get_capability(self) -> AdapterCapability:
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """检测适配器是否在当前环境中可用"""
        pass

    @abstractmethod
    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """初始化适配器"""
        pass

    @abstractmethod
    def execute(self, **kwargs) -> Dict[str, Any]:
        """执行主逻辑"""
        pass

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """获取适配器状态"""
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """清理资源"""
        pass

    def validate_config(self, config: Dict[str, Any]) -> Tuple[bool, str]:
        """验证配置参数"""
        return True, "OK"

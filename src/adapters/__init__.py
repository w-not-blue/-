"""
适配器层 - 提供多平台、多硬件的自适应能力

设计模式: 策略模式 + 适配器模式
参考项目: stevedore (OpenStack), Flask-PluginKit

支持的适配器:
    LocalAdapter     - 本地CPU计算
    RemoteAdapter    - 远程服务调用
    CudaAdapter      - NVIDIA CUDA GPU加速 (可选)
    AscendAdapter    - 华为昇腾NPU加速/国产硬件 (可选)
    AutoAdapter      - 自动检测并选择最优适配器

环境变量配置:
    DCS_ENV             - 运行环境 (development/testing/production)
    DCS_FORCE_ADAPTER   - 强制使用指定适配器
    DCS_API_URL         - 远程API地址
    DCS_NUM_SERVERS     - 服务器数量
    DCS_SIM_HOURS       - 仿真时长
    DCS_PARALLEL_WORKERS - 并行工作器数
"""

from .base import BaseAdapter, AdapterType, AdapterCapability
from .local import LocalAdapter
from .remote import RemoteAdapter
from .auto import AutoAdapter

try:
    from .cuda import CudaAdapter
except ImportError:
    CudaAdapter = None

try:
    from .ascend import AscendAdapter
except ImportError:
    AscendAdapter = None

__all__ = [
    'BaseAdapter',
    'LocalAdapter',
    'RemoteAdapter',
    'CudaAdapter',
    'AscendAdapter',
    'AutoAdapter',
    'AdapterType',
    'AdapterCapability'
]

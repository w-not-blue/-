"""
插件系统 v2.0

提供标准化的插件接口和统一调用机制，支持:
- 插件注册/发现/加载/卸载/执行
- 事件总线 (插件间通信)
- 依赖解析
- 热重载
- 状态监控与指标收集

快速使用:
    from src.plugin import PluginManager, PluginBase, PluginResult

    pm = PluginManager()
    pm.register_plugin(MyPlugin)
    pm.load_plugin("MyPlugin", config={"key": "value"})
    result = pm.execute_plugin("MyPlugin", arg1=123)
    print(result.to_json())
    pm.unload_plugin("MyPlugin")
"""

from .protocol import (
    PluginResult, PluginInfo, PluginManifest, PluginStatus,
    PluginPriority, PluginEvent, PluginCallback, PluginConfig
)
from .base import PluginBase
from .manager import PluginManager

__all__ = [
    'PluginBase',
    'PluginManager',
    'PluginResult',
    'PluginInfo',
    'PluginManifest',
    'PluginStatus',
    'PluginPriority',
    'PluginEvent',
    'PluginCallback',
    'PluginConfig'
]

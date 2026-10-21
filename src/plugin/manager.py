"""
插件管理器 v2.0 — 标准化统一调用机制

核心职责:
    - 插件注册/发现/加载/卸载 (生命周期管理)
    - 统一执行入口 (基于 _execute_wrapper 的标准流程)
    - 依赖解析与拓扑排序加载
    - 事件总线 (插件间通信)
    - 并发执行控制 (线程安全)
    - 状态监控与指标收集
    - 热重载支持

线程安全:
    使用 threading.RLock 保护所有共享状态。

第三方应用调用示例:

    pm = PluginManager()

    # 1. 注册插件
    pm.register_plugin(SimulationPlugin, priority=PluginPriority.HIGH)

    # 2. 加载插件
    pm.load_plugin("SimulationPlugin", config={"num_servers": 20})

    # 3. 执行插件 (统一入口)
    result = pm.execute_plugin("SimulationPlugin", algorithms=["greedy", "genetic"])

    # 4. 检查结果
    if result.success:
        print(f"完成: {result.message}")
        print(f"数据: {result.data}")
    else:
        print(f"失败 [{result.error_code}]: {result.message}")

    # 5. 卸载
    pm.unload_plugin("SimulationPlugin")
"""

import importlib
import os
import threading
import time
from typing import Dict, List, Type, Optional, Set
from datetime import datetime

from src.plugin.base import PluginBase
from src.plugin.protocol import (
    PluginResult, PluginInfo, PluginManifest, PluginStatus,
    PluginPriority, PluginEvent, PluginCallback, PluginConfig
)
from src.utils.logger import setup_logger

# Fail-closed list: modules that stay on disk but are not discovered.
# rl_ppo_scheduler needs gymnasium + stable-baselines3 + a trained model.
DISABLED_PLUGIN_MODULES = {"rl_ppo_scheduler"}


class PluginManager:
    """
    插件管理器

    状态管理:
        每个插件通过 _plugin_instances 和 _plugin_infos 追踪全生命周期。

    事件系统:
        通过 subscribe / unsubscribe / _emit_global 实现全局事件总线，
        插件间可通过事件解耦通信。
    """

    def __init__(self):
        self._logger = setup_logger("plugin_manager")
        self._lock = threading.RLock()
        self._plugin_classes: Dict[str, Type[PluginBase]] = {}
        self._plugin_instances: Dict[str, PluginBase] = {}
        self._plugin_infos: Dict[str, PluginInfo] = {}
        self._plugin_priorities: Dict[str, PluginPriority] = {}
        self._global_callbacks: Dict[str, List[PluginCallback]] = {}
        self._metrics: Dict[str, Dict] = {}
        self._started_at: str = datetime.now().isoformat()

    # =========================================================================
    # 插件注册
    # =========================================================================

    def register_plugin(self, plugin_class: Type[PluginBase],
                        priority: PluginPriority = PluginPriority.NORMAL,
                        dependencies: Optional[List[str]] = None) -> bool:
        """
        注册一个插件类到管理器

        Args:
            plugin_class: 继承自 PluginBase 的插件类
            priority: 执行优先级 (默认 NORMAL)
            dependencies: 依赖的其他插件名称列表

        Returns:
            True 注册成功

        Raises:
            TypeError: 如果 plugin_class 不是 PluginBase 的子类
        """
        if not issubclass(plugin_class, PluginBase):
            raise TypeError(f"{plugin_class.__name__} 必须继承 PluginBase")

        manifest = plugin_class().get_manifest()
        name = manifest.name

        with self._lock:
            self._plugin_classes[name] = plugin_class
            self._plugin_priorities[name] = priority

            info = PluginInfo(
                name=name,
                version=manifest.version,
                description=",".join(manifest.supported_operations),
                status=PluginStatus.REGISTERED,
                priority=priority,
                dependencies=dependencies or [],
                config_schema=manifest.input_schema,
                tags=manifest.supported_operations,
                registered_at=datetime.now().isoformat()
            )
            self._plugin_infos[name] = info

        self._logger.info(f"插件已注册: {name} v{manifest.version} (优先级: {priority.name})")
        self._emit_global("PLUGIN_REGISTERED", {"name": name, "version": manifest.version})
        return True

    # =========================================================================
    # 插件加载
    # =========================================================================

    def load_plugin(self, name: str, config: Optional[PluginConfig] = None) -> bool:
        """
        加载并初始化指定插件

        流程:
            1. 检查是否已注册
            2. 解析并加载依赖插件
            3. 验证配置参数
            4. 实例化插件类
            5. 调用 initialize() 初始化

        Args:
            name: 插件名称
            config: 配置参数字典

        Returns:
            True 加载成功
        """
        with self._lock:
            if name not in self._plugin_classes:
                self._logger.error(f"加载失败: 插件 '{name}' 未注册")
                return False

            if name in self._plugin_instances:
                self._logger.warning(f"插件 '{name}' 已加载，跳过重复加载")
                return True

            info = self._plugin_infos[name]

            # 解析依赖
            for dep_name in info.dependencies:
                if dep_name not in self._plugin_instances:
                    self._logger.info(f"加载依赖插件: {dep_name} → {name}")
                    if not self.load_plugin(dep_name):
                        self._logger.error(f"依赖插件加载失败: {dep_name}")
                        return False

            # 实例化
            try:
                plugin_class = self._plugin_classes[name]
                plugin = plugin_class()
                info.status = PluginStatus.INITIALIZING
                self._plugin_infos[name] = info
            except Exception as e:
                return self._handle_load_error(name, e, "实例化", info)

            # 配置验证
            config = config or {}
            if not plugin.validate_config(config):
                self._logger.error(f"加载失败: 插件 '{name}' 配置验证不通过")
                info.status = PluginStatus.ERROR
                self._plugin_infos[name] = info
                return False
            plugin._config = config

            # 初始化
            try:
                if not plugin.initialize(config):
                    info.status = PluginStatus.ERROR
                    self._plugin_infos[name] = info
                    self._logger.error(f"初始化失败: 插件 '{name}'")
                    return False
            except Exception as e:
                return self._handle_load_error(name, e, "初始化", info)

            plugin._status = PluginStatus.READY
            plugin._loaded_at = datetime.now().isoformat()
            self._plugin_instances[name] = plugin
            info.status = PluginStatus.READY
            info.last_loaded_at = datetime.now().isoformat()
            self._plugin_infos[name] = info

            self._logger.info(f"插件已加载: {name} (状态: READY)")
            self._emit_global("PLUGIN_INITIALIZED", {"name": name})
            return True

    def _handle_load_error(self, name: str, error: Exception,
                           stage: str, info: PluginInfo) -> bool:
        info.status = PluginStatus.ERROR
        self._plugin_infos[name] = info
        self._logger.error(f"{stage}失败 [{name}]: {error}", exc_info=True)
        self._emit_global("PLUGIN_ERROR", {"name": name, "stage": stage, "error": str(error)})
        return False

    # =========================================================================
    # 插件执行 (统一入口)
    # =========================================================================

    def execute_plugin(self, name: str, **kwargs) -> Optional[PluginResult]:
        """
        执行插件 — 统一的第三方调用入口

        使用 _execute_wrapper 确保一致的执行流程:
            pre_execute → execute → post_execute → 计时 → 指标收集

        Args:
            name: 插件名称
            **kwargs: 传递给 execute() 的参数

        Returns:
            PluginResult: 标准化执行结果

        用法:
            result = pm.execute_plugin("SimulationPlugin", algorithms=["greedy"])
            if result.success:
                print(result.data['statistics'])
        """
        with self._lock:
            if name not in self._plugin_instances:
                self._logger.error(f"执行失败: 插件 '{name}' 未加载")
                return PluginResult.fail(
                    message=f"插件 '{name}' 未加载",
                    error_code="PLUGIN_NOT_LOADED"
                )

            plugin = self._plugin_instances[name]

        result = plugin._execute_wrapper(**kwargs)

        plugin_info = self._plugin_infos.get(name)
        if plugin_info:
            plugin_info.execution_count = plugin._execution_count
            plugin_info.last_executed_at = datetime.now().isoformat()

        self._logger.info(
            f"插件执行完成: {name} "
            f"({'成功' if result.success else '失败'}) "
            f"[{result.execution_time_ms:.0f}ms]"
        )
        self._emit_global("METRICS_UPDATED", {"name": name, "metrics": plugin.get_metrics()})
        return result

    # =========================================================================
    # 插件卸载
    # =========================================================================

    def unload_plugin(self, name: str) -> bool:
        """
        卸载指定插件并释放资源

        Args:
            name: 插件名称

        Returns:
            True 卸载成功
        """
        with self._lock:
            if name not in self._plugin_instances:
                self._logger.warning(f"卸载跳过: 插件 '{name}' 未加载")
                return False

            plugin = self._plugin_instances[name]
            try:
                plugin._set_status(PluginStatus.STOPPING)
                plugin.cleanup()
                plugin._set_status(PluginStatus.STOPPED)
            except Exception as e:
                self._logger.error(f"清理异常 [{name}]: {e}")

            del self._plugin_instances[name]
            if name in self._plugin_infos:
                self._plugin_infos[name].status = PluginStatus.STOPPED

            self._logger.info(f"插件已卸载: {name}")
            self._emit_global("PLUGIN_STOPPED", {"name": name})
            return True

    # =========================================================================
    # 插件查询
    # =========================================================================

    def get_plugin(self, name: str) -> Optional[PluginBase]:
        """获取已加载的插件实例"""
        with self._lock:
            return self._plugin_instances.get(name)

    def get_plugin_info(self, name: str) -> Optional[PluginInfo]:
        """获取插件注册信息"""
        with self._lock:
            return self._plugin_infos.get(name)

    def list_plugins(self) -> List[Dict]:
        """列出所有已注册插件的元信息"""
        with self._lock:
            result = []
            for name, info in self._plugin_infos.items():
                d = info.to_dict()
                d["loaded"] = name in self._plugin_instances
                d["priority"] = self._plugin_priorities.get(name, PluginPriority.NORMAL).name
                result.append(d)
            return result

    def list_loaded_plugins(self) -> List[str]:
        """列出所有已加载插件的名称"""
        with self._lock:
            return list(self._plugin_instances.keys())

    def get_plugin_metrics(self, name: str) -> Optional[Dict]:
        """获取插件运行指标"""
        plugin = self.get_plugin(name)
        if plugin:
            return plugin.get_metrics()
        return None

    def get_all_metrics(self) -> Dict[str, Dict]:
        """获取所有插件的运行指标"""
        with self._lock:
            return {name: p.get_metrics() for name, p in self._plugin_instances.items()}

    # =========================================================================
    # 插件配置管理
    # =========================================================================

    def get_plugin_config(self, name: str) -> Optional[PluginConfig]:
        """获取插件当前配置"""
        plugin = self.get_plugin(name)
        return plugin.get_config() if plugin else None

    def update_plugin_config(self, name: str, config_update: PluginConfig) -> bool:
        """
        更新插件配置 (需重新加载生效)

        Args:
            name: 插件名称
            config_update: 要更新的配置 (合并到现有配置)

        Returns:
            True 更新成功
        """
        with self._lock:
            if name not in self._plugin_infos:
                return False
            plugin = self._plugin_instances.get(name)
            if plugin:
                merged = dict(plugin._config)
                merged.update(config_update)
                plugin._config = merged
                if not plugin.validate_config(merged):
                    self._logger.warning(f"配置更新后验证失败: {name}")
                    return False
            self._logger.info(f"配置已更新: {name}")
            return True

    # =========================================================================
    # 热重载
    # =========================================================================

    def reload_plugin(self, name: str) -> bool:
        """
        热重载插件 (卸载后重新加载，保留配置)

        Args:
            name: 插件名称

        Returns:
            True 重载成功
        """
        with self._lock:
            config = self.get_plugin_config(name)
            self.unload_plugin(name)
            time.sleep(0.1)
            return self.load_plugin(name, config)

    # =========================================================================
    # 批量操作
    # =========================================================================

    def load_all(self, configs: Optional[Dict[str, PluginConfig]] = None) -> Dict[str, bool]:
        """
        加载所有已注册的插件

        Args:
            configs: 各插件的配置字典 {name: config}

        Returns:
            {name: success} 加载结果映射
        """
        results = {}
        with self._lock:
            names = list(self._plugin_classes.keys())
        for name in names:
            cfg = (configs or {}).get(name)
            results[name] = self.load_plugin(name, cfg)
        return results

    def unload_all(self) -> None:
        """卸载所有已加载插件"""
        with self._lock:
            names = list(self._plugin_instances.keys())
        for name in reversed(names):
            self.unload_plugin(name)

    def execute_all(self, **kwargs) -> Dict[str, PluginResult]:
        """
        按优先级顺序执行所有已加载插件

        Args:
            **kwargs: 传递给各插件的公共参数

        Returns:
            {name: PluginResult} 执行结果映射
        """
        results = {}
        with self._lock:
            items = [(name, self._plugin_priorities.get(name, PluginPriority.NORMAL).value)
                     for name in self._plugin_instances.keys()]
        items.sort(key=lambda x: x[1])

        for name, _ in items:
            results[name] = self.execute_plugin(name, **kwargs)
        return results

    # =========================================================================
    # 事件总线
    # =========================================================================

    def subscribe(self, event_type: str, callback: PluginCallback) -> None:
        """
        订阅全局事件

        Args:
            event_type: 事件类型
            callback: 回调函数 (PluginEvent) → None

        用法:
            def on_completed(event):
                print(f"{event.plugin_name} 执行完成: {event.result.message}")

            pm.subscribe("PLUGIN_COMPLETED", on_completed)
        """
        with self._lock:
            if event_type not in self._global_callbacks:
                self._global_callbacks[event_type] = []
            self._global_callbacks[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: PluginCallback) -> None:
        """取消事件订阅"""
        with self._lock:
            if event_type in self._global_callbacks:
                self._global_callbacks[event_type] = [
                    cb for cb in self._global_callbacks[event_type] if cb != callback
                ]

    def _emit_global(self, event_type: str, data: Optional[Dict] = None) -> None:
        """触发全局事件"""
        event = PluginEvent(
            event_type=event_type,
            plugin_name="PluginManager",
            data=data
        )
        callbacks = self._global_callbacks.get(event_type, [])
        for cb in callbacks:
            try:
                cb(event)
            except Exception as e:
                self._logger.warning(f"全局事件回调异常 [{event_type}]: {e}")

    # =========================================================================
    # 自动发现
    # =========================================================================

    def discover_plugins(self, plugin_dir: str = "plugins") -> int:
        """
        自动发现并注册指定目录中的插件模块

        Args:
            plugin_dir: 插件目录名（相对于项目根目录），支持两种形式:
                - 文件路径: "plugins" / "src/plugins" / "./src/plugins"
                - Python 包: "src.plugins"
                内部统一转成文件系统路径与 Python 模块前缀。

        Returns:
            int: 成功注册的插件数量
        """
        # 归一化: 去掉 ./ 前缀与尾部斜杠
        norm = plugin_dir.replace("\\", "/").strip("/")
        if norm.startswith("./"):
            norm = norm[2:]

        # 推断: 含 '.' 且首段不是盘符 → 当作 Python 包; 否则文件路径
        is_python_package = (
            "." in norm
            and not (len(norm) >= 2 and norm[1] == ":")
        )
        if is_python_package:
            module_prefix = norm
            file_subpath = norm.replace(".", os.sep)
        else:
            file_subpath = norm
            module_prefix = norm.replace("/", ".").replace(os.sep, ".")

        plugin_path = os.path.join(os.path.dirname(__file__), "..", "..", file_subpath)
        if not os.path.exists(plugin_path):
            self._logger.warning(f"插件目录不存在: {plugin_path}")
            return 0

        count = 0
        disabled = set(getattr(self, "disabled_plugins", DISABLED_PLUGIN_MODULES))
        for filename in sorted(os.listdir(plugin_path)):
            if not filename.endswith(".py") or filename.startswith("_"):
                continue
            module_name = filename[:-3]
            if module_name in disabled:
                self._logger.info(f"插件已禁用，跳过: {module_name}")
                continue
            try:
                module = importlib.import_module(f"{module_prefix}.{module_name}")
                for attr_name in dir(module):
                    obj = getattr(module, attr_name)
                    if (isinstance(obj, type) and issubclass(obj, PluginBase)
                            and obj != PluginBase
                            and obj.__module__ == module.__name__):
                        self.register_plugin(obj)
                        count += 1
            except Exception as e:
                self._logger.error(f"模块加载失败 {module_name}: {e}")

        self._logger.info(f"插件发现完成: {count} 个插件已注册 (目录: {plugin_path})")
        return count

    # =========================================================================
    # 依赖解析
    # =========================================================================

    def resolve_dependencies(self, name: str) -> List[str]:
        """
        解析插件的完整依赖链 (拓扑排序)

        Args:
            name: 插件名称

        Returns:
            List[str]: 按依赖顺序排列的插件名称列表（被依赖的在前）
        """
        resolved: List[str] = []
        visited: Set[str] = set()
        visiting: Set[str] = set()

        def dfs(current: str):
            if current in visited:
                return
            if current in visiting:
                raise RuntimeError(f"检测到循环依赖: {current}")
            visiting.add(current)
            info = self._plugin_infos.get(current)
            if info:
                for dep in info.dependencies:
                    dfs(dep)
            visiting.discard(current)
            visited.add(current)
            if current != name:
                resolved.append(current)

        try:
            dfs(name)
        except RuntimeError as e:
            self._logger.error(str(e))
            return []

        resolved.append(name)
        return resolved

    # =========================================================================
    # 状态查询
    # =========================================================================

    def get_system_status(self) -> Dict:
        """
        获取整个插件系统的运行状态

        Returns:
            dict: 包含已注册/已加载/错误插件数量及整体健康状态
        """
        with self._lock:
            total = len(self._plugin_infos)
            loaded = len(self._plugin_instances)
            errors = sum(1 for info in self._plugin_infos.values()
                         if info.status == PluginStatus.ERROR)
            healthy = (errors == 0)

            return {
                "total_registered": total,
                "total_loaded": loaded,
                "status": "healthy" if healthy else "degraded",
                "error_count": errors,
                "started_at": self._started_at,
                "uptime_seconds": round(
                    (datetime.now() - datetime.fromisoformat(self._started_at)).total_seconds(), 1
                ),
                "plugins": self.list_plugins()
            }

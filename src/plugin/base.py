"""
插件抽象基类 v2.0 — 标准化接口规范

所有插件必须继承此基类并实现其抽象方法。

核心生命周期:
    __init__ → initialize(config) → [pre_execute → execute → post_execute]×N → cleanup

增强功能 (新增):
    - PluginResult 统一返回类型
    - 执行前后钩子 (pre_execute / post_execute)
    - 错误处理钩子 (on_error)
    - 事件回调注册系统
    - 配置验证 (validate_config)
    - 运行状态查询 (get_status)
    - 插件清单 (get_manifest) 供第三方发现

用法示例:

    class MyPlugin(PluginBase):
        def get_manifest(self):
            return PluginManifest(
                name="MyPlugin",
                version="1.0.0",
                supported_operations=["process", "analyze"],
                input_schema={"data": "list"},
                output_schema={"result": "dict"}
            )

        def initialize(self, config):
            self.threshold = config.get("threshold", 0.5)
            return True

        def execute(self, **kwargs):
            data = kwargs.get("data", [])
            processed = [x for x in data if x > self.threshold]
            return PluginResult.ok(data=processed, message="处理完成")
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import time
from datetime import datetime

from src.plugin.protocol import (
    PluginResult, PluginInfo, PluginManifest, PluginStatus,
    PluginPriority, PluginEvent, PluginCallback, PluginConfig,
    PluginContext
)
from src.utils.logger import setup_logger


class PluginBase(ABC):
    """
    插件抽象基类

    接口契约:
        必须实现: get_manifest, initialize, execute, cleanup
        可选覆盖: pre_execute, post_execute, on_error, validate_config

    所有插件的 execute() 必须返回 PluginResult 实例。
    """

    def __init__(self):
        self.logger = setup_logger(self.__class__.__name__)
        self._status = PluginStatus.REGISTERED
        self._context: PluginContext = {}
        self._config: PluginConfig = {}
        self._callbacks: Dict[str, List[PluginCallback]] = {}
        self._execution_count: int = 0
        self._total_execution_time_ms: float = 0.0
        self._last_error: Optional[str] = None
        self._loaded_at: Optional[str] = None

    # =========================================================================
    # 必须实现的抽象方法
    # =========================================================================

    @abstractmethod
    def get_manifest(self) -> PluginManifest:
        """
        返回插件清单 — 描述插件的能力、接口和兼容性

        第三方应用通过此清单:
        - 发现可调用的操作
        - 了解输入参数格式
        - 确认版本兼容性

        Returns:
            PluginManifest: 包含名称/版本/操作列表/参数schema的完整清单
        """
        pass

    @abstractmethod
    def initialize(self, config: Optional[PluginConfig] = None) -> bool:
        """
        初始化插件 — 分配资源、验证配置、建立连接

        调用时机: 插件加载时由 PluginManager 调用
        调用频率: 每次 load_plugin 调用一次

        Args:
            config: 配置字典，包含插件运行所需参数
                    传入 None 表示使用默认配置

        Returns:
            bool: True 表示初始化成功，False 表示失败

        最佳实践:
        - 在此方法中进行资源分配（文件、网络、数据库连接）
        - 验证 config 中的关键参数
        - 失败时记录详细日志
        """
        pass

    @abstractmethod
    def execute(self, **kwargs) -> PluginResult:
        """
        执行插件核心业务逻辑

        调用时机: 插件加载后，由 PluginManager.execute_plugin() 调用
        调用频率: 可多次调用

        Args:
            **kwargs: 可变关键字参数，由调用方传入

        Returns:
            PluginResult: 统一返回格式，包含:
                - success: 执行是否成功
                - data: 业务产出数据
                - message: 状态描述
                - error_code: 错误码

        注意:
        - **绝对不要**抛出未捕获异常
        - 所有错误通过 PluginResult.fail() 返回
        - 构造函数中已分配 logger，使用 self.logger 记录日志
        """
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """
        清理插件资源 — 释放所有分配的资源

        调用时机: 插件卸载时由 PluginManager 调用
        调用频率: 一次

        要求:
        - 幂等性: 多次调用不会出错
        - 防御性: 即使资源未初始化也不会崩溃
        - 彻底性: 释放所有数据库连接/文件句柄/线程池等
        """
        pass

    # =========================================================================
    # 可选覆盖的钩子方法 (Hook Methods)
    # =========================================================================

    def validate_config(self, config: PluginConfig) -> bool:
        """
        验证配置参数是否符合插件要求

        可覆盖此方法实现自定义验证逻辑:
        - 检查必填参数是否存在
        - 验证参数取值范围
        - 确认参数类型正确

        Args:
            config: 待验证的配置字典

        Returns:
            True 表示配置有效，False 表示配置无效

        示例:
            def validate_config(self, config):
                if 'num_servers' not in config:
                    self.logger.error("缺少必填参数: num_servers")
                    return False
                if config['num_servers'] < 1 or config['num_servers'] > 1000:
                    self.logger.error("num_servers 必须在 [1, 1000] 范围内")
                    return False
                return True
        """
        return True

    def pre_execute(self, **kwargs) -> Optional[PluginResult]:
        """
        执行前钩子 — 在主逻辑执行前调用

        可用于:
        - 参数预处理和验证
        - 设置执行上下文
        - 记录执行开始日志

        Args:
            **kwargs: 传入 execute() 的相同参数

        Returns:
            None 表示继续执行
            PluginResult 表示短路跳过后续执行（如参数验证失败）
        """
        return None

    def post_execute(self, result: PluginResult, **kwargs) -> PluginResult:
        """
        执行后钩子 — 在主逻辑执行后调用

        可用于:
        - 结果后处理
        - 记录执行完成日志
        - 更新统计指标

        Args:
            result: execute() 返回的原始结果
            **kwargs: 传入 execute() 的相同参数

        Returns:
            PluginResult: 处理后的结果（可修改原结果或返回新结果）
        """
        return result

    def on_error(self, error: Exception, operation: str) -> PluginResult:
        """
        错误处理钩子 — 当操作发生异常时调用

        Args:
            error: 发生的异常对象
            operation: 发生异常的操作名称 (如 'initialize', 'execute', 'cleanup')

        Returns:
            PluginResult: 封装错误信息的失败结果

        默认实现:
        - 记录详细错误日志
        - 返回包含错误信息的 PluginResult.fail()
        """
        self._last_error = str(error)
        self.logger.error(f"[{operation}] 异常: {error}", exc_info=True)
        return PluginResult.fail(
            message=f"{operation} 执行异常",
            error_code=f"{operation.upper()}_ERROR",
            error_detail=str(error)
        )

    # =========================================================================
    # 事件回调系统
    # =========================================================================

    def on(self, event_type: str, callback: PluginCallback) -> None:
        """
        注册事件回调

        Args:
            event_type: 事件类型 (如 'STATE_CHANGED', 'METRICS_UPDATED')
            callback: 回调函数，接收 PluginEvent 参数
        """
        if event_type not in self._callbacks:
            self._callbacks[event_type] = []
        self._callbacks[event_type].append(callback)

    def off(self, event_type: str, callback: PluginCallback) -> None:
        """
        注销事件回调

        Args:
            event_type: 事件类型
            callback: 要注销的回调函数
        """
        if event_type in self._callbacks:
            self._callbacks[event_type] = [
                cb for cb in self._callbacks[event_type] if cb != callback
            ]

    def emit(self, event_type: str, data: Optional[Dict[str, Any]] = None,
             result: Optional[PluginResult] = None) -> None:
        """
        触发事件 — 通知所有注册的回调函数

        Args:
            event_type: 事件类型
            data: 随事件传递的附加数据
            result: 关联的 PluginResult（如果有）
        """
        event = PluginEvent(
            event_type=event_type,
            plugin_name=self.get_manifest().name,
            data=data,
            result=result
        )
        callbacks = self._callbacks.get(event_type, [])
        for cb in callbacks:
            try:
                cb(event)
            except Exception as e:
                self.logger.warning(f"事件回调异常 [{event_type}]: {e}")

    # =========================================================================
    # 状态查询方法
    # =========================================================================

    def get_status(self) -> PluginStatus:
        """获取插件当前运行状态"""
        return self._status

    def get_info(self) -> PluginInfo:
        """
        获取插件完整注册信息

        Returns:
            PluginInfo: 包含名称/版本/状态/统计等完整信息的结构体
        """
        manifest = self.get_manifest()
        return PluginInfo(
            name=manifest.name,
            version=manifest.version,
            description=manifest.supported_operations[0] if manifest.supported_operations else "",
            status=self._status,
            last_loaded_at=self._loaded_at,
            last_executed_at=datetime.now().isoformat() if self._execution_count > 0 else None,
            execution_count=self._execution_count
        )

    def get_config(self) -> PluginConfig:
        """获取当前生效的配置（只读）"""
        return dict(self._config)

    def get_metrics(self) -> Dict[str, Any]:
        """
        获取插件运行指标

        Returns:
            dict: 包含 total_executions, avg_execution_time_ms, last_error 等指标
        """
        avg_time = (self._total_execution_time_ms / self._execution_count
                    if self._execution_count > 0 else 0)
        return {
            "plugin_name": self.get_manifest().name,
            "status": self._status.name,
            "total_executions": self._execution_count,
            "avg_execution_time_ms": round(avg_time, 2),
            "total_execution_time_ms": round(self._total_execution_time_ms, 2),
            "last_error": self._last_error
        }

    # =========================================================================
    # 内部辅助方法
    # =========================================================================

    def _set_status(self, status: PluginStatus) -> None:
        """更新状态并发送状态变更事件"""
        old_status = self._status
        self._status = status
        self.emit("STATE_CHANGED", {
            "old_status": old_status.name,
            "new_status": status.name
        })

    def _execute_wrapper(self, **kwargs) -> PluginResult:
        """
        执行包装器 — 统一的执行流程

        流程:
            1. 检查状态 (必须 READY)
            2. 调用 pre_execute 钩子
            3. 如果 pre_execute 返回非 None，直接返回
            4. 调用 execute 并计时
            5. 调用 post_execute 钩子
            6. 返回最终结果
        """
        if self._status != PluginStatus.READY:
            return PluginResult.fail(
                message=f"插件未就绪 (当前状态: {self._status.name})",
                error_code="INVALID_STATE"
            )

        short_circuit = self.pre_execute(**kwargs)
        if short_circuit is not None:
            return short_circuit

        self._set_status(PluginStatus.RUNNING)
        self.emit("PLUGIN_STARTED", {"kwargs_keys": list(kwargs.keys())})

        start = time.perf_counter()
        result = PluginResult.fail("未执行")
        try:
            result = self.execute(**kwargs)
            if not isinstance(result, PluginResult):
                result = PluginResult.ok(data=result, message="执行完成")
        except Exception as e:
            result = self.on_error(e, "execute")
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            result.execution_time_ms = round(elapsed, 2)
            self._execution_count += 1
            self._total_execution_time_ms += elapsed
            self._set_status(PluginStatus.READY)

        result = self.post_execute(result, **kwargs)
        self.emit("PLUGIN_COMPLETED", result=result)
        return result

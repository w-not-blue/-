"""
插件系统标准化接口协议 v1.0
Plugin System Standardized Protocol & Data Types

本模块定义插件系统的核心数据类型和接口规范，确保:
- 第三方应用通过统一的数据结构调用插件功能
- 所有插件返回一致格式的结果
- 插件状态可被外部系统查询和监控
- 支持异步事件通知机制

设计原则:
- 不可变数据类 (dataclass + frozen)
- 类型安全 (所有字段有明确类型注解)
- 可序列化 (支持 JSON 序列化)
- 向前兼容 (扩展字段使用 Optional)
"""

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, Any, List, Optional, Callable, Union
from datetime import datetime
import json


class PluginStatus(Enum):
    """
    插件生命周期状态机

    状态流转:
        UNREGISTERED → REGISTERED → INITIALIZING → READY
            READY → RUNNING → COMPLETED → READY
            READY → PAUSED → READY
            READY → STOPPING → STOPPED
            任意状态 → ERROR

    UNREGISTERED: 插件未被管理器识别
    REGISTERED:   插件类已注册到管理器，尚未实例化
    INITIALIZING: 插件正在执行 initialize() 方法
    READY:        插件已初始化完成，等待执行
    RUNNING:      插件正在执行 execute() 方法
    COMPLETED:    插件一次执行完成（自动回到 READY）
    PAUSED:       插件被暂停（管理器可暂停长时间运行插件）
    STOPPING:     插件正在执行 cleanup() 方法
    STOPPED:      插件已停止但未卸载
    ERROR:        插件遇到不可恢复的错误
    """
    UNREGISTERED = auto()
    REGISTERED = auto()
    INITIALIZING = auto()
    READY = auto()
    RUNNING = auto()
    COMPLETED = auto()
    PAUSED = auto()
    STOPPING = auto()
    STOPPED = auto()
    ERROR = auto()


class PluginPriority(Enum):
    """
    插件执行优先级

    管理器在并发调度时根据优先级决定执行顺序:
    - CRITICAL: 系统关键插件，必须优先执行
    - HIGH: 重要业务插件
    - NORMAL: 常规插件（默认）
    - LOW: 后台任务插件
    - IDLE: 仅在系统空闲时执行
    """
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3
    IDLE = 4


@dataclass
class PluginResult:
    """
    插件执行结果 — 统一返回类型

    所有插件的 execute() 方法必须返回此类型的实例。

    字段说明:
        success: 执行是否成功（布尔值，必填）
        data: 插件产出的业务数据（任意可序列化对象）
        message: 人类可读的状态描述
        error_code: 错误码（成功时为空字符串）
        error_detail: 详细错误信息（含堆栈跟踪，仅生产环境脱敏）
        execution_time_ms: 执行耗时（毫秒）
        warnings: 非致命的警告信息列表
        metrics: 插件自定义的性能指标
        timestamp: 结果生成时间戳
    """
    success: bool
    data: Optional[Any] = None
    message: str = ""
    error_code: str = ""
    error_detail: str = ""
    execution_time_ms: float = 0.0
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def ok(cls, data: Any = None, message: str = "执行成功",
           execution_time_ms: float = 0.0, **kwargs) -> "PluginResult":
        """
        工厂方法：快速创建一个成功结果

        用法:
            return PluginResult.ok(data={"temperature": 25.0})
            return PluginResult.ok(message="仿真完成", data=df)
        """
        return cls(
            success=True,
            data=data,
            message=message,
            execution_time_ms=execution_time_ms,
            **kwargs
        )

    @classmethod
    def fail(cls, message: str = "执行失败", error_code: str = "UNKNOWN_ERROR",
             error_detail: str = "", **kwargs) -> "PluginResult":
        """
        工厂方法：快速创建一个失败结果

        用法:
            return PluginResult.fail("参数缺失", error_code="MISSING_PARAM")
            return PluginResult.fail("连接超时", "TIMEOUT", detail)
        """
        return cls(
            success=False,
            message=message,
            error_code=error_code,
            error_detail=error_detail,
            **kwargs
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为可 JSON 序列化的字典"""
        return {
            "success": self.success,
            "data": self.data,
            "message": self.message,
            "error_code": self.error_code,
            "error_detail": self.error_detail,
            "execution_time_ms": self.execution_time_ms,
            "warnings": self.warnings,
            "metrics": self.metrics,
            "timestamp": self.timestamp
        }

    def to_json(self, indent: int = 2) -> str:
        """转换为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, default=str)

    def has_warnings(self) -> bool:
        """是否有警告信息"""
        return len(self.warnings) > 0


@dataclass
class PluginInfo:
    """
    插件注册信息 — 完整描述一个已注册插件

    字段说明:
        name: 插件唯一标识名称
        version: 语义化版本号 (如 "2.1.0")
        description: 功能描述
        author: 作者/组织名称
        status: 当前状态
        priority: 执行优先级
        dependencies: 依赖的其他插件名称列表
        config_schema: 配置参数的 JSON Schema
        tags: 分类标签列表
        registered_at: 注册时间戳
        last_loaded_at: 最近一次加载时间
        last_executed_at: 最近一次执行时间
        execution_count: 累计执行次数
    """
    name: str
    version: str = "0.0.0"
    description: str = ""
    author: str = ""
    status: PluginStatus = PluginStatus.REGISTERED
    priority: PluginPriority = PluginPriority.NORMAL
    dependencies: List[str] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    registered_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_loaded_at: Optional[str] = None
    last_executed_at: Optional[str] = None
    execution_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "status": self.status.name,
            "priority": self.priority.name,
            "dependencies": self.dependencies,
            "config_schema": self.config_schema,
            "tags": self.tags,
            "registered_at": self.registered_at,
            "last_loaded_at": self.last_loaded_at,
            "last_executed_at": self.last_executed_at,
            "execution_count": self.execution_count
        }


@dataclass
class PluginManifest:
    """
    插件清单 — 描述插件的能力和接口

    第三方应用通过读取此清单了解插件的:
    - 输入参数规格
    - 输出格式
    - 支持的操作
    - 版本兼容性
    """
    name: str
    version: str
    supported_operations: List[str]
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    min_system_version: str = "1.0.0"
    max_instances: int = 1
    category: str = "algorithm"


@dataclass
class PluginEvent:
    """
    插件事件 — 用于插件间通信和外部系统通知

    事件类型包括:
        PLUGIN_REGISTERED: 插件已注册
        PLUGIN_INITIALIZED: 插件初始化完成
        PLUGIN_STARTED: 插件开始执行
        PLUGIN_COMPLETED: 插件执行完成
        PLUGIN_ERROR: 插件发生错误
        PLUGIN_STOPPED: 插件已停止
        STATE_CHANGED: 任意状态变更
        METRICS_UPDATED: 性能指标更新
    """
    event_type: str
    plugin_name: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    data: Optional[Dict[str, Any]] = None
    result: Optional[PluginResult] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "event_type": self.event_type,
            "plugin_name": self.plugin_name,
            "timestamp": self.timestamp,
            "data": self.data
        }
        if self.result:
            d["result"] = self.result.to_dict()
        return d


PluginCallback = Callable[[PluginEvent], None]
"""插件事件回调函数类型签名"""

PluginConfig = Dict[str, Any]
"""插件配置参数类型"""

PluginContext = Dict[str, Any]
"""插件运行上下文类型"""

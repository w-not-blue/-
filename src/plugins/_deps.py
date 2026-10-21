"""
GitHub Skills 可选依赖探测 (Lazy Detection)

对 Gymnasium / Stable Baselines3 / pymoo 三个可选依赖做软探测，
未安装时返回 False 而不抛异常，供插件层做降级处理。

设计原则:
    - 所有探测函数使用 try/except ImportError，绝不向上抛
    - 提供 ``get_dependency_status()`` 统一汇总，方便 CLI / API 透出
    - 提供 ``install_hint`` 字段，便于生成安装指引

对应测试: ``tests/plugins/test_pymoo_plugin.py::TestDependencyMissingFallback``
"""

from __future__ import annotations

from typing import Any, Dict


def has_gymnasium() -> bool:
    """检查 ``gymnasium`` (RL 环境标准 API) 是否安装。"""
    try:
        import gymnasium  # noqa: F401
        return True
    except ImportError:
        return False


def has_sb3() -> bool:
    """检查 ``stable_baselines3`` (DLR-RM RL 算法库) 是否安装。"""
    try:
        import stable_baselines3  # noqa: F401
        return True
    except ImportError:
        return False


def has_pymoo() -> bool:
    """检查 ``pymoo`` (多目标优化库) 是否安装。"""
    try:
        import pymoo  # noqa: F401
        return True
    except ImportError:
        return False


def _probe_version(import_name: str, mod: Any = None) -> str:
    """安全获取已安装库的版本号。"""
    try:
        m = mod if mod is not None else __import__(import_name)
        return getattr(m, "__version__", "unknown")
    except Exception:
        return "unknown"


def get_dependency_status() -> Dict[str, Any]:
    """
    返回三个可选依赖的统一状态。

    Returns:
        dict: ``{
            "gymnasium": {"available": bool, "version": str, "install_hint": str},
            "stable_baselines3": {...},
            "pymoo": {...},
        }``
    """
    status: Dict[str, Any] = {}

    # gymnasium
    g_avail = has_gymnasium()
    g_ver = _probe_version("gymnasium") if g_avail else None
    status["gymnasium"] = {
        "available": g_avail,
        "version": g_ver,
        "install_hint": "pip install gymnasium>=1.0",
    }

    # stable_baselines3
    sb3_avail = has_sb3()
    sb3_ver = _probe_version("stable_baselines3") if sb3_avail else None
    status["stable_baselines3"] = {
        "available": sb3_avail,
        "version": sb3_ver,
        "install_hint": "pip install stable-baselines3>=2.0",
    }

    # pymoo
    p_avail = has_pymoo()
    p_ver = None
    if p_avail:
        try:
            from pymoo import __version__ as _p_ver
            p_ver = _p_ver
        except Exception:
            p_ver = "unknown"
    status["pymoo"] = {
        "available": p_avail,
        "version": p_ver,
        "install_hint": "pip install pymoo>=0.6",
    }

    return status

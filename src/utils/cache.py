"""
缓存模块

提供内存缓存支持，用于优化频繁的数据读取操作:
- 基于 TTL (Time-To-Live) 的缓存过期机制
- 线程安全的缓存操作
- 支持手动失效和自动清理
"""

import threading
import time
from typing import Any, Dict, Optional, Callable
from functools import wraps
from src.utils.logger import setup_logger

logger = setup_logger("cache")


class CacheEntry:
    """缓存条目"""

    def __init__(self, value: Any, ttl_sec: int):
        self.value = value
        self.created_at = time.time()
        self.ttl_sec = ttl_sec

    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl_sec


class Cache:
    """
    线程安全的内存缓存

    使用示例:
        cache = Cache()
        cache.set("key", value, ttl_sec=300)
        value = cache.get("key")
    """

    def __init__(self):
        self._store: Dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[Any]:
        """获取缓存值，过期返回 None"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._store[key]
                logger.debug(f"Cache expired for key: {key}")
                return None
            logger.debug(f"Cache hit for key: {key}")
            return entry.value

    def set(self, key: str, value: Any, ttl_sec: int = 300) -> None:
        """设置缓存值，默认 TTL 5 分钟"""
        with self._lock:
            self._store[key] = CacheEntry(value, ttl_sec)
            logger.debug(f"Cache set for key: {key} (ttl={ttl_sec}s)")

    def delete(self, key: str) -> bool:
        """删除缓存条目"""
        with self._lock:
            if key in self._store:
                del self._store[key]
                logger.debug(f"Cache deleted for key: {key}")
                return True
            return False

    def clear(self) -> None:
        """清空所有缓存"""
        with self._lock:
            self._store.clear()
            logger.info("Cache cleared")

    def cleanup_expired(self) -> int:
        """清理过期条目，返回清理数量"""
        with self._lock:
            expired_keys = [k for k, v in self._store.items() if v.is_expired()]
            for k in expired_keys:
                del self._store[k]
            if expired_keys:
                logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
            return len(expired_keys)

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        with self._lock:
            total = len(self._store)
            expired = sum(1 for v in self._store.values() if v.is_expired())
            return {
                'total_entries': total,
                'expired_entries': expired,
                'active_entries': total - expired
            }


# 全局缓存实例
_global_cache = Cache()


def get_cache() -> Cache:
    """获取全局缓存实例"""
    return _global_cache


def cached(ttl_sec: int = 300, key_func: Optional[Callable] = None):
    """
    缓存装饰器

    Args:
        ttl_sec: 缓存过期时间（秒）
        key_func: 自定义缓存键生成函数，接收原函数参数，返回字符串键

    使用示例:
        @cached(ttl_sec=60)
        def expensive_function(x, y):
            return x + y
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache = get_cache()

            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # 默认使用函数名和参数生成键
                key_parts = [func.__module__, func.__name__]
                if args:
                    key_parts.append(str(args))
                if kwargs:
                    key_parts.append(str(sorted(kwargs.items())))
                cache_key = "|".join(key_parts)

            cached_value = cache.get(cache_key)
            if cached_value is not None:
                return cached_value

            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl_sec)
            return result
        return wrapper
    return decorator

"""
项目级安全输入验证模块

提供统一的输入验证、清洗和安全防护:
- 类型验证与转换
- 范围约束检查
- SQL/XSS/命令注入防护
- 安全HTTP头生成
- 速率限制辅助

参考: OWASP Input Validation Cheat Sheet, Flask-Security
"""
import re
import os
import hashlib
import secrets
from typing import Any, Dict, List, Optional, Tuple, TypeVar, Union

T = TypeVar('T')

class ValidationError(ValueError):
    pass

def validate_int(value: Any, min_val: int = None, max_val: int = None,
                 default: int = None, name: str = "value") -> int:
    if value is None:
        if default is not None:
            return default
        raise ValidationError(f"{name} 不能为空")
    try:
        v = int(value)
    except (ValueError, TypeError):
        raise ValidationError(f"{name} 必须是整数，收到: {value}")
    if min_val is not None and v < min_val:
        raise ValidationError(f"{name} 不能小于 {min_val}，当前: {v}")
    if max_val is not None and v > max_val:
        raise ValidationError(f"{name} 不能大于 {max_val}，当前: {v}")
    return v

def validate_float(value: Any, min_val: float = None, max_val: float = None,
                   default: float = None, name: str = "value") -> float:
    if value is None:
        if default is not None:
            return default
        raise ValidationError(f"{name} 不能为空")
    try:
        v = float(value)
    except (ValueError, TypeError):
        raise ValidationError(f"{name} 必须是数字，收到: {value}")
    if min_val is not None and v < min_val:
        raise ValidationError(f"{name} 不能小于 {min_val}，当前: {v}")
    if max_val is not None and v > max_val:
        raise ValidationError(f"{name} 不能大于 {max_val}，当前: {v}")
    return v

def validate_str(value: Any, min_len: int = 0, max_len: int = 256,
                 pattern: str = None, allowed_chars: str = None,
                 default: str = "", name: str = "value") -> str:
    if value is None:
        if default is not None:
            return default
        raise ValidationError(f"{name} 不能为空")
    v = str(value)
    if len(v) < min_len:
        raise ValidationError(f"{name} 长度不能小于 {min_len}")
    if len(v) > max_len:
        raise ValidationError(f"{name} 长度不能超过 {max_len}")
    if pattern and not re.match(pattern, v):
        raise ValidationError(f"{name} 格式无效: {v}")
    if allowed_chars:
        for ch in v:
            if ch not in allowed_chars:
                raise ValidationError(f"{name} 包含非法字符: '{ch}'")
    return v

def validate_algorithms(algorithms: Any, allowed: List[str] = None) -> List[str]:
    if allowed is None:
        allowed = ['greedy', 'genetic', 'round_robin', 'damo_ets', 'nsga2', 'moead']
    if algorithms is None:
        return ['greedy', 'genetic', 'round_robin', 'damo_ets']
    if isinstance(algorithms, str):
        algorithms = [a.strip() for a in algorithms.split(',')]
    if not isinstance(algorithms, list):
        raise ValidationError("algorithms 必须是列表或逗号分隔字符串")
    result = []
    for algo in algorithms:
        algo = str(algo).strip().lower()
        if algo not in allowed:
            raise ValidationError(f"不支持的算法: '{algo}'，支持: {allowed}")
        result.append(algo)
    return result or ['greedy']

def sanitize_filename(filename: str, max_len: int = 128) -> str:
    dangerous = r'[<>:"/\\|?*\';&$`!#]'
    sanitized = re.sub(dangerous, '_', filename)
    path_traversal = r'(\.\./|\.\.\\)'
    sanitized = re.sub(path_traversal, '', sanitized)
    sanitized = sanitized.strip('. ')
    if len(sanitized) > max_len:
        name, ext = os.path.splitext(sanitized)
        sanitized = name[:max_len - len(ext)] + ext
    if not sanitized:
        sanitized = 'download'
    return sanitized

SQL_INJECTION_PATTERNS = re.compile(
    r"(UNION\s+SELECT|DROP\s+TABLE|ALTER\s+TABLE|INSERT\s+INTO|"
    r"DELETE\s+FROM|--\s|\bOR\b\s+['\"]?\d['\"]?\s*=\s*['\"]?\d)",
    re.IGNORECASE
)

XSS_PATTERNS = re.compile(
    r"(<script|javascript:|onerror\s*=|onload\s*=|"
    r"<img[^>]+onerror|<svg|eval\s*\()",
    re.IGNORECASE
)

def detect_sql_injection(value: str) -> bool:
    return bool(SQL_INJECTION_PATTERNS.search(str(value)))

def detect_xss(value: str) -> bool:
    return bool(XSS_PATTERNS.search(str(value)))

def sanitize_html(value: str) -> str:
    return (value
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#x27;'))

def validate_simulation_params(data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        'num_servers': validate_int(
            data.get('num_servers'), min_val=1, max_val=1000, default=10,
            name='服务器数量'),
        'simulation_hours': validate_int(
            data.get('simulation_hours'), min_val=1, max_val=720, default=24,
            name='仿真时长'),
        'parallel_workers': validate_int(
            data.get('parallel_workers'), min_val=1, max_val=32, default=4,
            name='并行工作器'),
        'run_mode': validate_str(
            data.get('run_mode'), max_len=32,
            pattern=r'^(standard|distributed|large_scale)$', default='standard',
            name='运行模式'),
        'server_type': validate_str(
            data.get('server_type'), max_len=32,
            pattern=r'^(mixed|standard|high_performance|gpu_server)$', default='mixed',
            name='服务器类型'),
        'algorithms': validate_algorithms(data.get('algorithms')),
        'visualization': bool(data.get('visualization', True)),
        'save_results': bool(data.get('save_results', True)),
    }

def security_headers() -> Dict[str, str]:
    return {
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
        'X-XSS-Protection': '1; mode=block',
        'Referrer-Policy': 'strict-origin-when-cross-origin',
        'Permissions-Policy': 'camera=(), microphone=(self), geolocation=()',
        'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
        'Content-Security-Policy': (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "img-src 'self' data:; "
            "font-src 'self' https://cdnjs.cloudflare.com; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "form-action 'self'"
        ),
    }

def generate_csrf_token() -> str:
    return secrets.token_hex(32)

def hash_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

class RateLimiter:
    """简易令牌桶速率限制器"""

    def __init__(self, max_requests: int = 60, window_sec: float = 60.0):
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._clients: Dict[str, List[float]] = {}
        import threading
        self._lock = threading.Lock()

    def _cleanup(self, now: float) -> None:
        for ip in list(self._clients.keys()):
            self._clients[ip] = [t for t in self._clients[ip] if now - t < self.window_sec]
            if not self._clients[ip]:
                del self._clients[ip]

    def is_allowed(self, client_ip: str) -> bool:
        import time
        now = time.time()
        with self._lock:
            self._cleanup(now)
            if client_ip not in self._clients:
                self._clients[client_ip] = []
            if len(self._clients[client_ip]) >= self.max_requests:
                return False
            self._clients[client_ip].append(now)
            return True

    def remaining(self, client_ip: str) -> int:
        import time
        now = time.time()
        with self._lock:
            self._cleanup(now)
            return max(0, self.max_requests - len(self._clients.get(client_ip, [])))

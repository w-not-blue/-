"""
仿真模拟测试环境 - Mock服务组件

提供可配置的模拟服务组件，实现对各类接口和协议的模拟支持:
- 网络拓扑模拟（延迟/带宽/丢包/抖动）
- 硬件配置模拟（CPU/RAM/Disk/NIC）
- 协议Mock（HTTP/gRPC/WebSocket）
- 数据流量模拟（正常/峰值/突发/DDoS）
"""

import random
import time
import json
import threading
import queue
from typing import Dict, Any, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from src.utils.logger import setup_logger
from src.utils.config import Config

logger = setup_logger("mock_services")
config = Config()

class NetworkCondition(Enum):
    PERFECT = "perfect"
    NORMAL = "normal"
    DEGRADED = "degraded"
    CONGESTED = "congested"
    DISCONNECTED = "disconnected"

@dataclass
class NetworkRegion:
    name: str
    latency_ms: float
    bandwidth_mbps: float
    packet_loss: float
    jitter_ms: float

@dataclass
class HardwareProfile:
    name: str
    cpu_cores: int
    memory_gb: int
    disk_gb: int
    nic_speed_mbps: int
    gpu: Optional[str]
    npu: Optional[str]
    os: str
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    disk_usage: float = 0.0

@dataclass
class TrafficStats:
    requests_total: int = 0
    requests_success: int = 0
    requests_failed: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    avg_response_ms: float = 0.0
    max_response_ms: float = 0.0
    min_response_ms: float = float('inf')
    error_rate: float = 0.0

class NetworkSimulator:
    """
    网络拓扑模拟器
    
    模拟Spine-Leaf数据中心网络拓扑:
           [Spine1]       [Spine2]
           /     \       /     \
      [Leaf1]  [Leaf2] [Leaf3] [Leaf4]
         |        |       |       |
      [Server] [Server] [Server] [Server]
    
    参考项目: Mininet, NS-3
    """

    def __init__(self, regions: List[Dict] = None):
        self.regions: List[NetworkRegion] = []
        self.condition = NetworkCondition.NORMAL
        self._latency_multiplier = 1.0
        self._packet_loss_multiplier = 1.0
        self._blocked_regions: set = set()
        
        if regions:
            for r in regions:
                self.regions.append(NetworkRegion(**r))
        else:
            cfg_regions = config.network.get('regions', []) if hasattr(config, 'network') else []
            for r in cfg_regions:
                self.regions.append(NetworkRegion(**r))

    def set_condition(self, condition: NetworkCondition) -> None:
        self.condition = condition
        multipliers = {
            NetworkCondition.PERFECT: (0.5, 0.0),
            NetworkCondition.NORMAL: (1.0, 1.0),
            NetworkCondition.DEGRADED: (2.0, 5.0),
            NetworkCondition.CONGESTED: (5.0, 10.0),
            NetworkCondition.DISCONNECTED: (1000.0, 1.0),
        }
        self._latency_multiplier, self._packet_loss_multiplier = multipliers[condition]
        logger.info(f"Network condition set to {condition.value}")

    def get_latency(self, region_name: str) -> float:
        if region_name in self._blocked_regions:
            return 9999.0
        for r in self.regions:
            if r.name == region_name:
                base = r.latency_ms + random.gauss(0, r.jitter_ms)
                return max(0, base * self._latency_multiplier)
        return 1.0 * self._latency_multiplier

    def simulates_packet_loss(self, region_name: str) -> bool:
        if region_name in self._blocked_regions:
            return True
        for r in self.regions:
            if r.name == region_name:
                return random.random() < r.packet_loss * self._packet_loss_multiplier
        return False

    def get_bandwidth(self, region_name: str) -> float:
        for r in self.regions:
            if r.name == region_name:
                if self.condition == NetworkCondition.CONGESTED:
                    return r.bandwidth_mbps * 0.1
                return r.bandwidth_mbps
        return 1000.0

    def block_region(self, region_name: str) -> None:
        self._blocked_regions.add(region_name)
        logger.warning(f"Region blocked: {region_name}")

    def unblock_region(self, region_name: str) -> None:
        self._blocked_regions.discard(region_name)

    def simulate_transfer(self, data_size_bytes: int, region_name: str) -> Tuple[float, bool]:
        bw = self.get_bandwidth(region_name) * 1e6 / 8
        transfer_time = data_size_bytes / max(bw, 1)
        
        latency = self.get_latency(region_name) / 1000
        total_time = transfer_time + latency * 2
        
        lost = self.simulates_packet_loss(region_name)
        
        return total_time, not lost


class HardwareProfileSimulator:
    """
    硬件配置模拟器
    
    模拟不同硬件平台的计算能力:
    - 鲲鹏920 + 昇腾310 (国产化)
    - 标准x86 + NVIDIA A100
    - 高性能GPU节点 (H100)
    - 边缘计算节点
    
    参考项目: NVIDIA DCGM, Prometheus Node Exporter
    """

    def __init__(self, profiles: List[Dict] = None):
        self.profiles: List[HardwareProfile] = []
        if profiles:
            for p in profiles:
                self.profiles.append(HardwareProfile(**p))
        else:
            cfg_profiles = config.hardware_profiles if hasattr(config, 'hardware_profiles') else []
            for p in cfg_profiles:
                self.profiles.append(HardwareProfile(**p))

    def get_profile(self, name: str) -> Optional[HardwareProfile]:
        for p in self.profiles:
            if p.name == name:
                return p
        return None

    def simulate_cpu_load(self, profile: HardwareProfile, load_pct: float) -> float:
        profile.cpu_usage = min(100.0, max(0.0, load_pct))
        return profile.cpu_cores * (1.0 - profile.cpu_usage / 100.0)

    def simulate_memory_usage(self, profile: HardwareProfile, usage_gb: float) -> Tuple[float, bool]:
        profile.memory_usage = usage_gb
        pct = usage_gb / profile.memory_gb * 100.0
        return pct, pct < 90.0

    def simulate_disk_io(self, profile: HardwareProfile, read_mbps: float, write_mbps: float) -> float:
        total = read_mbps + write_mbps
        return min(100.0, total / 1000.0 * 100.0)

    def estimate_task_capacity(self, profile: HardwareProfile) -> int:
        base = profile.cpu_cores * 2
        if profile.gpu:
            base *= 3
        if profile.npu:
            base *= 2
        return int(base)

    def get_all_status(self) -> List[Dict[str, Any]]:
        return [
            {
                'name': p.name, 'cpu_usage': p.cpu_usage,
                'memory_usage': p.memory_usage, 'disk_usage': p.disk_usage,
                'available_cores': p.cpu_cores * (1 - p.cpu_usage / 100),
                'available_memory': p.memory_gb * (1 - p.memory_usage / 100)
            } for p in self.profiles
        ]


class ProtocolMockServer:
    """
    协议模拟服务器
    
    模拟各类API接口行为:
    - HTTP REST API (正常/延迟/错误响应)
    - WebSocket (实时推送)
    - 自定义协议
    
    参考项目: WireMock, Mockoon, Postman Mock Server
    """

    def __init__(self, base_url: str = "http://localhost:5000"):
        self.base_url = base_url
        self._mocks: Dict[str, Dict[str, Any]] = {}
        self._response_delay = 0.0
        self._error_rate = 0.0
        self._request_log: List[Dict] = []
        self._callbacks: Dict[str, Callable] = {}

    def register_mock(self, path: str, method: str = "GET",
                      response: Any = None, status_code: int = 200,
                      headers: Dict = None, delay_sec: float = 0.0) -> None:
        key = f"{method}:{path}"
        self._mocks[key] = {
            'response': response or {},
            'status_code': status_code,
            'headers': headers or {'Content-Type': 'application/json'},
            'delay': delay_sec
        }

    def set_error_injection(self, rate: float) -> None:
        self._error_rate = max(0.0, min(1.0, rate))

    def set_response_delay(self, delay_sec: float) -> None:
        self._response_delay = delay_sec

    def mock_request(self, method: str, path: str, data: Any = None) -> Dict[str, Any]:
        key = f"{method}:{path}"
        self._request_log.append({
            'method': method, 'path': path, 'data': data,
            'timestamp': datetime.now().isoformat()
        })

        if random.random() < self._error_rate:
            return {'status_code': 500, 'body': {'error': 'Injected server error'}}

        mock = self._mocks.get(key)
        if not mock:
            return {'status_code': 404, 'body': {'error': f'No mock for {key}'}}

        if mock['delay'] > 0 or self._response_delay > 0:
            time.sleep(max(mock['delay'], self._response_delay))

        return {
            'status_code': mock['status_code'],
            'body': mock['response']() if callable(mock['response']) else mock['response'],
            'headers': mock['headers']
        }

    def register_callback(self, path: str, callback: Callable) -> None:
        self._callbacks[path] = callback

    def get_request_log(self, limit: int = 100) -> List[Dict]:
        return self._request_log[-limit:]

    def clear_request_log(self) -> None:
        self._request_log.clear()


class TrafficGenerator:
    """
    数据流量生成器
    
    模拟不同负载场景:
    - 正常负载（日常运行）
    - 潮汐峰值（工作时间高峰）
    - 突发流量（瞬时高并发）
    - 持续高压（长时间高负载）
    - DDoS模拟（攻击流量）
    
    参考项目: Apache JMeter, Locust, wrk2
    """

    def __init__(self, patterns: List[Dict] = None):
        self.patterns: List[Dict] = patterns or []
        self._active = False
        self._threads: List[threading.Thread] = []
        self.stats = TrafficStats()

    def load_config_patterns(self) -> None:
        cfg_patterns = config.traffic_patterns if hasattr(config, 'traffic_patterns') else []
        for p in cfg_patterns:
            self.patterns.append(p)

    def generate_traffic(self, pattern_name: str, target_api: str,
                         request_func: Callable) -> TrafficStats:
        pattern = None
        for p in self.patterns:
            if p['name'] == pattern_name:
                pattern = p
                break
        
        if not pattern:
            raise ValueError(f"Unknown pattern: {pattern_name}")

        rps = pattern['requests_per_sec']
        duration = pattern['duration_minutes'] * 60
        stats = TrafficStats()
        
        start_time = time.time()
        request_count = 0

        while time.time() - start_time < duration:
            batch = min(rps, 100)
            for _ in range(batch):
                req_start = time.time()
                try:
                    result = request_func()
                    stats.requests_total += 1
                    if result:
                        stats.requests_success += 1
                    else:
                        stats.requests_failed += 1
                except Exception:
                    stats.requests_failed += 1
                
                elapsed = (time.time() - req_start) * 1000
                stats.avg_response_ms = (stats.avg_response_ms * request_count + elapsed) / (request_count + 1)
                stats.max_response_ms = max(stats.max_response_ms, elapsed)
                stats.min_response_ms = min(stats.min_response_ms, elapsed)
                request_count += 1

            time.sleep(1.0 / max(rps / batch, 1))

        stats.error_rate = stats.requests_failed / max(stats.requests_total, 1)
        self.stats = stats
        return stats

    def generate_burst(self, num_requests: int, target_api: str,
                       request_func: Callable) -> TrafficStats:
        stats = TrafficStats()
        start = time.time()
        
        for i in range(num_requests):
            req_start = time.time()
            try:
                request_func()
                stats.requests_success += 1
            except Exception:
                stats.requests_failed += 1
            stats.requests_total += 1
            
            elapsed = (time.time() - req_start) * 1000
            stats.max_response_ms = max(stats.max_response_ms, elapsed)
            stats.min_response_ms = min(stats.min_response_ms, elapsed)

        elapsed_total = (time.time() - start)
        if elapsed_total > 0:
            stats.avg_response_ms = (elapsed_total / num_requests) * 1000
        stats.error_rate = stats.requests_failed / max(stats.requests_total, 1)
        self.stats = stats
        return stats

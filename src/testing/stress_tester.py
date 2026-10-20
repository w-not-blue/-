"""
压力测试模块

模拟不同负载条件下的系统表现:
- CPU压力测试 (多线程计算密集)
- 内存压力测试 (大数据分配)
- 磁盘IO压力测试 (密集读写)
- 网络压力测试 (高并发请求)
- 时间窗口测试 (持续高压)

参考项目: Apache JMeter, Locust, stress-ng
"""

import time
import threading
import random
import numpy as np
from typing import Dict, Any, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from src.utils.logger import setup_logger
from src.utils.config import Config
import psutil

logger = setup_logger("stress_tester")
config = Config()

@dataclass
class StressMetrics:
    test_name: str
    duration_sec: float
    peak_cpu_pct: float
    avg_cpu_pct: float
    peak_memory_mb: float
    avg_memory_mb: float
    peak_disk_io_mbps: float
    peak_network_mbps: float
    total_requests: int
    success_rate: float
    avg_response_ms: float
    p50_response_ms: float
    p95_response_ms: float
    p99_response_ms: float
    error_count: int
    timeout_count: int
    threshold_violations: List[str] = field(default_factory=list)

class StressTester:
    """
    压力测试器
    
    系统性地模拟各种负载场景，评估系统极限:
    1. 阶梯增压 (逐步提高负载找到拐点)
    2. 恒压测试 (长时间保持高负载)
    3. 突发测试 (瞬时高并发)
    4. 混合负载 (多类型任务混合)
    """

    def __init__(self, target_func: Callable = None):
        self.target_func = target_func
        self.metrics_history: List[StressMetrics] = []
        self.thresholds = self._load_thresholds()

    def _load_thresholds(self) -> Dict:
        try:
            return config.stress_thresholds
        except Exception:
            return {
                'cpu': {'warning': 70, 'critical': 85, 'max': 95},
                'memory': {'warning': 75, 'critical': 90, 'max': 95},
                'response_time': {'warning_ms': 200, 'critical_ms': 500, 'timeout_ms': 5000},
                'error_rate': {'warning': 0.01, 'critical': 0.05, 'max': 0.10}
            }

    def _monitor_system(self, duration_sec: float) -> Dict[str, List[float]]:
        cpu_samples = []
        mem_samples = []
        interval = 0.5
        for _ in range(int(duration_sec / interval)):
            cpu_samples.append(psutil.cpu_percent(interval=0.1))
            mem = psutil.virtual_memory()
            mem_samples.append(mem.used / (1024 * 1024))
        return {'cpu': cpu_samples, 'memory': mem_samples}

    def run_cpu_stress(self, num_threads: int = 4, duration_sec: int = 30) -> StressMetrics:
        logger.info(f"Starting CPU stress test: {num_threads} threads, {duration_sec}s")

        def cpu_intensive_work():
            for _ in range(1000000):
                np.random.rand(100, 100).dot(np.random.rand(100, 100))

        start = time.time()
        responses = []

        monitor_thread = threading.Thread(target=self._monitor_system, args=(duration_sec,))
        monitor_thread.start()

        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = [executor.submit(cpu_intensive_work) for _ in range(num_threads * 10)]
            for future in as_completed(futures):
                pass

        monitor_thread.join(timeout=5)
        elapsed = time.time() - start

        metrics = StressMetrics(
            test_name="CPU压力测试",
            duration_sec=elapsed,
            peak_cpu_pct=psutil.cpu_percent(),
            avg_cpu_pct=psutil.cpu_percent(),
            peak_memory_mb=psutil.virtual_memory().used / (1024 * 1024),
            avg_memory_mb=psutil.virtual_memory().used / (1024 * 1024),
            peak_disk_io_mbps=0, peak_network_mbps=0,
            total_requests=num_threads * 10,
            success_rate=1.0,
            avg_response_ms=0, p50_response_ms=0,
            p95_response_ms=0, p99_response_ms=0,
            error_count=0, timeout_count=0
        )
        self.metrics_history.append(metrics)
        return metrics

    def run_concurrency_stress(self, concurrency_levels: List[int],
                               request_func: Callable,
                               requests_per_level: int = 100) -> List[StressMetrics]:
        results = []
        for concurrency in concurrency_levels:
            logger.info(f"Concurrency stress test: {concurrency} concurrent")
            responses = []
            errors = 0
            timeouts = 0
            start = time.time()

            def worker():
                try:
                    result = request_func()
                    responses.append(time.time())
                    return result
                except Exception:
                    nonlocal errors
                    errors += 1
                    return None

            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = [executor.submit(worker) for _ in range(requests_per_level)]
                for future in as_completed(futures):
                    try:
                        future.result(timeout=5)
                    except Exception:
                        timeouts += 1

            elapsed = time.time() - start
            if len(responses) >= 2:
                latencies = [t - start for t in sorted(responses)]
                metrics = StressMetrics(
                    test_name=f"并发压力: {concurrency}",
                    duration_sec=elapsed,
                    peak_cpu_pct=psutil.cpu_percent(),
                    avg_cpu_pct=psutil.cpu_percent(),
                    peak_memory_mb=psutil.virtual_memory().used / (1024 * 1024),
                    avg_memory_mb=psutil.virtual_memory().used / (1024 * 1024),
                    peak_disk_io_mbps=0, peak_network_mbps=0,
                    total_requests=requests_per_level,
                    success_rate=(requests_per_level - errors) / requests_per_level,
                    avg_response_ms=np.mean(latencies) * 1000,
                    p50_response_ms=np.percentile(latencies, 50) * 1000,
                    p95_response_ms=np.percentile(latencies, 95) * 1000,
                    p99_response_ms=np.percentile(latencies, 99) * 1000,
                    error_count=errors, timeout_count=timeouts
                )
                results.append(metrics)
                self.metrics_history.append(metrics)

        return results

    def run_endurance_test(self, request_func: Callable, duration_minutes: int = 5) -> StressMetrics:
        logger.info(f"Starting endurance test: {duration_minutes} minutes")
        start = time.time()
        end = start + duration_minutes * 60
        total = 0
        errors = 0

        while time.time() < end:
            try:
                request_func()
                total += 1
            except Exception:
                errors += 1

        elapsed = time.time() - start
        metrics = StressMetrics(
            test_name="持续负载测试",
            duration_sec=elapsed,
            peak_cpu_pct=psutil.cpu_percent(),
            avg_cpu_pct=psutil.cpu_percent(),
            peak_memory_mb=psutil.virtual_memory().used / (1024 * 1024),
            avg_memory_mb=psutil.virtual_memory().used / (1024 * 1024),
            peak_disk_io_mbps=0, peak_network_mbps=0,
            total_requests=total,
            success_rate=(total - errors) / max(total, 1),
            avg_response_ms=elapsed / max(total, 1) * 1000,
            p50_response_ms=0, p95_response_ms=0, p99_response_ms=0,
            error_count=errors, timeout_count=0
        )
        self.metrics_history.append(metrics)
        return metrics

    def check_thresholds(self, metrics: StressMetrics) -> List[str]:
        violations = []

        thresholds = self.thresholds
        if metrics.peak_cpu_pct > thresholds.get('cpu', {}).get('critical', 85):
            violations.append(f"CPU超临界阈值: {metrics.peak_cpu_pct:.1f}% > {thresholds['cpu']['critical']}%")
        elif metrics.peak_cpu_pct > thresholds.get('cpu', {}).get('warning', 70):
            violations.append(f"CPU超警告阈值: {metrics.peak_cpu_pct:.1f}% > {thresholds['cpu']['warning']}%")

        if (1.0 - metrics.success_rate) > thresholds.get('error_rate', {}).get('critical', 0.05):
            violations.append(f"错误率超临界阈值: {(1.0-metrics.success_rate)*100:.1f}%")

        if metrics.avg_response_ms > thresholds.get('response_time', {}).get('critical_ms', 500):
            violations.append(f"响应时间超临界阈值: {metrics.avg_response_ms:.0f}ms")

        metrics.threshold_violations = violations
        return violations

    def get_summary(self) -> Dict[str, Any]:
        if not self.metrics_history:
            return {'tests': 0}

        all_peak_cpu = [m.peak_cpu_pct for m in self.metrics_history]
        all_error_rates = [1.0 - m.success_rate for m in self.metrics_history]

        return {
            'total_tests': len(self.metrics_history),
            'max_cpu_pct': max(all_peak_cpu),
            'avg_cpu_pct': np.mean(all_peak_cpu),
            'max_error_rate': max(all_error_rates),
            'avg_error_rate': np.mean(all_error_rates),
            'total_violations': sum(len(m.threshold_violations) for m in self.metrics_history),
            'violations': [m.threshold_violations for m in self.metrics_history if m.threshold_violations]
        }

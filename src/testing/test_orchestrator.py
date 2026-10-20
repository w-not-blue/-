"""
自动化测试引擎

建立自动化测试流程，支持:
- 兼容性测试 (跨版本/跨平台/跨依赖)
- 压力测试 (CPU/内存/IO/网络/并发)
- 安全渗透测试 (注入/模糊/认证绕过)
- 回归测试

参考项目: pytest, Selenium Grid, Locust
"""

import time
import json
import threading
import subprocess
import os
from typing import Dict, Any, List, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from src.utils.logger import setup_logger
from src.utils.config import Config

logger = setup_logger("test_orchestrator")
config = Config()

class TestStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"

class TestSeverity(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

@dataclass
class TestResult:
    test_id: str
    test_name: str
    category: str
    status: TestStatus
    severity: TestSeverity
    duration_sec: float
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

@dataclass
class TestSuite:
    name: str
    description: str
    tests: List[Callable] = field(default_factory=list)
    setup: Optional[Callable] = None
    teardown: Optional[Callable] = None
    timeout_sec: int = 300


class TestOrchestrator:
    """
    测试编排引擎
    
    管理测试套件的执行流程:
    1. 环境准备 (Setup)
    2. 测试执行 (并发执行)
    3. 结果收集 (Result Collection)
    4. 环境清理 (Teardown)
    5. 报告生成 (Report)
    """

    def __init__(self, parallel_workers: int = 4):
        self.parallel_workers = parallel_workers
        self._suites: Dict[str, TestSuite] = {}
        self._results: List[TestResult] = []
        self._running = False
        self._hooks: Dict[str, List[Callable]] = {
            'before_all': [], 'after_all': [],
            'before_each': [], 'after_each': [],
            'on_failure': []
        }

    def register_suite(self, suite: TestSuite) -> None:
        self._suites[suite.name] = suite

    def add_hook(self, event: str, callback: Callable) -> None:
        if event in self._hooks:
            self._hooks[event].append(callback)

    def run_all(self) -> List[TestResult]:
        self._running = True
        self._results = []

        for hook in self._hooks['before_all']:
            hook()

        for name, suite in self._suites.items():
            suite_results = self._run_suite(suite)
            self._results.extend(suite_results)

        for hook in self._hooks['after_all']:
            hook()

        self._running = False
        return self._results

    def _run_suite(self, suite: TestSuite) -> List[TestResult]:
        results = []
        logger.info(f"Running suite: {suite.name}")

        if suite.setup:
            try:
                suite.setup()
            except Exception as e:
                logger.error(f"Suite setup failed: {e}")
                return [TestResult(
                    test_id=f"{suite.name}_setup",
                    test_name="Suite Setup",
                    category="setup",
                    status=TestStatus.ERROR,
                    severity=TestSeverity.CRITICAL,
                    duration_sec=0,
                    message=str(e)
                )]

        with ThreadPoolExecutor(max_workers=self.parallel_workers) as executor:
            futures = {}
            for test in suite.tests:
                future = executor.submit(self._run_single_test, test, suite)
                futures[future] = test

            for future in as_completed(futures):
                try:
                    result = future.result(timeout=suite.timeout_sec)
                    results.append(result)
                except Exception as e:
                    test = futures[future]
                    results.append(TestResult(
                        test_id=getattr(test, '__name__', 'unknown'),
                        test_name=getattr(test, '__name__', 'unknown'),
                        category=suite.name,
                        status=TestStatus.ERROR,
                        severity=TestSeverity.HIGH,
                        duration_sec=suite.timeout_sec,
                        message=str(e)
                    ))

        if suite.teardown:
            try:
                suite.teardown()
            except Exception as e:
                logger.error(f"Suite teardown failed: {e}")

        return results

    def _run_single_test(self, test_func: Callable, suite: TestSuite) -> TestResult:
        name = getattr(test_func, '__name__', 'unknown')
        start = time.time()

        for hook in self._hooks['before_each']:
            hook()

        try:
            result = test_func()
            status = TestStatus.PASSED if result else TestStatus.FAILED
            message = "Test passed" if result else "Test assertion failed"
        except Exception as e:
            status = TestStatus.ERROR
            message = str(e)
            for hook in self._hooks['on_failure']:
                hook(name, str(e))

        for hook in self._hooks['after_each']:
            hook()

        elapsed = time.time() - start
        return TestResult(
            test_id=name, test_name=name, category=suite.name,
            status=status, severity=TestSeverity.MEDIUM,
            duration_sec=elapsed, message=message
        )

    def get_summary(self) -> Dict[str, Any]:
        total = len(self._results)
        passed = sum(1 for r in self._results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in self._results if r.status == TestStatus.FAILED)
        errors = sum(1 for r in self._results if r.status == TestStatus.ERROR)
        skipped = sum(1 for r in self._results if r.status == TestStatus.SKIPPED)
        
        return {
            'total': total,
            'passed': passed,
            'failed': failed,
            'errors': errors,
            'skipped': skipped,
            'pass_rate': passed / max(total, 1) * 100,
            'total_duration_sec': sum(r.duration_sec for r in self._results)
        }

    def get_results_by_severity(self, severity: TestSeverity) -> List[TestResult]:
        return [r for r in self._results if r.severity == severity]

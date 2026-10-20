"""
仿真模拟测试环境

提供完整的测试基础设施:
- Mock服务:  网络/硬件/协议/流量模拟
- 测试编排:  自动化测试流程管理
- 兼容性:    跨版本/跨平台测试
- 压力测试:  CPU/内存/IO/并发
- 安全扫描:  依赖/端口/SAST/注入
- 环境管理:  快照/回滚/重置
- 风险评估:  综合风险报告
"""

from .mock_services import (
    NetworkSimulator, HardwareProfileSimulator,
    ProtocolMockServer, TrafficGenerator,
    NetworkCondition, NetworkRegion, HardwareProfile, TrafficStats
)
from .test_orchestrator import (
    TestOrchestrator, TestSuite, TestResult,
    TestStatus, TestSeverity
)
from .compatibility_tester import (
    CompatibilityTester, CompatibilityCheck
)
from .stress_tester import (
    StressTester, StressMetrics
)
from .security_scanner import (
    SecurityScanner, Vulnerability,
    SeverityLevel, ScanType
)
from .environment_manager import (
    EnvironmentManager, Snapshot, EnvironmentState
)
from .risk_assessor import (
    RiskAssessor, RiskItem, RiskReport, RiskLevel
)
from .security_report import (
    generate_security_report, print_report_summary, SecurityReport
)
from .test_runner import run_full_test_suite

__all__ = [
    # Mock Services
    'NetworkSimulator', 'HardwareProfileSimulator',
    'ProtocolMockServer', 'TrafficGenerator',
    'NetworkCondition', 'NetworkRegion', 'HardwareProfile', 'TrafficStats',
    # Orchestrator
    'TestOrchestrator', 'TestSuite', 'TestResult',
    'TestStatus', 'TestSeverity',
    # Compatibility
    'CompatibilityTester', 'CompatibilityCheck',
    # Stress
    'StressTester', 'StressMetrics',
    # Security
    'SecurityScanner', 'Vulnerability', 'SeverityLevel', 'ScanType',
    # Environment
    'EnvironmentManager', 'Snapshot', 'EnvironmentState',
    # Risk
    'RiskAssessor', 'RiskItem', 'RiskReport', 'RiskLevel',
    # Report
    'generate_security_report', 'print_report_summary', 'SecurityReport',
    # Runner
    'run_full_test_suite'
]

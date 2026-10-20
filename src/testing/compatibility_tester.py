"""
兼容性测试模块

支持多维度的兼容性测试:
- 跨Python版本 (3.9/3.10/3.11/3.12)
- 跨操作系统 (openEuler/CentOS/Ubuntu/Windows)
- 依赖版本矩阵
- 硬件平台 (x86_64/arm64)
- API向后兼容

参考项目: tox, nox, pytest-matrix
"""

import sys
import platform
import subprocess
import importlib
from importlib.metadata import version, PackageNotFoundError
from packaging.version import parse as parse_version
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from src.utils.logger import setup_logger
from src.utils.config import Config

logger = setup_logger("compatibility_tester")
config = Config()

@dataclass
class CompatibilityCheck:
    name: str
    dimension: str
    expected: str
    actual: str
    compatible: bool
    details: str = ""

class CompatibilityTester:
    """
    兼容性测试器
    
    验证系统在不同环境下的兼容性。
    支持矩阵化的兼容性验证。
    """

    def __init__(self):
        self.checks: List[CompatibilityCheck] = []

    def check_python_version(self, min_version: str = "3.9", max_version: str = "3.12") -> CompatibilityCheck:
        current = f"{sys.version_info.major}.{sys.version_info.minor}"
        compatible = tuple(map(int, current.split('.'))) >= tuple(map(int, min_version.split('.')))
        check = CompatibilityCheck(
            name="Python版本检查",
            dimension="python_version",
            expected=f">={min_version}",
            actual=current,
            compatible=compatible
        )
        self.checks.append(check)
        return check

    def check_os_compatibility(self, supported_os: List[str] = None) -> CompatibilityCheck:
        if supported_os is None:
            supported_os = ["Linux", "Windows", "Darwin"]
        current = platform.system()
        compatible = current in supported_os
        check = CompatibilityCheck(
            name="操作系统兼容",
            dimension="os",
            expected=str(supported_os),
            actual=current,
            compatible=compatible
        )
        self.checks.append(check)
        return check

    def check_platform_arch(self, supported: List[str] = None) -> CompatibilityCheck:
        if supported is None:
            supported = ["x86_64", "AMD64", "arm64", "aarch64"]
        current = platform.machine()
        compatible = current.lower() in [s.lower() for s in supported]
        check = CompatibilityCheck(
            name="硬件架构兼容",
            dimension="arch",
            expected=str(supported),
            actual=current,
            compatible=compatible
        )
        self.checks.append(check)
        return check

    def check_dependency_version(self, package: str, min_version: str, max_version: str = None) -> CompatibilityCheck:
        try:
            current = version(package)
            compatible = parse_version(current) >= parse_version(min_version)
            if max_version and compatible:
                compatible = parse_version(current) <= parse_version(max_version)
        except PackageNotFoundError:
            current = "NOT_INSTALLED"
            compatible = False

        check = CompatibilityCheck(
            name=f"依赖检查: {package}",
            dimension="dependency",
            expected=f">={min_version}" + (f", <={max_version}" if max_version else ""),
            actual=current,
            compatible=compatible
        )
        self.checks.append(check)
        return check

    def check_all_dependencies(self, dep_matrix: Dict[str, List[str]] = None) -> List[CompatibilityCheck]:
        if dep_matrix is None:
            dep_matrix = {
                'numpy': ['1.24.0'], 'scipy': ['1.10.0'],
                'pandas': ['2.0.0'], 'matplotlib': ['3.7.0'],
                'flask': ['2.3.0'], 'pyyaml': ['6.0'],
                'requests': ['2.31.0'], 'psutil': ['5.9.0']
            }
        
        results = []
        for pkg, versions in dep_matrix.items():
            min_ver = versions[0]
            check = self.check_dependency_version(pkg, min_ver)
            results.append(check)
        return results

    def check_api_compatibility(self, module_name: str, required_functions: List[str]) -> List[CompatibilityCheck]:
        results = []
        try:
            mod = importlib.import_module(module_name)
            for func_name in required_functions:
                exists = hasattr(mod, func_name)
                results.append(CompatibilityCheck(
                    name=f"API检查: {module_name}.{func_name}",
                    dimension="api",
                    expected="exists",
                    actual="exists" if exists else "not_found",
                    compatible=exists
                ))
        except ImportError:
            results.append(CompatibilityCheck(
                name=f"模块导入: {module_name}",
                dimension="api",
                expected="importable",
                actual="not_found",
                compatible=False
            ))
        self.checks.extend(results)
        return results

    def check_import_all_modules(self) -> List[CompatibilityCheck]:
        modules = [
            'src.algorithms.greedy_algorithm',
            'src.algorithms.genetic_algorithm',
            'src.algorithms.round_robin',
            'src.models.server',
            'src.models.task',
            'src.simulation.datacenter_simulator',
            'src.plugin.base',
            'src.plugin.manager',
            'src.plugin.simulation_plugin',
            'src.utils.config',
            'src.utils.logger',
            'src.sdk.client',
        ]
        
        results = []
        for mod_name in modules:
            try:
                importlib.import_module(mod_name)
                results.append(CompatibilityCheck(
                    name=f"导入: {mod_name}",
                    dimension="module_import",
                    expected="importable",
                    actual="importable",
                    compatible=True
                ))
            except ImportError as e:
                results.append(CompatibilityCheck(
                    name=f"导入: {mod_name}",
                    dimension="module_import",
                    expected="importable",
                    actual=f"failed: {str(e)[:80]}",
                    compatible=False
                ))
        self.checks.extend(results)
        return results

    def get_summary(self) -> Dict[str, Any]:
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c.compatible)
        failed = total - passed
        
        by_dimension = {}
        for c in self.checks:
            if c.dimension not in by_dimension:
                by_dimension[c.dimension] = {'total': 0, 'passed': 0}
            by_dimension[c.dimension]['total'] += 1
            if c.compatible:
                by_dimension[c.dimension]['passed'] += 1

        return {
            'total_checks': total,
            'passed': passed,
            'failed': failed,
            'compatibility_score': passed / max(total, 1) * 100,
            'by_dimension': by_dimension
        }

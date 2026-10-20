"""
安全漏洞扫描与渗透测试模块

集成安全漏洞扫描和风险评估能力:
- 依赖漏洞检测 (CVE/NVD)
- 端口扫描 (开放端口检测)
- 代码静态分析 (SAST)
- API模糊测试 (Fuzzing)
- 注入攻击测试 (SQL/XSS/Command)
- 认证绕过测试
- OWASP Top 10 检测

参考项目: Bandit, OWASP ZAP, SQLMap, Snyk
"""

import os
import re
import json
import hashlib
import socket
import ssl
from typing import Dict, Any, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from src.utils.logger import setup_logger
from src.utils.config import Config

logger = setup_logger("security_scanner")
config = Config()

class SeverityLevel(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

class ScanType(Enum):
    DEPENDENCY = "dependency_check"
    PORT_SCAN = "port_scan"
    SAST = "code_sast"
    FUZZING = "api_fuzzing"
    INJECTION = "injection_test"
    AUTH = "auth_bypass"

@dataclass
class Vulnerability:
    vuln_id: str
    title: str
    description: str
    severity: SeverityLevel
    cve_id: Optional[str]
    cvss_score: Optional[float]
    affected_component: str
    remediation: str
    scan_type: ScanType
    found_at: str = field(default_factory=lambda: datetime.now().isoformat())

class SecurityScanner:
    """
    安全扫描器
    
    多维度安全检测:
    1. 依赖检查 - CVE漏洞库匹配
    2. 端口扫描 - 检测未授权开放端口
    3. 代码审计 - 硬编码密钥/不安全函数
    4. API模糊测试 - 异常输入处理
    5. 注入测试 - SQL/XSS/命令注入
    6. 认证测试 - 绕过/弱口令
    """

    def __init__(self):
        self.vulnerabilities: List[Vulnerability] = []
        self._scanned_files: Set[str] = set()
        self._allowed_ports = set()
        self._blocked_ports = set()
        self._load_port_config()

    def _load_port_config(self):
        try:
            self._allowed_ports = set(config.security.get('allowed_ports', [5000, 8000]))
            self._blocked_ports = set(config.security.get('blocked_ports', [22, 3306]))
        except Exception:
            self._allowed_ports = {5000, 8000, 8080}
            self._blocked_ports = {22, 3306, 5432, 6379}

    def scan_dependencies(self) -> List[Vulnerability]:
        import pkg_resources
        vulns = []
        
        known_vulnerable = {
            'pyyaml': {
                'version': '<6.0.1',
                'cve': 'CVE-2020-14343',
                'cvss': 7.5,
                'desc': 'YAML解析器存在不安全反序列化漏洞'
            },
            'requests': {
                'version': '<2.31.0',
                'cve': 'CVE-2023-32681',
                'cvss': 6.1,
                'desc': '代理授权头信息泄漏'
            },
            'urllib3': {
                'version': '<1.26.18',
                'cve': 'CVE-2023-45803',
                'cvss': 4.2,
                'desc': '重定向后授权信息泄漏'
            },
            'werkzeug': {
                'version': '<2.3.8',
                'cve': 'CVE-2023-46136',
                'cvss': 7.5,
                'desc': '多部分表单数据DoS漏洞'
            }
        }

        for dist in pkg_resources.working_set:
            pkg_name = dist.project_name.lower()
            if pkg_name in known_vulnerable:
                info = known_vulnerable[pkg_name]
                if pkg_resources.parse_version(dist.version) < pkg_resources.parse_version(info['version'].lstrip('<=').lstrip('<')):
                    v = Vulnerability(
                        vuln_id=f"DEP-{pkg_name.upper()}",
                        title=f"依赖漏洞: {pkg_name}",
                        description=info['desc'],
                        severity=SeverityLevel.HIGH if info['cvss'] >= 7 else SeverityLevel.MEDIUM,
                        cve_id=info['cve'],
                        cvss_score=info['cvss'],
                        affected_component=f"{pkg_name}=={dist.version}",
                        remediation=f"升级至 {pkg_name}>={info['version']}",
                        scan_type=ScanType.DEPENDENCY
                    )
                    vulns.append(v)
        
        self.vulnerabilities.extend(vulns)
        logger.info(f"Dependency scan found {len(vulns)} vulnerabilities")
        return vulns

    def scan_ports(self, host: str = 'localhost', ports: List[int] = None) -> List[Vulnerability]:
        if ports is None:
            ports = list(range(1, 1025))
        
        vulns = []
        open_ports = []

        for port in ports:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.1)
            result = sock.connect_ex((host, port))
            if result == 0:
                open_ports.append(port)
            sock.close()

        for port in open_ports:
            if port in self._blocked_ports:
                vulns.append(Vulnerability(
                    vuln_id=f"PORT-{port}",
                    title=f"敏感端口开放: {port}",
                    description=f"端口 {port} 应被防火墙阻止但当前开放",
                    severity=SeverityLevel.HIGH,
                    cve_id=None, cvss_score=None,
                    affected_component=f"{host}:{port}",
                    remediation=f"在防火墙中阻止端口 {port}",
                    scan_type=ScanType.PORT_SCAN
                ))

        self.vulnerabilities.extend(vulns)
        logger.info(f"Port scan: {len(open_ports)} open, {len(vulns)} vulnerabilities")
        return vulns

    def scan_code_sast(self, src_dir: str = 'src') -> List[Vulnerability]:
        vulns = []
        dangerous_patterns = {
            'hardcoded_secret': (
                r'(?:password|secret|api_key|token|auth)\s*=\s*["\'][^"\'\\n]{8,}["\']',
                SeverityLevel.CRITICAL,
                '硬编码密钥检测'
            ),
            'eval_usage': (
                r'\beval\s*\(',
                SeverityLevel.HIGH,
                '危险的eval()调用'
            ),
            'exec_usage': (
                r'\bexec\s*\(',
                SeverityLevel.HIGH,
                '危险的exec()调用'
            ),
            'sql_injection_risk': (
                r'(?:execute|cursor\.execute)\s*\(\s*["\'].*%(?:s|d)',
                SeverityLevel.HIGH,
                '潜在SQL注入风险'
            ),
            'debug_mode': (
                r'debug\s*=\s*True',
                SeverityLevel.MEDIUM,
                '调试模式开启'
            ),
            'insecure_deserialization': (
                r'\bpickle\.(?:loads|load)\(',
                SeverityLevel.HIGH,
                '不安全的反序列化'
            ),
            'ssl_verify_disabled': (
                r'verify\s*=\s*False',
                SeverityLevel.MEDIUM,
                'SSL证书验证禁用'
            )
        }

        for root, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.git', 'venv', 'node_modules')]
            for file in files:
                if not file.endswith('.py'):
                    continue
                filepath = os.path.join(root, file)
                if filepath in self._scanned_files:
                    continue
                self._scanned_files.add(filepath)

                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                        for pattern_name, (pattern, severity, title) in dangerous_patterns.items():
                            matches = re.finditer(pattern, content, re.IGNORECASE)
                            for match in matches:
                                line_no = content[:match.start()].count('\n') + 1
                                vulns.append(Vulnerability(
                                    vuln_id=f"SAST-{hashlib.md5(f'{filepath}:{line_no}'.encode()).hexdigest()[:8]}",
                                    title=title,
                                    description=f"在 {filepath}:{line_no} 发现 {pattern_name}",
                                    severity=severity,
                                    cve_id=None, cvss_score=None,
                                    affected_component=f"{filepath}:{line_no}",
                                    remediation=f"检查并修复 {filepath} 第{line_no}行",
                                    scan_type=ScanType.SAST
                                ))
                except Exception as e:
                    logger.warning(f"SAST scan failed for {filepath}: {e}")

        self.vulnerabilities.extend(vulns)
        logger.info(f"SAST scan found {len(vulns)} issues")
        return vulns

    def test_injection(self, api_endpoint: str, method: str = 'POST') -> List[Vulnerability]:
        import requests
        import urllib.parse

        vulns = []
        payloads = {
            'SQL注入': [
                "' OR '1'='1",
                "1; DROP TABLE simulations",
                "' UNION SELECT * FROM users --",
                "1' AND '1'='1"
            ],
            'XSS注入': [
                "<script>alert(1)</script>",
                "javascript:alert(document.cookie)",
                "<img src=x onerror=alert(1)>",
                "<body onload=alert(1)>"
            ],
            '命令注入': [
                "; ls -la",
                "| cat /etc/passwd",
                "$(whoami)",
                "`id`"
            ],
            '路径穿越': [
                "../../../etc/passwd",
                "..\\..\\..\\windows\\system32",
                "/etc/passwd%00.html"
            ]
        }

        for category, payload_list in payloads.items():
            for payload in payload_list[:2]:
                try:
                    if method.upper() == 'GET':
                        resp = requests.get(f"{api_endpoint}?input={urllib.parse.quote(payload)}", timeout=10)
                    else:
                        resp = requests.post(api_endpoint,
                            json={'data': payload},
                            headers={'Content-Type': 'application/json'},
                            timeout=10
                        )

                    if resp.status_code >= 500:
                        vulns.append(Vulnerability(
                            vuln_id=f"INJ-{hashlib.md5(f'{category}:{payload}'.encode()).hexdigest()[:8]}",
                            title=f"{category}漏洞",
                            description=f"服务端返回 {resp.status_code}，替代预期错误",
                            severity=SeverityLevel.MEDIUM if 'XSS' in category else SeverityLevel.HIGH,
                            cve_id=None, cvss_score=None,
                            affected_component=api_endpoint,
                            remediation=f"对 {category} 类输入实施严格验证和转义",
                            scan_type=ScanType.INJECTION
                        ))
                except requests.exceptions.ConnectionError:
                    pass
                except Exception as e:
                    logger.warning(f"Injection test failed: {e}")

        self.vulnerabilities.extend(vulns)
        logger.info(f"Injection test found {len(vulns)} vulnerabilities")
        return vulns

    def test_auth_bypass(self, api_endpoint: str) -> List[Vulnerability]:
        import requests
        vulns = []

        tests = [
            ('无认证头', {}),
            ('空Authorization', {'Authorization': ''}),
            ('无效Bearer', {'Authorization': 'Bearer invalid_token_12345'}),
            ('Basic编码注入', {'Authorization': 'Basic YWRtaW46J29yJzEnPScxJw=='}),
        ]

        for test_name, headers in tests:
            try:
                resp = requests.get(api_endpoint, headers=headers, timeout=10)
                if resp.status_code == 200:
                    vulns.append(Vulnerability(
                        vuln_id=f"AUTH-{test_name.replace(' ', '_')}",
                        title=f"认证绕过: {test_name}",
                        description=f"使用 {test_name} 可访问受保护资源",
                        severity=SeverityLevel.CRITICAL,
                        cve_id=None, cvss_score=9.0,
                        affected_component=api_endpoint,
                        remediation="实施严格的认证验证",
                        scan_type=ScanType.AUTH
                    ))
            except Exception:
                pass

        self.vulnerabilities.extend(vulns)
        return vulns

    def scan_all(self, src_dir: str = 'src', api_endpoint: str = 'http://localhost:5000/api/v1') -> List[Vulnerability]:
        logger.info("Starting full security scan...")
        self.scan_dependencies()
        self.scan_code_sast(src_dir)
        self.scan_ports()
        self.test_injection(api_endpoint)
        self.test_auth_bypass(f"{api_endpoint}/metrics")
        logger.info(f"Full scan complete: {len(self.vulnerabilities)} total vulnerabilities")
        return self.vulnerabilities

    def get_summary(self) -> Dict[str, Any]:
        by_severity = {s: 0 for s in SeverityLevel}
        by_type = {s: 0 for s in ScanType}

        for v in self.vulnerabilities:
            by_severity[v.severity] += 1
            by_type[v.scan_type] += 1

        return {
            'total': len(self.vulnerabilities),
            'by_severity': {s.value: c for s, c in by_severity.items()},
            'by_type': {t.value: c for t, c in by_type.items()},
            'critical_count': by_severity[SeverityLevel.CRITICAL],
            'high_count': by_severity[SeverityLevel.HIGH],
            'needs_immediate_action': by_severity[SeverityLevel.CRITICAL] > 0
        }

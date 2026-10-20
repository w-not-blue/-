"""
风险评估模块

对测试结果进行综合风险评估:
- 漏洞严重性评估 (CVSS)
- 风险矩阵生成
- 影响范围分析
- 修复优先级排序

参考项目: NIST CVSS Calculator, OWASP Risk Rating
"""

from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from src.utils.logger import setup_logger
from src.utils.config import Config

logger = setup_logger("risk_assessor")
config = Config()

class RiskLevel(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    ACCEPTABLE = "acceptable"

@dataclass
class RiskItem:
    category: str
    description: str
    likelihood: float  # 0.0-1.0
    impact: float      # 0.0-1.0
    risk_score: float   # likelihood * impact
    risk_level: RiskLevel
    affected_components: List[str]
    mitigation: str

@dataclass
class RiskReport:
    report_id: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    overall_risk: RiskLevel = RiskLevel.LOW
    risk_items: List[RiskItem] = field(default_factory=list)
    safety_score: float = 100.0
    recommendations: List[str] = field(default_factory=list)

class RiskAssessor:
    """
    风险评估器
    
    综合评估系统安全风险:
    1. 漏洞风险等级评定
    2. 影响范围分析
    3. 风险矩阵可视化
    4. 修复建议排序
    """

    def __init__(self):
        self.risks: List[RiskItem] = []
        self._cvss_threshold = 7.0

    def assess_from_security_scan(self, vulnerabilities: List[Any]) -> List[RiskItem]:
        severity_to_likelihood = {
            'critical': 0.9,
            'high': 0.7,
            'medium': 0.4,
            'low': 0.2,
            'info': 0.1
        }
        severity_to_impact = {
            'critical': 0.95,
            'high': 0.8,
            'medium': 0.5,
            'low': 0.3,
            'info': 0.1
        }

        for vuln in vulnerabilities:
            severity = getattr(vuln, 'severity', None)
            if severity:
                sev_str = severity.value if hasattr(severity, 'value') else str(severity)
                likelihood = severity_to_likelihood.get(sev_str, 0.5)
                impact = severity_to_impact.get(sev_str, 0.5)
            else:
                likelihood = 0.5
                impact = 0.5

            score = likelihood * impact
            level = self._score_to_level(score)

            risk = RiskItem(
                category=getattr(vuln, 'scan_type', 'unknown').value if hasattr(vuln.scan_type, 'value') else str(getattr(vuln, 'scan_type', 'unknown')),
                description=getattr(vuln, 'title', 'Unknown'),
                likelihood=likelihood,
                impact=impact,
                risk_score=score,
                risk_level=level,
                affected_components=[getattr(vuln, 'affected_component', 'unknown')],
                mitigation=getattr(vuln, 'remediation', 'No mitigation available')
            )
            self.risks.append(risk)

        return self.risks

    def assess_from_stress_test(self, metrics) -> RiskItem:
        violations = getattr(metrics, 'threshold_violations', [])
        error_rate = 1.0 - getattr(metrics, 'success_rate', 1.0)
        peak_cpu = getattr(metrics, 'peak_cpu_pct', 0)

        if violations:
            likelihood = min(1.0, error_rate * 10 + peak_cpu / 200)
            impact = 0.7
            score = likelihood * impact
            level = self._score_to_level(score)
        else:
            return RiskItem(
                category='performance',
                description='系统性能在可接受范围内',
                likelihood=0.1, impact=0.2, risk_score=0.02,
                risk_level=RiskLevel.ACCEPTABLE,
                affected_components=['simulation_engine'],
                mitigation='无需处理'
            )

        risk = RiskItem(
            category='performance',
            description=f"系统超阈值: {len(violations)}项",
            likelihood=likelihood,
            impact=impact,
            risk_score=score,
            risk_level=level,
            affected_components=['simulation_engine', 'api_server'],
            mitigation='优化算法性能，增加资源分配'
        )
        self.risks.append(risk)
        return risk

    def assess_from_compatibility(self, compat_summary: Dict[str, Any]) -> RiskItem:
        score_pct = compat_summary.get('compatibility_score', 100)

        if score_pct >= 95:
            level = RiskLevel.ACCEPTABLE
            likelihood, impact = 0.05, 0.1
        elif score_pct >= 80:
            level = RiskLevel.LOW
            likelihood, impact = 0.2, 0.3
        elif score_pct >= 60:
            level = RiskLevel.MEDIUM
            likelihood, impact = 0.4, 0.5
        else:
            level = RiskLevel.HIGH
            likelihood, impact = 0.7, 0.8

        score = likelihood * impact
        risk = RiskItem(
            category='compatibility',
            description=f"兼容性评分: {score_pct:.1f}%",
            likelihood=likelihood,
            impact=impact,
            risk_score=score,
            risk_level=level,
            affected_components=['all_modules'],
            mitigation='修复兼容性问题，更新依赖版本'
        )
        self.risks.append(risk)
        return risk

    def _score_to_level(self, score: float) -> RiskLevel:
        if score >= 0.8:
            return RiskLevel.CRITICAL
        elif score >= 0.6:
            return RiskLevel.HIGH
        elif score >= 0.3:
            return RiskLevel.MEDIUM
        elif score >= 0.1:
            return RiskLevel.LOW
        return RiskLevel.ACCEPTABLE

    def generate_report(self) -> RiskReport:
        if not self.risks:
            return RiskReport(
                report_id=f"RISK-{datetime.now().strftime('%Y%m%d%H%M')}",
                overall_risk=RiskLevel.LOW,
                safety_score=100.0
            )

        by_level = {level: 0 for level in RiskLevel}
        total_score = 0.0

        for r in self.risks:
            by_level[r.risk_level] += 1
            total_score += r.risk_score

        if by_level[RiskLevel.CRITICAL] > 0:
            overall = RiskLevel.CRITICAL
        elif by_level[RiskLevel.HIGH] > 0:
            overall = RiskLevel.HIGH
        elif by_level[RiskLevel.MEDIUM] > 2:
            overall = RiskLevel.MEDIUM
        elif by_level[RiskLevel.MEDIUM] > 0:
            overall = RiskLevel.LOW
        else:
            overall = RiskLevel.ACCEPTABLE

        safety_score = max(0.0, 100.0 - total_score * 100 / max(len(self.risks), 1))

        recommendations = []
        for r in self.risks:
            if r.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH):
                recommendations.insert(0, f"[紧急] {r.description}: {r.mitigation}")
            elif r.risk_level == RiskLevel.MEDIUM:
                recommendations.append(f"[建议] {r.description}: {r.mitigation}")

        report = RiskReport(
            report_id=f"RISK-{datetime.now().strftime('%Y%m%d%H%M')}",
            overall_risk=overall,
            risk_items=self.risks,
            safety_score=safety_score,
            recommendations=recommendations
        )

        logger.info(f"Risk report generated: {overall.value}, safety_score={safety_score:.1f}")
        return report

"""
安全评估报告生成器

生成符合行业标准的安全评估报告:
- NIST CVSS评分
- OWASP Top 10对照
- 修复优先级排序
"""

import json
import os
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass

@dataclass
class SecurityReport:
    report_id: str
    timestamp: str
    scan_summary: Dict[str, Any]
    vulnerability_list: List[Dict[str, Any]]
    risk_assessment: Dict[str, Any]
    compliance_checklist: Dict[str, Any]
    recommendations: List[Dict[str, Any]]


def generate_security_report(
    vulnerabilities: List[Any],
    scan_summary: Dict[str, Any],
    risk_assessment: Dict[str, Any],
    output_path: str = None
) -> str:
    """
    生成安全评估报告
    
    Args:
        vulnerabilities: 漏洞列表
        scan_summary: 扫描摘要
        risk_assessment: 风险评估
        output_path: 输出路径
    
    Returns:
        报告文件路径
    """
    
    report = {
        'meta': {
            'report_id': f"SEC-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
            'timestamp': datetime.now().isoformat(),
            'generator': 'DataCenter-Scheduler Security Scanner',
            'version': '1.0.0'
        },
        'executive_summary': {
            'total_vulnerabilities': scan_summary.get('total', 0),
            'critical': scan_summary.get('critical_count', 0),
            'high': scan_summary.get('high_count', 0),
            'medium': scan_summary.get('by_severity', {}).get('medium', 0),
            'low': scan_summary.get('by_severity', {}).get('low', 0),
            'risk_level': risk_assessment.get('overall_risk', 'unknown'),
            'safety_score': risk_assessment.get('safety_score', 0),
            'needs_immediate_action': scan_summary.get('critical_count', 0) > 0
        },
        'vulnerability_details': [],
        'scan_coverage': {
            'dependency_check': scan_summary.get('by_type', {}).get('dependency_check', 0) > 0,
            'port_scan': scan_summary.get('by_type', {}).get('port_scan', 0) > 0,
            'code_sast': scan_summary.get('by_type', {}).get('code_sast', 0) > 0,
            'api_fuzzing': scan_summary.get('by_type', {}).get('api_fuzzing', 0) > 0,
            'injection_test': scan_summary.get('by_type', {}).get('injection_test', 0) > 0,
            'auth_bypass': scan_summary.get('by_type', {}).get('auth_bypass', 0) > 0
        },
        'owasp_top10_checklist': {
            'A01_Broken_Access_Control': scan_summary.get('by_type', {}).get('auth_bypass', 0) > 0,
            'A02_Cryptographic_Failures': False,
            'A03_Injection': scan_summary.get('by_type', {}).get('injection_test', 0) > 0,
            'A05_Security_Misconfiguration': scan_summary.get('by_type', {}).get('port_scan', 0) > 0,
            'A06_Vulnerable_Components': scan_summary.get('by_type', {}).get('dependency_check', 0) > 0,
            'A08_Software_Integrity_Failures': scan_summary.get('by_type', {}).get('code_sast', 0) > 0,
        },
        'recommendations': risk_assessment.get('recommendations', []),
        'remediation_plan': [
            {
                'priority': 'P0-紧急',
                'condition': '存在CRITICAL级别漏洞',
                'action': '立即停止部署，修复所有严重漏洞',
                'timeline': '24小时内'
            },
            {
                'priority': 'P1-高',
                'condition': '存在HIGH级别漏洞',
                'action': '制定修复计划，在下一版本中修复',
                'timeline': '1周内'
            },
            {
                'priority': 'P2-中',
                'condition': '存在MEDIUM级别漏洞',
                'action': '纳入迭代修复计划',
                'timeline': '2周内'
            },
            {
                'priority': 'P3-低',
                'condition': '安全评分<80',
                'action': '持续优化安全配置',
                'timeline': '持续'
            }
        ],
        'sign_off': {
            'scanner': 'DataCenter Scheduler Security Scanner v1.0',
            'scanned_by': 'Automated Pipeline',
            'review_required': scan_summary.get('critical_count', 0) > 0 or scan_summary.get('high_count', 0) > 2
        }
    }

    # 填充漏洞详情
    for vuln in vulnerabilities:
        report['vulnerability_details'].append({
            'id': getattr(vuln, 'vuln_id', ''),
            'title': getattr(vuln, 'title', ''),
            'description': getattr(vuln, 'description', ''),
            'severity': getattr(vuln, 'severity', 'unknown').value if hasattr(getattr(vuln, 'severity', None), 'value') else str(getattr(vuln, 'severity', 'unknown')),
            'cve_id': getattr(vuln, 'cve_id', None),
            'cvss_score': getattr(vuln, 'cvss_score', None),
            'affected_component': getattr(vuln, 'affected_component', ''),
            'remediation': getattr(vuln, 'remediation', '')
        })

    # 写入文件
    if output_path is None:
        os.makedirs('test_reports', exist_ok=True)
        output_path = f"test_reports/security_report_{report['meta']['report_id']}.json"
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    
    return output_path


def print_report_summary(report_data: Dict[str, Any]) -> None:
    """打印报告摘要"""
    import logging
    logger = logging.getLogger("security_report")
    
    exec_summary = report_data.get('executive_summary', {})
    sign_off = report_data.get('sign_off', {})
    recs = report_data.get('recommendations', [])

    logger.info("=" * 60)
    logger.info("  安全评估报告")
    logger.info("=" * 60)
    logger.info(f"  报告ID:     {report_data.get('meta', {}).get('report_id', 'N/A')}")
    logger.info(f"  扫描时间:   {report_data.get('meta', {}).get('timestamp', 'N/A')}")
    logger.info(f"  漏洞总数:   {exec_summary.get('total_vulnerabilities', 0)}")
    logger.info(f"  严重/高:    {exec_summary.get('critical', 0)}/{exec_summary.get('high', 0)}")
    logger.info(f"  风险等级:   {exec_summary.get('risk_level', 'unknown')}")
    logger.info(f"  安全评分:   {exec_summary.get('safety_score', 0):.1f}/100")
    logger.info(f"  需审查:     {'是 ⚠' if sign_off.get('review_required') else '否 ✓'}")
    
    if recs:
        logger.info("  修复建议:")
        for rec in recs[:5]:
            logger.info(f"    - {rec}")

    logger.info("=" * 60)

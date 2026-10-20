"""
测试环境一键运行器

集成所有测试模块，提供统一的测试入口:
1. 环境检测 + 初始化
2. 兼容性测试 (跨版本/跨平台)
3. 压力测试 (CPU/内存/并发)
4. 安全扫描 (依赖/端口/SAST/注入)
5. 风险评估 + 报告生成
"""

import sys
import json
import time
import os
from datetime import datetime
from typing import Dict, Any, List
from src.utils.logger import setup_logger
from src.utils.config import Config
from src.testing import (
    CompatibilityTester, StressTester, SecurityScanner,
    RiskAssessor, EnvironmentManager
)

logger = setup_logger("test_runner")
config = Config()

def run_full_test_suite(output_file: str = None) -> Dict[str, Any]:
    """
    运行完整测试套件
    
    流程:
    1. 环境初始化 + 快照
    2. 兼容性测试
    3. 压力测试
    4. 安全扫描
    5. 风险评估
    6. 报告生成
    7. 环境清理
    """
    
    start_time = time.time()
    results = {
        'run_id': datetime.now().strftime('%Y%m%d_%H%M%S'),
        'timestamp': datetime.now().isoformat(),
        'summary': {},
        'compatibility': {},
        'stress': {},
        'security': {},
        'risk': None,
        'duration_sec': 0
    }
    
    logger.info("=" * 60)
    logger.info("开始仿真模拟测试环境 - 完整测试套件")
    logger.info("=" * 60)
    
    env_mgr = EnvironmentManager()
    env_mgr.save_state('test_start', datetime.now().isoformat())
    env_mgr.create_snapshot("测试执行前快照")

    try:
        # 1. 兼容性测试
        logger.info("\n[1/5] 兼容性测试...")
        ct = CompatibilityTester()
        ct.check_python_version("3.9", "3.12")
        ct.check_os_compatibility()
        ct.check_platform_arch()
        ct.check_all_dependencies()
        ct.check_import_all_modules()
        results['compatibility'] = ct.get_summary()
        logger.info(f"  兼容性评分: {results['compatibility']['compatibility_score']:.1f}%")

        # 2. 压力测试
        logger.info("\n[2/5] 压力测试...")
        st = StressTester()
        cpu_metrics = st.run_cpu_stress(num_threads=2, duration_sec=5)
        st.check_thresholds(cpu_metrics)
        results['stress'] = st.get_summary()
        logger.info(f"  最高CPU使用率: {results['stress']['max_cpu_pct']:.1f}%")

        # 3. 安全扫描
        logger.info("\n[3/5] 安全扫描...")
        ss = SecurityScanner()
        ss.scan_dependencies()
        ss.scan_code_sast('src')
        ss.scan_ports('localhost', [5000, 5432, 6379, 8080])
        results['security'] = ss.get_summary()
        logger.info(f"  发现漏洞数: {results['security']['total']}")
        logger.info(f"  严重: {results['security']['critical_count']} / 高: {results['security']['high_count']}")

        # 4. 风险评估
        logger.info("\n[4/5] 风险评估...")
        ra = RiskAssessor()
        ra.assess_from_security_scan(ss.vulnerabilities)
        ra.assess_from_stress_test(cpu_metrics)
        report = ra.generate_report()
        results['risk'] = {
            'overall_risk': report.overall_risk.value,
            'safety_score': report.safety_score,
            'recommendations': report.recommendations
        }
        logger.info(f"  整体风险等级: {report.overall_risk.value}")
        logger.info(f"  安全评分: {report.safety_score:.1f}/100")

        # 5. 汇总
        logger.info("\n[5/5] 生成测试报告...")
        results['summary'] = {
            'compatibility_score': results['compatibility']['compatibility_score'],
            'stress_tests_passed': results['stress']['total_tests'],
            'security_vulnerabilities': results['security']['total'],
            'critical_vulnerabilities': results['security']['critical_count'],
            'risk_level': results['risk']['overall_risk'],
            'safety_score': results['risk']['safety_score']
        }

        results['duration_sec'] = time.time() - start_time
        logger.info(f"\n测试完成! 耗时: {results['duration_sec']:.1f}s")

        # 生成报告文件
        if output_file is None:
            os.makedirs('test_reports', exist_ok=True)
            output_file = f"test_reports/test_report_{results['run_id']}.json"

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)

        logger.info(f"报告已保存至: {output_file}")

    except Exception as e:
        logger.error(f"测试执行异常: {e}", exc_info=True)
        results['error'] = str(e)

    finally:
        env_mgr.create_snapshot("测试执行后快照")
        env_mgr.cleanup_snapshots(keep_last=5)

    _print_report(results)
    return results


def _print_report(results: Dict[str, Any]) -> None:
    logger.info("=" * 60)
    logger.info("  测试环境执行报告")
    logger.info("=" * 60)
    logger.info(f"  执行ID:      {results['run_id']}")
    logger.info(f"  耗时:        {results['duration_sec']:.1f}s")
    logger.info(f"  兼容性评分:  {results['compatibility'].get('compatibility_score', 0):.1f}%")
    logger.info(f"  安全漏洞:    {results['security'].get('total', 0)} ({results['security'].get('critical_count', 0)} 严重)")
    logger.info(f"  风险等级:    {results['risk'].get('overall_risk', 'N/A')}")
    logger.info(f"  安全评分:    {results['risk'].get('safety_score', 0):.1f}/100")
    logger.info("=" * 60)

    if results.get('summary', {}).get('risk_level') == 'critical':
        logger.warning("⚠ 发现严重风险！请立即处理以下问题:")
        for rec in results['risk'].get('recommendations', []):
            logger.warning(f"    - {rec}")
    elif results.get('summary', {}).get('safety_score', 100) >= 80:
        logger.info("✓ 系统安全状况良好")


if __name__ == '__main__':
    run_full_test_suite()

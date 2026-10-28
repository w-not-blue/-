"""
NSGA2 / MOEAD 插件单元测试

覆盖:
    1. manifest 字段完整性
    2. initialize 成功路径
    3. execute 调度结果结构
    4. 依赖缺失降级（mock）

运行: pytest tests/plugins/test_pymoo_plugin.py -v
"""

import os
import sys
import unittest

# Make project importable
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.plugin.protocol import PluginManifest, PluginResult  # noqa: E402
from src.plugins._deps import has_pymoo  # noqa: E402


class TestNSGA2Manifest(unittest.TestCase):
    """测试 NSGA2 manifest 完整性"""

    def setUp(self):
        if not has_pymoo():
            self.skipTest("pymoo 未安装，跳过")
        from src.plugins.nsga2_scheduler import NSGA2SchedulerPlugin
        self.plugin = NSGA2SchedulerPlugin()
        self.manifest = self.plugin.get_manifest()

    def test_manifest_is_plugin_manifest(self):
        self.assertIsInstance(self.manifest, PluginManifest)

    def test_manifest_required_fields(self):
        self.assertEqual(self.manifest.name, "NSGA2SchedulerPlugin")
        self.assertEqual(self.manifest.version, "1.0.0")
        self.assertIn("schedule", self.manifest.supported_operations)

    def test_manifest_input_schema_keys(self):
        schema = self.manifest.input_schema
        for key in ["pop_size", "n_gen", "selection", "seed"]:
            self.assertIn(key, schema, f"缺少 input_schema 字段: {key}")


class TestNSGA2Initialize(unittest.TestCase):
    """测试 NSGA2 initialize 路径"""

    def setUp(self):
        if not has_pymoo():
            self.skipTest("pymoo 未安装，跳过")
        from src.plugins.nsga2_scheduler import NSGA2SchedulerPlugin
        self.plugin = NSGA2SchedulerPlugin()

    def test_initialize_default_config(self):
        ok = self.plugin.initialize({})
        self.assertTrue(ok, "默认配置应能成功初始化")

    def test_initialize_invalid_selection(self):
        ok = self.plugin.initialize({"selection": "INVALID"})
        self.assertFalse(ok, "非法 selection 应失败")


class TestNSGA2Execute(unittest.TestCase):
    """测试 NSGA2 execute 调度"""

    def setUp(self):
        if not has_pymoo():
            self.skipTest("pymoo 未安装，跳过")
        from src.plugins.nsga2_scheduler import NSGA2SchedulerPlugin
        self.plugin = NSGA2SchedulerPlugin()
        self.plugin.initialize({"pop_size": 20, "n_gen": 30, "seed": 42})

        # 构造仿真器 + 小任务集
        from src.simulation import DataCenterSimulator
        sim = DataCenterSimulator(num_servers=5, simulation_hours=2, seed=42)
        self.servers = sim.servers
        from src.models import Task
        self.tasks = [
            Task(load=5, priority=1, deadline=10, workload_class="cpu_bound"),
            Task(load=3, priority=2, deadline=5, workload_class="memory_bound"),
            Task(load=7, priority=1, deadline=None, workload_class="io_bound"),
        ]

    def test_execute_returns_valid_assignments(self):
        result = self.plugin.execute(tasks=self.tasks, servers=self.servers)
        self.assertIsInstance(result, PluginResult)
        self.assertTrue(result.success, f"NSGA2 应成功: {result.message}")
        self.assertIn("assignments", result.data)
        self.assertEqual(len(result.data["assignments"]), len(self.tasks))

    def test_execute_missing_params(self):
        result = self.plugin.execute()
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "MISSING_PARAM")


class TestDependencyMissingFallback(unittest.TestCase):
    """测试依赖缺失时优雅降级"""

    def test_dep_check_returns_dict(self):
        from src.plugins._deps import get_dependency_status
        status = get_dependency_status()
        self.assertIsInstance(status, dict)
        for key in ["gymnasium", "stable_baselines3", "pymoo"]:
            self.assertIn(key, status)
            self.assertIn("available", status[key])
            self.assertIn("install_hint", status[key])


class TestMOEADManifest(unittest.TestCase):
    """测试 MOEAD manifest 完整性（与 NSGA2 镜像）"""

    def setUp(self):
        if not has_pymoo():
            self.skipTest("pymoo 未安装，跳过")
        from src.plugins.moead_scheduler import MOEADSchedulerPlugin
        self.plugin = MOEADSchedulerPlugin()

    def test_manifest_name_and_ops(self):
        m = self.plugin.get_manifest()
        self.assertEqual(m.name, "MOEADSchedulerPlugin")
        self.assertIn("schedule", m.supported_operations)

    def test_initialize_default(self):
        ok = self.plugin.initialize({})
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()

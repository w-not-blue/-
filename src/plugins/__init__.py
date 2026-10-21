"""
GitHub Skills 第三方插件包 — 通过 PluginManager 自动发现

提供从 GitHub 开源项目接入的算力调度增强算法，全部以 PluginBase 插件形式
通过 ``PluginManager.discover_plugins(plugin_dir="src/plugins")`` 自动注册。
对未安装的可选依赖（Gymnasium / Stable Baselines3 / pymoo）优雅降级。

已启用:
    - ``nsga2_scheduler``     : pymoo NSGA2 多目标调度器
        (https://github.com/anyoptimization/pymoo, Apache 2.0)
    - ``moead_scheduler``     : pymoo MOEAD 分解多目标调度器
        (https://github.com/anyoptimization/pymoo, Apache 2.0)

预留:
    - ``datacenter_gym_env`` / ``rl_ppo_scheduler`` : 强化学习接口
      依赖 Gymnasium / Stable Baselines3 与训练模型，默认禁用

使用示例::

    from src.plugin import PluginManager
    pm = PluginManager()
    n = pm.discover_plugins(plugin_dir="src/plugins")
    pm.load_plugin("NSGA2SchedulerPlugin", config={"pop_size": 50, "n_gen": 100})
    result = pm.execute_plugin("NSGA2SchedulerPlugin", tasks=tasks, servers=servers)
"""

__all__ = [
    "_deps",
    "datacenter_gym_env",
    "rl_ppo_scheduler",
    "nsga2_scheduler",
    "moead_scheduler",
]

__version__ = "1.0.0"

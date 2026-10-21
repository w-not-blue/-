# Third-Party Plugins (`src/plugins/`)

> GitHub Skills 集成 — 通过 `PluginManager.discover_plugins('plugins')` 自动注册

本目录存放从 GitHub 开源项目接入的算力调度增强算法。**不修改核心代码**，
通过 `PluginBase` v2.0 接口热插拔，对未安装的可选依赖优雅降级。

## 已集成

| 文件 | 来源仓库 | 用途 | 依赖 |
|---|---|---|---|
| `datacenter_gym_env.py` | [Farama-Foundation/Gymnasium](https://github.com/Farama-Foundation/Gymnasium) | 把 DataCenterSimulator 包装为 `gymnasium.Env` | `gymnasium` |
| `rl_ppo_scheduler.py` | [DLR-RM/stable-baselines3](https://github.com/DLR-RM/stable-baselines3) | PPO 强化学习调度器（训练/推理/保存） | `stable-baselines3` + `gymnasium` |
| `nsga2_scheduler.py` | [anyoptimization/pymoo](https://github.com/anyoptimization/pymoo) | NSGA2 多目标调度（能耗+温度+deadline） | `pymoo` |
| `moead_scheduler.py` | [anyoptimization/pymoo](https://github.com/anyoptimization/pymoo) | MOEAD 分解多目标调度（权重向量偏好） | `pymoo` |
| `_deps.py` | 本地 | 延迟导入与可用性探测 | — |

## 安装（可选）

```bash
# 全部安装
pip install gymnasium stable-baselines3 pymoo

# 或按需
pip install pymoo  # 仅 NSGA2/MOEAD
pip install stable-baselines3  # 包含 gymnasium
```

## 使用方法

### Python 编程方式
```python
from src.plugin import PluginManager
pm = PluginManager()
pm.discover_plugins(plugin_dir="src/plugins")  # 自动扫描 src/plugins/*.py
print(pm.list_algorithms())  # 查看所有算法

pm.load_plugin("RLSchedulerPlugin", config={"num_servers": 10})
result = pm.execute_plugin(
    "RLSchedulerPlugin",
    algorithms=["rl_ppo"],
    train=True,
    total_timesteps=5000,
    model_path="data/policies/ppo.zip"
)
print(result.data)  # {"model_path": "data/policies/ppo.zip", ...}
```

### CLI
```bash
python -m src.cli.main train --algo ppo --timesteps 5000 --save-path data/policies/ppo.zip
```

### SDK
```python
from src.sdk.client import DataCenterSimulatorSDK
sdk = DataCenterSimulatorSDK(mode='local')
sdk.load_plugin("RLSchedulerPlugin", {"num_servers": 10})
result = sdk.run_simulation(algorithms=["rl_ppo"], num_servers=10, simulation_hours=24)
```

### REST API
```bash
curl http://localhost:5000/api/v1/plugins
curl -X POST http://localhost:5000/api/v1/plugins/RLSchedulerPlugin/load \
     -H "Content-Type: application/json" \
     -d '{"num_servers": 10}'
```

## 降级策略

未安装可选依赖时，`load_plugin` 会返回 `PluginResult.fail(error_code="DEPENDENCY_MISSING")`，
不会崩溃。可通过 `get_dependency_status()` 检查当前环境：
```python
from src.plugins._deps import get_dependency_status
print(get_dependency_status())
# {"gymnasium": {"available": true, "version": "1.0.0", ...}, ...}
```

## 如何贡献新算法插件

1. 在本目录新建 `my_algo_scheduler.py`
2. 继承 `PluginBase` 并实现 `get_manifest / initialize / execute / cleanup / validate_config`
3. `PluginManager.discover_plugins('plugins')` 会自动发现
4. 在 `src/cli/main.py` 的 `list_algorithms` 与 `src/api/app.py` 的 `/api/v1/algorithms`
   中追加算法描述（可选）
5. 编写测试到 `tests/plugins/`

参考实现: `nsga2_scheduler.py`（约 200 行，含完整元数据与降级处理）。

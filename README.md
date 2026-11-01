# 校园数据中心智能散热与算力调度系统

把地区气候、任务负载、服务器散热与制冷能耗放进同一套可复现的**受控仿真**里，让六种调度算法在同一份任务流上比较能耗、温度、PUE 和服务率。

> 这不是学校机房的实时数字孪生。系统用公开数据和本地数据目录做算法对比，页面上常驻一段"运行口径"提示，写明数据来源与场景指纹。

---

## 一、这个系统解决什么问题

1. **调度靠经验，缺少可比的对照。** 不同论文里的算法各自用不同任务流、不同规模，放在一起说"谁更好"没有意义。这里让所有算法重放同一份任务流快照、用同一个随机种子（默认 42）。
2. **数据分散。** 温度、功率、PUE、任务完成情况散在不同报表里，高温告警时要来回翻查。
3. **"省电"结论不可信。** 如果各算法自己生成任务流、只看总能耗排名，接活最少的算法反而最省电。所以排名先卡 98% 服务率，再比单位服务负载能耗。

## 二、功能一览

| 页面 | 做什么 |
|---|---|
| 实时仪表盘 `/` | 四个跨算法指标、六张图、指标对比表、受控排名 |
| 数据分析 | 能耗 / 温度 / 负载 / PUE 趋势 + 算法综合性能雷达（本轮相对分） |
| AI 情景预测 | 按当前站点与配置推演未来 6/12/24/48 小时 |
| 分布图 `/map.html` | 2,919 所学校 / 345 城 / 31 省，四个图层，城市详情与对比 |
| AI 诊断与寻优 | 阈值诊断、8 组 DAMO 权重快速调参、两阶段全局寻优（Pareto 候选） |
| 仿真控制 | 服务器数、时长、并行度、运行模式、城市、学校、算法、服务器类型 |
| 插件管理 | NSGA-II / MOEA-D 以插件接入，放入 `src/plugins/` 即被发现 |
| 个人中心 / 语音转文字 / 应用下载 | 账号信息与改密；浏览器 Web Speech API；五平台下载入口 |

## 三、技术架构

六层，从上到下：

```
表现层    Web 控制台（Bootstrap + Chart.js + ECharts，全部本地内置）
接口层    Flask REST /api/v1/*（鉴权、运行、指标、预测、寻优、地图、下载、SSE）
业务层    调度 / 仿真 / 可视化引擎 + 单线程运行队列 + 历史对比
算法层    贪心 / 轮询 / 遗传 / DAMO-ETS + 插件式 NSGA-II / MOEA-D
数据层    data/catalog.db（学校、城市、气温、碳因子、任务流快照）+ data/runs/<run_id>/
支撑层    配置 config/default.yaml、日志、缓存、统计
```

关键目录：

```
src/algorithms/     调度算法与插件适配（plugin_adapter.py 配置搜索预算）
src/simulation/     引擎 engine.py、指标与排名 metrics.py
src/models/         功率与温度模型 energy_model.py、server.py
src/services/       ai_service.py（大模型与规则回退）、weight_tuner.py、plan_optimizer.py
src/api/            app.py、auth_api.py、simulation_api.py …
src/data/           catalog 读取层与合成基线回退
src/plugins/        nsga2_scheduler.py、moead_scheduler.py
frontend/           index.html、app.js、map.js、assistant.js、theme.css
tools/etl/          公开数据整备成 catalog.db
tests/              pytest 用例
data/               catalog.db、运行结果、账号库
```

## 四、安装与运行

### 方式 A：Windows 安装包（推荐给非开发环境）

1. 双击 `CampusDataCenterScheduler_Setup_v1.0.0.exe`（或运行 `一键安装.bat` 走菜单）
2. 选安装路径，等待依赖就位
3. 从开始菜单启动，浏览器打开 `http://127.0.0.1:5000`
4. 默认账号 `yumo` / `123456`（建议首次登录后改密）

卸载：`setup/uninstall.bat`，会清理程序文件、快捷方式与注册表项。

### 方式 B：源码运行

```bash
# 1) Python 3.11+（推荐 3.12）
python -V

# 2) 依赖
pip install -r requirements.txt

# 3) 启动（工作目录必须是本仓库根目录）
python -m src.api.app
# 或 Windows 双击 bin/start.bat（会先检查 Python 版本与依赖）

# 4) 打开
http://127.0.0.1:5000
```

健康检查：`http://127.0.0.1:5000/health` 应返回
`{"service": "datacenter-simulation-api", "status": "healthy"}`

### 方式 C：便携包

解压后双击 `CampusDataCenterScheduler.exe`，无需安装。

## 五、环境变量

复制 `.env.example` 为 `.env`，或直接设系统环境变量。**不配置也能跑**，只是 AI 助手会走本地规则模式。

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 空 | 为空则用本地规则，页面上会显示"本地专家规则 · 未配置 API Key" |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 对话模型 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 换供应商只改这里 |
| `DEEPSEEK_TIMEOUT` | `90` | 秒 |
| `DEEPSEEK_MAX_TOKENS` | `4096` | 输出上限 |

Key 的读取顺序：环境变量 → `config/deepseek.key`（该文件不随安装包分发，默认空）。

## 六、数据说明

| 数据 | 来源 | 规模 |
|---|---|---|
| 高校名单与城市 | 教育部《全国高等学校名单（2025）》 | 2,919 所 / 345 城 / 31 省 |
| 城市气温 | Open-Meteo / ECMWF ERA5 逐日 → 逐时展开 | 345 城 × 365 天，125,925 行 |
| 电力碳因子 | 生态环境部 2021 年电力二氧化碳排放因子 | 全国 0.5568 kgCO₂/kWh，按七大区域切换 |
| 任务流 | Alibaba cluster-trace-gpu-v2025 第 19–25 天 | 6,386 个事件，按 0.3 缩放到 48 台 |

原始快照在 `data/raw/`（不随仓库分发），由 `tools/etl/` 整备成 `data/catalog.db`（8 张表，含 `meta_data_source` 记录发布方、链接、抓取时间与 SHA256）。目录库不可用时自动回退到合成基线并在页面标注，不会静默伪装。

## 七、默认参数（`config/default.yaml`）

```
服务器 48 台 · 时长 24 小时 · 随机种子 42 · SLA 松弛 1.5
基础功率 100 W · 满载 300 W · 空闲功率系数 0.6
COP = 2.5 + 0.1 × max(0, 22 − 环境温度)
部分负载惩罚 0.5 · 高温修正参考温度 35 ℃
服务率门槛 0.98
```

## 八、测试

```bash
python -m pytest tests -q
```

当前结果：**70 项通过，3 项跳过**（跳过项与 RL-PPO 依赖 `stable-baselines3` 相关）。浏览器端到端 37 项通过、页面运行错误 0 条。

## 九、团队信息

| 项 | 内容 |
|---|---|
| 队伍名称 | 【待填写】 |
| 成员 | 【待填写】 |
| 学校 / 学院 | 【待填写】 |
| 指导教师 | 【待填写】 |
| 联系方式 | 【待填写】 |

## 十、许可与致谢

课程/竞赛用途。使用的公开数据版权归原始发布方所有，本仓库只分发派生统计量与引用信息，不分发原始数据。

# 数据接入操作手册（tools/etl）

本目录把"外部公开数据"变成仿真可读的 `data/catalog.db`。

设计原则只有一条：**没有出处的数据不允许进库**。
每个 CSV 必须配一个同名的 `.json` sidecar，写明发布方、链接、授权与抓取时间。
`build_catalog.py` 遇到没有 sidecar 的文件会跳过并提示。

---

## 1. 目录结构

```
data/raw/
├── ambient/      环境温度逐时序列
├── trace/        任务负载轨迹（也可放合成/派生任务流）
├── standards/    能效标准参照值（PUE 等级线）
├── emission/     碳排放因子
└── server_type/  机架规格标定
```

文件命名规则：`<名称>.csv` + `<名称>.json`。名称随意，但两侧必须一致。

---

## 2. 常见数据从哪来

| 类别 | 建议来源 | 获取方式 | 注意 |
|---|---|---|---|
| `ambient` | 国家气象科学数据中心（中国气象数据网） | 注册后下载地面站逐小时观测（气温/湿度/风速） | 一般不允许再分发原始数据，只把派生的逐时序列入库并保留出处 |
| `trace` | 公开集群轨迹（如阿里云集群追踪数据、Google Cluster Data、Azure Public Dataset） | 下载原始文件后用 `--scale-keep` 降采样 | **不是国家数据**，必须如实标注发布方；原始文件不要进分发包 |
| `standards` | 数据中心能效国家标准 | 按标准正文填入 PUE 等级值 | 数值必须核对原文，不引用未核实的数字 |
| `emission` | 官方发布的区域电网平均碳排放因子 | 按年度公布值填写 | 注意分区域、分年度 |
| `server_type` | 行业白皮书 / 厂商公开规格 | 整理成模板格式 | 注明是哪一年、哪类机型 |
| `schools` | 教育部全国高等学校名单 | 由 `import_schools.py` 生成 | 含名称/省份/城市/层次 |
| `climate` | 各城市逐日气温（ERA5） | 由 `import_schools.py` 生成 | 345 城 × 365 天，仿真时展开为逐时 |

> 表格里的**具体接口地址、授权范围、是否收费**请在使用前到官网逐条核对。
> 本文档只给数据类别与字段格式，不代替官方说明。

---

## 3. 字段格式

### 3.1 ambient（环境温度）

```csv
ts,temp_c,humidity,wind_ms
2026-01-01T00:00:00,4.8,44,2.1
2026-01-01T01:00:00,4.5,46,1.8
```

```json
{
  "source_id": "cma-ambient-2026",
  "name": "某站点 2026 年逐小时气温",
  "publisher": "国家气象科学数据中心",
  "url": "（填写实际下载页地址）",
  "license": "（填写实际授权条款）",
  "retrieved_at": "2026-10-15",
  "site_id": "campus-a",
  "note": "演示用片段"
}
```

`site_id` 会写进数据库，仿真侧用 `data_source.site_id` 选择。

### 3.2 trace（任务负载）

必填列 `load_units`，时间列三选一（优先级从高到低）：

| 列名 | 含义 |
|---|---|
| `hour` | 已经是"第几小时"的桶号，最省事 |
| `arrival_hour` | 到达时刻（小时） |
| `ts` | 秒级时间戳，导入时按窗口起点换算成小时 |

可选列：`priority`、`workload_class`、`duration_h`、`deadline_offset_h`。

```csv
hour,load_units,priority,workload_class,duration_h
0,32,3,cpu_bound,2
0,18,5,gpu_bound,6
1,25,2,memory_bound,4
```

**降采样**：真实轨迹动辄百万行，务必先降采样：

```
python tools/etl/build_catalog.py --kind trace --scale-keep 0.35
```

按比例随机保留，**保留原分布形状**（长尾、突发都在），只是整体降频。
不要改成均匀分布或只取前 N 条 —— 那样就退回自造数据了。

`deadline_offset_h` 缺省时按 `max(duration, round(duration × sla_slack), 1)` 计算。

### 3.3 standards（能效参照）

```csv
standard_id,grade,pue_limit,applies_to
GB 40879-2021,1级,（按标准正文填写）,新建大型数据中心
```

只用于在图表上画参照线。**具体阈值必须查标准原文**。

### 3.4 emission（碳排放因子）

```csv
region,year,kg_co2_per_kwh
华北区域,2026,（按官方公布值填写）
```

### 3.5 server_type（机架规格）

```csv
server_type,max_load,max_temp,base_power,max_power,efficiency
standard,100,85,100,300,0.85
```

用于标定 `config/default.yaml` 里 `server_types` 的出处。

---

## 4. 生成数据库

```
python tools/etl/build_catalog.py --list
python tools/etl/build_catalog.py
python tools/etl/build_catalog.py --kind ambient
python tools/etl/build_catalog.py --db data/catalog.db --scale-keep 0.3 --sla-slack 1.5
```

生成后 `data/catalog.db` 里会有 `meta_data_source`（出处）与
`meta_dataset_version`（版本/行数/哈希），仿真与接口都从这里读。

## 5. 让仿真使用真实数据

编辑 `config/default.yaml`：

```yaml
data_source:
  mode: "catalog"
  catalog_db: "data/catalog.db"
  site_id: "campus-a"
  province: "某省"
  price_year: 2026
  trace_id: "（trace 文件名或 trace_id）"
```

改完重启服务。仿真启动时会打印实际来源；若数据库不可用会自动退回
`synthetic` 并在日志里给出警告，不会静默假装用了真实数据。

## 6. 演示前检查

- [ ] 断网状态下能跑完一次仿真（快照已落盘）
- [ ] `logs/` 里没有"回退到合成数据"的告警
- [ ] `data/run_metadata.json` 里 `data_mode` 是 `catalog`
- [ ] 每个引用都能在 `meta_data_source` 里找到 url 与抓取时间
- [ ] `data/raw/` 已加入打包排除清单（不要把原始数据随包分发）

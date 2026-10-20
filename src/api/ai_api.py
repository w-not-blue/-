"""AI 诊断与自然语言参数解析接口"""
from typing import Any, Dict

from flask import Blueprint, jsonify, request

import json
import re
import os
from datetime import datetime

import yaml

from src.services.ai_service import get_ai_service, humanize_algorithms, rule_chat, rule_parse
from src.services import run_store
from src.services.plan_optimizer import get_plan_optimizer
from src.services.run_manager import get_run_manager
from src.services.simulation_insights import build_insights
from src.services.weight_tuner import WEIGHT_KEYS, run_sweep
from src.simulation.metrics import rank_algorithms, summarize
from src.utils import load_run_metadata, load_simulation_data
from src.utils.config import Config
from src.utils.logger import setup_logger
from src.utils.validation import validate_int, validate_simulation_params

logger = setup_logger("ai_api")

ai_bp = Blueprint("ai", __name__)

WEIGHT_KEYS = (
    "alpha_deadline",
    "beta_energy",
    "gamma_thermal",
    "delta_heterogeneity",
    "epsilon_migration",
)


def _current_stats(df=None) -> Dict[str, Dict[str, Any]]:
    if df is None:
        df = load_simulation_data()
    if df is None:
        return {}
    return summarize(df)


def _current_weights() -> Dict[str, float]:
    section = Config().get("damo_ets", {}) or {}
    weights: Dict[str, float] = {}
    for key in WEIGHT_KEYS:
        try:
            weights[key] = float(section.get(key))
        except (TypeError, ValueError, AttributeError):
            continue
    return weights


def _config_snapshot() -> Dict[str, Any]:
    config = Config()
    simulation = config.get("simulation", {}) or {}
    cooling = config.get("cooling", {}) or {}
    energy = config.get("energy", {}) or {}
    damo = config.get("damo_ets", {}) or {}
    return {
        "num_server_range": [
            simulation.get("min_servers"),
            simulation.get("max_servers"),
        ],
        "simulation_hour_range": [
            simulation.get("min_simulation_hours"),
            simulation.get("max_simulation_hours"),
        ],
        "sla_slack": simulation.get("sla_slack"),
        "fan_speed_range": cooling.get("fan_speed_range"),
        "fan_target_temp": cooling.get("fan_target_temp"),
        "cooling_efficiency": cooling.get("cooling_efficiency"),
        "idle_power_factor": energy.get("idle_power_factor"),
        "part_load_penalty": energy.get("part_load_penalty"),
        "temp_reference": energy.get("temp_reference"),
        "damo_ets_weights": {key: damo.get(key) for key in WEIGHT_KEYS},
        "server_types": config.get("server_types", []),
    }


def _suggest_run(text: str, context: Dict[str, Any]) -> Dict[str, Any] | None:
    triggers = ("如果", "改成", "调到", "增加到", "减少到", "降到", "提高到", "换成", "试算", "跑一下", "试试", "台服务器")
    if not any(word in text for word in triggers):
        return None
    snapshot = context.get("data_snapshot") or {}
    defaults = {
        "num_servers": snapshot.get("num_servers"),
        "simulation_hours": snapshot.get("simulation_hours"),
        "algorithms": snapshot.get("algorithms") or [],
        "run_mode": "standard",
        "site_id": snapshot.get("site"),
        "school_name": snapshot.get("school_name"),
    }
    parsed = rule_parse(text, defaults=defaults)
    params = dict(parsed.get("params") or {})

    try:
        from src.data.catalog import Catalog
        catalog = Catalog()
        if catalog.available():
            matches = [
                school for school in catalog.schools(limit=5000)
                if school.get("school_name") and school["school_name"] in text
            ]
            if matches:
                school = max(matches, key=lambda item: len(item["school_name"]))
                params["school_name"] = school["school_name"]
                params["site_id"] = school.get("city") or params.get("site_id")
    except Exception:
        pass

    changed = {}
    for key in ("num_servers", "simulation_hours", "algorithms", "run_mode", "site_id", "school_name"):
        if params.get(key) != defaults.get(key):
            changed[key] = params.get(key)
    if not changed:
        return None
    if not params.get("algorithms"):
        params["algorithms"] = list(defaults.get("algorithms") or [])
    params["visualization"] = True
    params["save_results"] = True
    if snapshot.get("site") and not params.get("site_id"):
        params["site_id"] = snapshot.get("site")
    label_parts = []
    if changed.get("school_name"):
        label_parts.append(str(changed["school_name"]))
    elif changed.get("site_id"):
        label_parts.append(str(changed["site_id"]))
    if "num_servers" in changed:
        label_parts.append("%s 台" % changed["num_servers"])
    if "simulation_hours" in changed:
        label_parts.append("%s 小时" % changed["simulation_hours"])
    if "algorithms" in changed:
        label_parts.append("%d 个算法" % len(changed["algorithms"]))
    label = "按 " + " / ".join(label_parts) + " 试算" if label_parts else "按建议配置试算"
    current_algorithms = set(defaults.get("algorithms") or [])
    suggested_algorithms = set(params.get("algorithms") or [])
    already_matches_latest = (
        params.get("num_servers") == defaults.get("num_servers")
        and params.get("simulation_hours") == defaults.get("simulation_hours")
        and params.get("site_id") == defaults.get("site_id")
        and (params.get("school_name") or None) == (defaults.get("school_name") or None)
        and suggested_algorithms == current_algorithms
    )
    reason = (
        "当前最新运行已经是这个配置，可以直接分析现有结果。"
        if already_matches_latest
        else "当前最新运行不是这个配置，需要按建议参数重新运行才能得到实测结果。"
    )
    return {
        "label": label,
        "params": params,
        "changed": changed,
        "notes": parsed.get("notes", []),
        "already_matches_latest": already_matches_latest,
        "reason": reason,
    }


ALGORITHM_WORDS = {
    "greedy": ("贪心", "greedy"),
    "genetic": ("遗传", "genetic"),
    "round_robin": ("轮询", "round", "robin"),
    "damo_ets": ("damo", "多目标能效"),
    "nsga2": ("nsga", "nsga-ii", "nsga2"),
    "moead": ("moea", "moead", "moea-d"),
}


def _suggest_optimization(text: str, context: Dict[str, Any]) -> Dict[str, Any] | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None
    lowered = normalized.lower()
    range_match = re.search(r"(\d+)\s*(?:到|至|-|—|~)\s*(\d+)\s*台", normalized)
    service_match = re.search(r"服务率[^0-9]{0,8}(\d+(?:\.\d+)?)\s*%", normalized)
    temp_match = re.search(r"(?:温度|峰值温度|最高温度)[^0-9]{0,8}(?:不超过|不高于|低于|小于|≤|<=)\s*(\d+(?:\.\d+)?)", normalized)
    explicit = any(word in normalized for word in (
        "全局寻优", "方案寻优", "自动寻优", "配置寻优", "最优方案", "最佳方案",
        "最优配置", "方案最优", "最省电的方案", "最划算的方案", "性价比最高的方案",
    ))
    if not explicit and not (range_match and (service_match or temp_match)):
        return None

    snapshot = context.get("data_snapshot") or {}
    default_servers = int(snapshot.get("num_servers") or 48)
    default_hours = int(snapshot.get("simulation_hours") or 24)
    server_min = default_servers
    server_max = max(default_servers, 80)
    if range_match:
        server_min = int(range_match.group(1))
        server_max = int(range_match.group(2))
    else:
        single = re.search(r"(?<!\d)(\d+)\s*台", normalized)
        if single:
            server_min = server_max = int(single.group(1))
    if server_min > server_max:
        server_min, server_max = server_max, server_min

    hours_match = re.search(r"(\d+)\s*(?:个)?小时", normalized)
    hours = int(hours_match.group(1)) if hours_match else default_hours
    hours = max(1, min(72, hours))

    floor = 0.98
    if service_match:
        value = float(service_match.group(1))
        floor = value / 100.0 if value > 1.0 else value
        floor = max(0.8, min(1.0, floor))

    max_temp = None
    if temp_match:
        max_temp = max(20.0, min(100.0, float(temp_match.group(1))))

    if "pue" in lowered:
        target = "pue"
    elif any(word in normalized for word in ("温度最低", "最凉", "散热最好", "温度优先")):
        target = "temp"
    elif any(word in normalized for word in ("最省电", "能耗最低", "能耗最小", "用电最低", "能耗优先")):
        target = "energy"
    else:
        target = "balanced"

    algorithms = []
    for key, words in ALGORITHM_WORDS.items():
        if any(word in lowered for word in words):
            algorithms.append(key)
    if any(word in normalized for word in ("全部算法", "六种算法", "六个算法", "全算法")):
        algorithms = ["greedy", "genetic", "round_robin", "damo_ets", "nsga2", "moead"]
    if not algorithms:
        algorithms = ["greedy", "genetic", "round_robin", "damo_ets"]

    site_id = snapshot.get("site") or "北京市"
    school_name = snapshot.get("school_name") or ""
    try:
        from src.data.catalog import Catalog
        catalog = Catalog()
        if catalog.available():
            matches = [
                school for school in catalog.schools(limit=5000)
                if school.get("school_name") and school["school_name"] in normalized
            ]
            if matches:
                school = max(matches, key=lambda item: len(item["school_name"]))
                school_name = school["school_name"]
                site_id = school.get("city") or site_id
    except Exception:
        pass

    params = {
        "server_min": server_min,
        "server_max": server_max,
        "simulation_hours": hours,
        "algorithms": algorithms,
        "target": target,
        "service_rate_floor": floor,
        "max_temp": max_temp,
        "site_id": site_id,
        "school_name": school_name,
        "include_weights": "不调权重" not in normalized and "不用权重" not in normalized,
    }
    target_text = {
        "pue": "PUE 最低",
        "energy": "单位能耗最低",
        "temp": "峰值温度最低",
        "balanced": "综合均衡",
    }[target]
    label = "在 %d–%d 台、%d 小时内寻找%s方案" % (server_min, server_max, hours, target_text)
    return {"params": params, "label": label}


def _optimization_reply(job: Dict[str, Any]) -> str:
    params = job.get("params") or {}
    return "已启动全局寻优：在 %d–%d 台、%d 小时内评估 %d 种算法，服务率门槛 %.1f%%%s。结果会显示在下方寻优卡片里。" % (
        params.get("server_min", 0),
        params.get("server_max", 0),
        params.get("simulation_hours", 0),
        len(params.get("algorithms") or []),
        float(params.get("service_rate_floor") or 0.98) * 100.0,
        ("，峰值温度不超过 %.1f℃" % params["max_temp"]) if params.get("max_temp") is not None else "",
    )

CHAT_SYSTEM = """你是"校园数据中心智能调度系统"的 AI 助手（名字：小助）。
规则：
1. 只依据下面提供的"当前数据"回答，不得编造数字；数据里没有的项直接说"当前数据没有这一项"。
2. 用中文自然语言回答，**不要出现内部字段名**（如 max_temp、avg_pue、energy_per_served_load、service_rate），
   要说"峰值温度""平均 PUE""单位负载能耗""任务服务率"。
3. 先给一句结论，再列数据；列表最多 5 条，避免大段复述。
4. 当用户问"数据从哪来"时，依据当前数据里的 provenance 字段回答发布方与数据集名称
   （例如 ERA5 气温、阿里云集群轨迹、生态环境部碳因子），不要笼统说"目录数据"。
5. 算法名称一律用中文或规范写法：贪心算法、遗传算法、轮询算法、DAMO-ETS、NSGA-II、MOEA-D；
   不要写 greedy、genetic、round_robin、damo_ets 这类代码标识符。
6. 温度保留 1 位小数，PUE 保留 3 位小数，百分比保留 2 位小数。
7. 用户问怎么操作时，依据当前数据里的 ui_hints 告诉他点页面哪里；不要假装已经执行。
8. 严格区分两个指标：dropped_tasks 是"未被分配到服务器"的任务，deadline_violations 是"完成超时"的任务，
   不要把"未分配"说成"超时违约"，也不要把两者相加。
9. 当前数据里的 insights 是从最近一次仿真结果实时计算的。用户问"什么时候性价比最高""哪个时段最好"时，
   优先使用 safe_best_hours（已排除峰值温度达到 70℃ 或出现过热服务器的时段），给出具体小时、算法、
   单位服务负载能耗、温度和 PUE；如果 overall_best_hours 的第一名温度过高，要单独提醒，不要直接推荐。
   insights 里的 hour 是从 0 开始的时间索引，回答时换算成"第 hour+1 小时"。
10. 用户问"温度低于多少度怎么配置"时，使用 temperature_thresholds 或 temperature_bands，给出该温区的小时数、
    最优算法、服务率、风扇转速和 PUE；同时说明这是当前配置下的观测结果，不是改变硬件后的反事实预测。
11. 用户问"数据更新了吗""这次运行是什么时候"时，使用 snapshot 里的 generated_at、site、num_servers、
    simulation_hours 和 algorithms 回答，不要沿用历史对话里的旧数字。
12. 用户的问题不限于预设示例。只要与当前仿真数据、调度、能耗、温度、PUE、服务率、配置调整有关，
    都要基于当前数据回答；不要要求用户只能问某几类问题。
13. 遇到当前数据没有实际运行过的配置调整（服务器数量、风扇曲线、冷却策略、算法权重等），按三层回答：
    (1) 当前数据观察到的关系和数值；(2) 调整后的方向、可能收益和风险；(3) 需要用哪些参数重新仿真确认。
    不得把推测写成实测结果；没有数据时直接说"当前数据没有这一项"。
    只能建议用户操作 context.capabilities.ui_limits 里列出的网页表单参数；风扇曲线、冷却效率、权重不在表单里，
    应说明需要修改配置文件（config 目录下的 yaml 文件）后重新运行，不要说"在页面上直接调"。
14. context.config 给出了当前物理模型和参数边界；context.insights.relationships 只是相关性，不是因果结论。
15. 如果 context.suggested_run 存在，先看 already_matches_latest：
    - true：当前最新运行已经是这个配置，直接基于现有数据分析，不要再说需要重跑；
    - false：当前数据不是这个配置，明确说明差异（尤其是学校、台数、时长、算法），并告诉用户可以点消息下方的
      "按此配置试算"按钮验证。
16. 如果 context.executed_run.status 是 success，说明本轮已经实际运行完成，必须直接使用最新 snapshot/insights
    回答，并提到"运行编号"；不要再说"需要重新运行"。
17. 如果 context.executed_run.status 是 running，说明任务已经启动但还没完成，不要给出未完成的数值，
    只说明进度和稍后查看。
18. 如果 context.executed_run.status 是 failed，说明失败原因，不要编造结果。
19. 如果 context.auto_run_budget.allowed 是 false，说明为什么没有自动执行，并提示用户点"按此配置试算"
    或把参数调整到自动试算预算内。
20. 用户问"上次/最近几次/对比/变化"时，使用 context.recent_runs 和 context.latest_comparison；
    latest_comparison.left 是较早一次，right 是较新一次。只引用真实记录，不要编造历史。
21. 回答中使用"网页表单"，不要写"Web 表单"；配置文件统一写成"配置文件"或"配置文件（config 目录下的 yaml 文件）"。
22. 用户提到学校时，按该学校所在城市运行，并在回答中写出学校名称；只使用本站数据目录中的学校、城市、气温和仿真结果。
23. 你是本站专用助手，只操控本站的仿真、数据和运行历史，不访问互联网，不执行任意代码，不回答与本站无关的通用任务。
24. 只有在 snapshot.school_name 与用户提到的学校一致时，才能说该学校已经跑过；否则必须说明当前运行没有记录
    这个学校，建议按该校所在城市重新试算。
25. 面向用户时不要出现 run_id、runId 这类字段名，统一写"运行编号"；编号本身保持原样，不要翻译或改写。
当前数据：
"""


def _data_provenance() -> list:
    try:
        from src.data.catalog import Catalog
        catalog = Catalog()
        if not catalog.available():
            return []
        return [
            {
                "publisher": row.get("publisher"),
                "name": row.get("name"),
                "url": row.get("url"),
            }
            for row in catalog.provenance()
        ]
    except Exception as exc:
        logger.warning("读取数据出处失败: %s" % exc)
        return []


def _auto_run_budget(params: Dict[str, Any]) -> Dict[str, Any]:
    manager = get_run_manager()
    active = manager.active_run()
    if active:
        return {
            "allowed": False,
            "max_servers": 100,
            "max_hours": 48,
            "reason": "当前已有运行任务（运行编号 %s）正在进行，请等它完成后再试。" % active.get("run_id"),
        }
    num_servers = int(params.get("num_servers") or 0)
    hours = int(params.get("simulation_hours") or 0)
    if num_servers > 100 or hours > 48:
        return {
            "allowed": False,
            "max_servers": 100,
            "max_hours": 48,
            "reason": "自动试算预算为最多 100 台、48 小时，当前请求是 %d 台、%d 小时。" % (
                num_servers, hours
            ),
        }
    return {
        "allowed": True,
        "max_servers": 100,
        "max_hours": 48,
        "reason": "",
    }


def _run_status_text(executed: Dict[str, Any], context: Dict[str, Any]) -> str:
    params = executed.get("params") or {}
    label = executed.get("label") or "建议配置"
    run_id = executed.get("run_id") or "unknown"
    status = executed.get("status")
    if status == "success":
        return "已按 %s 完成运行（运行编号：%s），页面数据已经同步。" % (label, run_id)
    if status == "failed":
        return "我尝试按 %s 运行（运行编号：%s），但失败了：%s。可以调整参数后重试。" % (
            label, run_id, executed.get("error") or "未知错误"
        )
    return "已按 %s 启动运行（运行编号：%s），当前仍在执行。运行完成后页面会自动刷新，你可以稍后再问我结果。" % (
        label, run_id
    )


def _compact_run(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "run_id": record.get("run_id"),
        "origin": record.get("origin"),
        "status": record.get("status"),
        "created_at": record.get("created_at"),
        "finished_at": record.get("finished_at"),
        "params": record.get("params") or {},
        "summary": record.get("summary") or {},
    }


def _chat_context() -> Dict[str, Any]:
    df = load_simulation_data()
    stats = _current_stats(df)
    meta = load_run_metadata() or {}
    insights = build_insights(df, meta)
    recent_runs = [
        _compact_run(item) for item in run_store.list_runs(10)
        if item.get("status") == "success"
    ][:5]
    latest_comparison = None
    if len(recent_runs) >= 2:
        raw_comparison = run_store.compare_runs(
            recent_runs[1]["run_id"], recent_runs[0]["run_id"]
        )
        if raw_comparison:
            latest_comparison = {
                "left": _compact_run(raw_comparison["left"]),
                "right": _compact_run(raw_comparison["right"]),
                "metrics": raw_comparison["metrics"],
                "rule": raw_comparison["rule"],
            }
    return {
        "stats": stats,
        "ranking": rank_algorithms(stats) if stats else {},
        "insights": insights,
        "data_snapshot": insights.get("snapshot", {}),
        "config": _config_snapshot(),
        "capabilities": {
            "free_form": "可以回答任何与当前仿真数据、调度、能耗、温度、PUE、服务率和配置调整有关的问题，不限于预设示例。",
            "observed": "当前数据能直接回答已经运行过的结果、时段、温区和算法对比。",
            "requires_rerun": "改变服务器数量、风扇曲线、冷却策略或算法权重后的具体数值，需要重新仿真验证。",
            "scope": "只使用本站数据目录、仿真结果和运行历史，不访问互联网，不执行任意代码，也不操作本站以外的系统。",
            "ui_limits": "网页表单可改服务器数量、仿真时长、运行模式、服务器类型、算法和城市；风扇曲线、冷却效率、DAMO-ETS 权重需要修改配置文件（config 目录下的 yaml 文件）后重新运行。",
        },
        "recent_runs": recent_runs,
        "latest_comparison": latest_comparison,
        "data_source": {
            "site_id": meta.get("site_id"),
            "school_name": meta.get("school_name"),
            "site_province": meta.get("site_province"),
            "emission_region": meta.get("emission_region"),
            "ambient_source": meta.get("ambient_source"),
            "scenario_source": meta.get("scenario_source"),
            "scenario_digest": meta.get("scenario_digest"),
            "service_rate_floor": meta.get("service_rate_floor"),
        },
        "provenance": _data_provenance(),
        "ui_hints": {
            "run": "在「仿真控制」区块：先选所在城市（可输入一个字搜索），可选学校，勾选算法，然后点「开始运行」。",
            "results": "结果在下方「数据分析」的 6 张图表、「算法指标对比」表格和「受控排名」列表里。",
            "assistant": "页面右下角悬浮球打开本对话；面板内的麦克风按钮可直接语音输入。",
        },
        "num_servers": meta.get("num_servers"),
        "simulation_hours": meta.get("simulation_hours"),
        "controlled": meta.get("controlled"),
    }


@ai_bp.route("/api/v1/ai/chat", methods=["POST"])
def ai_chat():
    data = request.json or {}
    raw_messages = data.get("messages") or []
    if not isinstance(raw_messages, list) or not raw_messages:
        return jsonify({"success": False, "message": "messages 不能为空"}), 400

    messages = []
    for item in raw_messages[-10:]:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:2000]})
    if not messages:
        return jsonify({"success": False, "message": "没有有效的对话内容"}), 400

    context = _chat_context()
    latest_user = next((item["content"] for item in reversed(messages) if item["role"] == "user"), "")
    optimization_job = None
    optimization_suggestion = _suggest_optimization(latest_user, context)
    if optimization_suggestion:
        try:
            optimizer = get_plan_optimizer()
            optimization_job = optimizer.active() or optimizer.start(optimization_suggestion["params"])
            context["optimization"] = {
                "job_id": optimization_job.get("job_id"),
                "status": optimization_job.get("status"),
                "label": optimization_suggestion.get("label"),
                "params": optimization_job.get("params"),
            }
        except Exception as exc:
            logger.warning("启动全局寻优失败: %s" % exc)
            context["optimization_error"] = str(exc)
    suggested = None if optimization_job else _suggest_run(latest_user, context)
    context["suggested_run"] = suggested

    auto_run = bool(data.get("auto_run"))
    executed = None
    if auto_run and suggested:
        budget = _auto_run_budget(suggested["params"])
        context["auto_run_budget"] = budget
        if budget.get("allowed"):
            manager = get_run_manager()
            run_id = manager.submit(suggested["params"], origin="ai")
            record = manager.wait(run_id, timeout=120) or {}
            executed = {
                "run_id": run_id,
                "status": record.get("status") or "running",
                "params": suggested["params"],
                "label": suggested["label"],
                "summary": record.get("summary") or {},
                "error": record.get("error") or "",
            }
            if executed["status"] == "success":
                context = _chat_context()
                context["executed_run"] = executed
                context["suggested_run"] = None
            else:
                context["executed_run"] = executed
                context["suggested_run"] = suggested

    service = get_ai_service()
    service.reload_key()
    system = CHAT_SYSTEM + json.dumps(context, ensure_ascii=False, default=str)

    if optimization_job:
        return jsonify({
            "success": True,
            "reply": _optimization_reply(optimization_job),
            "source": "optimizer",
            "model": get_ai_service().model,
            "key_source": get_ai_service().key_source,
            "snapshot": context.get("insights", {}).get("snapshot", {}),
            "suggested_run": None,
            "run": None,
            "auto_run": auto_run,
            "budget": None,
            "optimization": optimization_job,
        })

    source = "deepseek"
    try:
        reply = humanize_algorithms(service.chat_messages(system, messages))
    except Exception as exc:
        logger.warning("AI 对话回退本地规则: %s" % exc)
        reply = rule_chat(messages[-1]["content"], context)
        if executed:
            if executed["status"] == "success":
                reply = _run_status_text(executed, context) + "\n" + reply
            else:
                reply = _run_status_text(executed, context)
        source = "rule"

    return jsonify({
        "success": True,
        "reply": reply,
        "source": source,
        "model": service.model,
        "key_source": service.key_source,
        "snapshot": context.get("insights", {}).get("snapshot", {}),
        "suggested_run": context.get("suggested_run"),
        "run": executed,
        "auto_run": auto_run,
        "budget": context.get("auto_run_budget"),
        "optimization": optimization_job,
        "optimization_error": context.get("optimization_error"),
    })


@ai_bp.route("/api/v1/ai/status", methods=["GET"])
def ai_status():
    service = get_ai_service()
    service.reload_key()
    return jsonify({"success": True, "status": service.status()})


@ai_bp.route("/api/v1/ai/analyze", methods=["POST"])
def ai_analyze():
    stats = _current_stats()
    if not stats:
        return jsonify({"success": False, "message": "暂无仿真数据，请先运行一次仿真"}), 404
    service = get_ai_service()
    service.reload_key()
    try:
        result = service.analyze(stats, _current_weights())
        result["metrics"] = stats
        return jsonify({"success": True, "result": result})
    except Exception as exc:
        logger.error("AI 诊断失败: %s" % exc)
        return jsonify({"success": False, "message": "诊断服务异常"}), 500


@ai_bp.route("/api/v1/ai/parse", methods=["POST"])
def ai_parse():
    data = request.json or {}
    text = str(data.get("text", "")).strip()
    if not text:
        return jsonify({"success": False, "message": "请输入一句需求描述"}), 400
    if len(text) > 200:
        return jsonify({"success": False, "message": "描述请控制在 200 字以内"}), 400
    service = get_ai_service()
    service.reload_key()
    try:
        parsed = service.parse_intent(text)
        params = validate_simulation_params(parsed.get("params", {}))
        params["visualization"] = True
        params["save_results"] = True
        return jsonify(
            {
                "success": True,
                "params": params,
                "notes": parsed.get("notes", []),
                "source": parsed.get("source", "rule"),
                "notice": parsed.get("notice", ""),
            }
        )
    except Exception as exc:
        logger.error("参数解析失败: %s" % exc)
        return jsonify({"success": False, "message": "无法解析为合法参数: %s" % exc}), 400


def _env_config_path() -> str:
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config")
    env = os.environ.get("DCS_ENV", "development")
    return os.path.abspath(os.path.join(root, "%s.yaml" % env))


def _apply_weights(weights: Dict[str, float]) -> Dict[str, Any]:
    path = _env_config_path()
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    previous = dict(Config().get("damo_ets", {}) or {})

    lines = ["  %s: %s" % (key, weights[key]) for key in WEIGHT_KEYS if key in weights]
    if len(lines) != len(WEIGHT_KEYS):
        raise ValueError("权重参数不完整")

    if "\ndamo_ets:" in text or text.startswith("damo_ets:"):
        parts = text.split("damo_ets:", 1)
        head = parts[0].rstrip("\n")
        rest = parts[1].split("\n")
        kept = []
        for line in rest:
            stripped = line.strip()
            if not stripped:
                continue
            if line.startswith(("  ", "\t")) and any(stripped.startswith(key + ":") for key in WEIGHT_KEYS):
                continue
            if not line.startswith(("  ", "\t")):
                kept.append("__BREAK__")
                kept.append(line)
                continue
            kept.append(line)
        body = []
        for line in kept:
            if line == "__BREAK__":
                body.extend(lines)
                break
            body.append(line)
        else:
            body.extend(lines)
        new_text = head + "\ndamo_ets:\n" + "\n".join(body)
        tail_lines = []
        if "__BREAK__" in kept:
            index = kept.index("__BREAK__")
            tail_lines = kept[index + 1:]
            new_text = head + "\ndamo_ets:\n" + "\n".join(body) + "\n" + "\n".join(tail_lines)
    else:
        new_text = text.rstrip("\n") + "\n\ndamo_ets:\n" + "\n".join(lines) + "\n"

    parsed = yaml.safe_load(new_text)
    if not isinstance(parsed, dict) or "damo_ets" not in parsed:
        raise ValueError("写入后的配置无法解析")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(new_text)
    Config._instance = None
    return {"path": path, "previous": previous, "applied": weights}


@ai_bp.route("/api/v1/ai/tune", methods=["POST"])
def ai_tune():
    data = request.json or {}
    try:
        servers = validate_int(data.get("num_servers"), min_val=10, max_val=1000, default=100, name="服务器数量")
        hours = validate_int(data.get("simulation_hours"), min_val=1, max_val=720, default=24, name="仿真时长")
    except Exception as exc:
        return jsonify({"success": False, "message": "参数不合法: %s" % exc}), 400

    service = get_ai_service()
    service.reload_key()
    try:
        sweep = run_sweep(servers, hours, 42)
        sweep["explanation"] = service.explain_tuning(sweep)
        return jsonify({"success": True, "result": sweep})
    except Exception as exc:
        logger.error("权重寻优失败: %s" % exc)
        return jsonify({"success": False, "message": "寻优失败: %s" % exc}), 500


@ai_bp.route("/api/v1/ai/apply-tuning", methods=["POST"])
def ai_apply_tuning():
    data = request.json or {}
    weights = data.get("weights") or {}
    cleaned: Dict[str, float] = {}
    try:
        for key in WEIGHT_KEYS:
            cleaned[key] = round(float(weights.get(key)), 4)
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "权重取值不合法"}), 400
    total = sum(cleaned.values())
    if total <= 0 or abs(total - 1.0) > 0.02:
        return jsonify({"success": False, "message": "权重之和应接近 1.0，当前为 %.3f" % total}), 400
    try:
        info = _apply_weights(cleaned)
        return jsonify({"success": True, "result": info})
    except Exception as exc:
        logger.error("写入权重失败: %s" % exc)
        return jsonify({"success": False, "message": "写入配置失败: %s" % exc}), 500

@ai_bp.route("/api/v1/ai/optimize", methods=["POST"])
def ai_optimize_start():
    data = request.json or {}
    try:
        optimizer = get_plan_optimizer()
        job = optimizer.active() or optimizer.start(data)
        return jsonify({"success": True, "job": job})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        logger.error("启动全局寻优失败: %s" % exc)
        return jsonify({"success": False, "message": "启动寻优失败: %s" % exc}), 500


@ai_bp.route("/api/v1/ai/optimize/<job_id>", methods=["GET"])
def ai_optimize_status(job_id):
    if not re.match(r"^[a-f0-9]{12}$", str(job_id or "")):
        return jsonify({"success": False, "message": "任务编号无效"}), 400
    job = get_plan_optimizer().snapshot(job_id)
    if not job:
        return jsonify({"success": False, "message": "寻优任务不存在"}), 404
    return jsonify({"success": True, "job": job})


@ai_bp.route("/api/v1/ai/optimize/<job_id>/apply", methods=["POST"])
def ai_optimize_apply(job_id):
    if not re.match(r"^[a-f0-9]{12}$", str(job_id or "")):
        return jsonify({"success": False, "message": "任务编号无效"}), 400
    try:
        result = get_plan_optimizer().apply(job_id)
        return jsonify({"success": True, "result": result})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        logger.error("应用寻优方案失败: %s" % exc)
        return jsonify({"success": False, "message": "应用方案失败: %s" % exc}), 500

def _prediction_series(records):
    fields = (
        "pue", "total_energy", "peak_temperature", "avg_temperature",
        "total_load", "served_load", "offered_load", "assigned", "dropped",
        "ambient_temperature", "co2_kg",
    )
    grouped: Dict[str, list] = {}
    for row in records or []:
        algo = str(row.get("algorithm") or "unknown")
        item = {"time": int(row.get("time", 0) or 0)}
        for field in fields:
            value = row.get(field)
            try:
                item[field] = float(value)
            except (TypeError, ValueError):
                item[field] = None
        grouped.setdefault(algo, []).append(item)
    for series in grouped.values():
        series.sort(key=lambda item: item["time"])
    return grouped


def _prediction_explanation(stats, series, best):
    if best and best in stats:
        item = stats[best]
        avg_pue = float(item.get("avg_pue") or 0)
        max_temp = float(item.get("max_temp") or 0)
        service_rate = float(item.get("service_rate") or 0) * 100
        text = (
            "%s 的综合表现最好：平均 PUE %.2f，峰值温度 %.1f°C，服务率 %.1f%%。"
            % (best, avg_pue, max_temp, service_rate)
        )
        seq = series.get(best) or []
        if seq:
            peak = max(seq, key=lambda row: row.get("peak_temperature") or 0)
            text += " 温度最高出现在第 %d 小时，建议在高温时段前提高制冷冗余，或把非紧急任务错峰安排。" % peak["time"]
        if service_rate >= 98:
            text += " 当前配置可以满足服务率门槛。"
        else:
            text += " 当前配置未达到 98% 服务率门槛，建议增加服务器或降低负载后复测。"
        return text
    return "当前配置下没有算法达到 98% 服务率门槛，建议增加服务器、降低负载或调整算法权重后重新预测。"


@ai_bp.route("/api/v1/ai/predict", methods=["POST"])
def ai_predict():
    data = request.get_json(silent=True) or {}
    try:
        horizon = validate_int(
            data.get("horizon_hours", data.get("simulation_hours")),
            min_val=6, max_val=48, default=24, name="预测时长",
        )
        params = validate_simulation_params({
            "num_servers": data.get("num_servers"),
            "simulation_hours": horizon,
            "parallel_workers": data.get("parallel_workers", 1),
            "run_mode": "standard",
            "server_type": data.get("server_type", "mixed"),
            "algorithms": data.get("algorithms") or ["greedy", "genetic", "round_robin", "damo_ets", "nsga2", "moead"],
        })
    except Exception as exc:
        return jsonify({"success": False, "message": "参数不合法: %s" % exc}), 400

    site_id = str(data.get("site_id") or "").strip()
    school_name = str(data.get("school_name") or "").strip()
    if site_id:
        params["site_id"] = site_id
    if school_name:
        params["school_name"] = school_name

    try:
        _, result = get_run_manager().run_prediction(params)
        if not result.success:
            return jsonify({"success": False, "message": result.message or "预测失败"}), 500
        payload = result.data or {}
        stats = payload.get("statistics") or {}
        ranking = payload.get("ranking") or {}
        best = ranking.get("best")
        series = _prediction_series(payload.get("series"))
        assumptions = [
            "使用所选学校所在城市的 2025 年历史气象作为典型日情景",
            "任务流来自 2025 年阿里云 GPU 集群公开轨迹，按 0.3 缩放、1h 粒度",
            "预测时长 %d 小时，服务器 %d 台，服务器类型 %s" % (
                params["simulation_hours"], params["num_servers"], params["server_type"],
            ),
            "结果为受控仿真，不是学校机房实时预测",
        ]
        return jsonify({
            "success": True,
            "scenario": {
                "site_id": params.get("site_id") or "北京市",
                "school_name": params.get("school_name") or "",
                "horizon_hours": params["simulation_hours"],
                "num_servers": params["num_servers"],
                "server_type": params["server_type"],
                "algorithms": params["algorithms"],
                "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            "series": series,
            "summary": stats,
            "ranking": ranking,
            "best_algorithm": best,
            "explanation": _prediction_explanation(stats, series, best),
            "assumptions": assumptions,
            "disclaimer": "这是基于历史气象与公开任务流的受控情景预测，不是学校机房实时预测。",
            "data_source": payload.get("data_source") or {},
        })
    except Exception as exc:
        logger.error("AI prediction failed: %s" % exc)
        return jsonify({"success": False, "message": "预测失败: %s" % exc}), 500











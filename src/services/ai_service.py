"""调度诊断 AI 服务

优先调用 DeepSeek 大模型生成诊断建议；未配置 API Key 或调用失败时，
自动退化为本地专家规则，保证离线环境下页面依然可用。
"""
import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.services.weight_tuner import WEIGHT_KEYS
from src.utils.config import Config
from src.utils.logger import setup_logger

logger = setup_logger("ai_service")

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
KEY_ENV = "DEEPSEEK_API_KEY"
KEY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "deepseek.key"
)

ALL_ALGORITHMS = ["greedy", "genetic", "round_robin", "damo_ets", "nsga2", "moead"]

ALGO_ALIASES = {
    "贪心": "greedy",
    "greedy": "greedy",
    "遗传": "genetic",
    "genetic": "genetic",
    "轮询": "round_robin",
    "round_robin": "round_robin",
    "round robin": "round_robin",
    "damo": "damo_ets",
    "多目标": "damo_ets",
    "能效": "damo_ets",
    "nsga": "nsga2",
    "nsga2": "nsga2",
    "nsga-ii": "nsga2",
    "nsga ii": "nsga2",
    "moead": "moead",
    "moea": "moead",
    "moea-d": "moead",
    "moea d": "moead",
}

CN_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "十二": 12}

ANALYZE_SYSTEM = """你是数据中心能效与调度专家。根据给定的多算法仿真指标输出 JSON：
{"summary": "一段中文结论", "best_algorithm": "算法标识",
 "risks": ["风险点"], "tuning": {"参数名": 建议数值}, "actions": ["可执行动作"]}
要求：数值只能来自输入数据，不得编造；语言简洁专业；risks 与 actions 各不超过 4 条。
tuning 只允许出现这些权重参数名：alpha_deadline、beta_energy、gamma_thermal、
delta_heterogeneity、epsilon_migration。
算法名称一律使用中文或规范写法：贪心算法、遗传算法、轮询算法、DAMO-ETS、NSGA-II、MOEA-D，
不要写成 greedy、genetic、round_robin、damo_ets 这类代码标识符。"""

PARSE_SYSTEM = """把用户的一句话需求转成仿真参数 JSON：
{"num_servers": 整数, "simulation_hours": 整数, "algorithms": ["greedy","genetic","round_robin","damo_ets","nsga2","moead"],
 "run_mode": "standard" 或 "distributed" 或 "large_scale"}
只输出 JSON，不要解释。无法确定的字段可以省略。"""


DISPLAY_NAMES = {
    "damo_ets": "DAMO-ETS",
    "round_robin": "轮询算法",
    "greedy": "贪心算法",
    "genetic": "遗传算法",
    "nsga2": "NSGA-II",
    "moead": "MOEA-D",
}

_NORMALIZED_NAMES = {
    re.sub(r"[_\-\s]", "", key): value for key, value in DISPLAY_NAMES.items()
}

_ALGO_PATTERN = re.compile(
    r"\b(damo[_\-\s]?ets|round[_\-\s]?robin|nsga[_\-\s]?(?:2|ii)|"
    r"moea[_\-\s]?d|greedy|genetic)\b",
    re.IGNORECASE,
)


def humanize_algorithms(text: str) -> str:
    """Replace code identifiers such as ``round_robin`` with display names."""
    if not isinstance(text, str):
        return text

    def replace(match: "re.Match[str]") -> str:
        key = re.sub(r"[_\-\s]", "", match.group(1).lower())
        return _NORMALIZED_NAMES.get(key, match.group(0))

    return _ALGO_PATTERN.sub(replace, text)


WEIGHT_LABELS = {
    "alpha_deadline": "α 截止权重",
    "beta_energy": "β 能耗权重",
    "gamma_thermal": "γ 温度权重",
    "delta_heterogeneity": "δ 异构权重",
    "epsilon_migration": "ε 迁移权重",
}


def localize(text: Any) -> Any:
    if not isinstance(text, str):
        return text
    result = text
    for key, label in list(DISPLAY_NAMES.items()) + list(WEIGHT_LABELS.items()):
        result = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(key) + r"(?![A-Za-z0-9_])", label, result)
    return result


def localize_result(result: Dict[str, Any]) -> Dict[str, Any]:
    if result.get("summary"):
        result["summary"] = localize(result["summary"])
    for field in ("risks", "actions", "reasons"):
        if result.get(field):
            result[field] = [localize(item) for item in result[field]]
    for field in ("caution", "notice"):
        if result.get(field):
            result[field] = localize(result[field])
    return result


def load_credentials() -> Tuple[str, str, str]:
    key = os.environ.get(KEY_ENV, "").strip()
    model = os.environ.get("DEEPSEEK_MODEL", "").strip()
    if key:
        return key, "env", model
    try:
        with open(KEY_FILE, "r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
        if lines:
            return lines[0], "file", (lines[1] if len(lines) > 1 else "")
    except OSError:
        pass
    return "", "none", ""


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def rule_analyze(stats: Dict[str, Dict[str, Any]], weights: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not stats:
        return {
            "summary": "暂无可分析的仿真数据，请先运行一次仿真。",
            "best_algorithm": "",
            "risks": [],
            "tuning": {},
            "actions": [],
            "source": "rule",
        }

    by_energy = sorted(stats.items(), key=lambda kv: _num(kv[1].get("avg_energy")))
    best_energy, best_row = by_energy[0]
    hottest = max(stats, key=lambda k: _num(stats[k].get("avg_temp", stats[k].get("max_temp"))))
    weakest = min(stats, key=lambda k: _num(stats[k].get("service_rate"), 1.0))
    baseline = _num(stats.get("round_robin", {}).get("avg_energy")) or _num(by_energy[-1][1].get("avg_energy"))
    saving = 0.0
    if baseline:
        saving = (baseline - _num(best_row.get("avg_energy"))) / baseline * 100.0

    max_temp = max(_num(row.get("max_temp")) for row in stats.values())
    avg_pue = sum(_num(row.get("avg_pue")) for row in stats.values()) / len(stats)
    weakest_rate = _num(stats[weakest].get("service_rate"), 1.0)

    summary = (
        "%s 在当前配置下平均能耗最低（%.0f W，较轮询基线低 %.1f%%）；"
        "集群最高温度 %.1f °C，平均 PUE %.2f。"
        % (best_energy, _num(best_row.get("avg_energy")), saving, max_temp, avg_pue)
    )
    if weakest_rate >= 0.999:
        summary += "四个算法的任务服务率均达到 100%%，指标可直接横向比较。"

    risks: List[str] = []
    actions: List[str] = []
    tuning: Dict[str, Any] = {}

    if weakest_rate < 0.98:
        dropped = int(_num(stats[weakest].get("dropped_tasks")))
        risks.append(
            "%s 有 %d 个任务未成功分配（服务率 %.1f%%），其能耗与温度偏低主要是因为接的任务更少，"
            "与其他算法不在同一口径上。" % (weakest, dropped, weakest_rate * 100)
        )
        actions.append("对外对比能耗前先公布服务率，或为 %s 增加可用服务器数量后重跑。" % weakest)

    if max_temp > 70:
        risks.append("最高温度达到 %.1f °C，已接近 85 °C 的强制散热阈值。" % max_temp)
        gamma = _num((weights or {}).get("gamma_thermal"), 0.20)
        tuning["gamma_thermal"] = round(min(0.40, gamma + 0.05), 2)
        actions.append("提高 DAMO-ETS 的温度权重 gamma_thermal，让调度优先避开高温机架。")

    if avg_pue > 1.4:
        risks.append("平均 PUE 为 %.2f，高于 1.4 的常规水平，冷却开销偏大。" % avg_pue)
        actions.append("检查冷却目标温度与风扇曲线，或降低单机负载密度。")

    deadline = max(int(_num(row.get("deadline_violations"))) for row in stats.values())
    if deadline > 0:
        risks.append("存在 %d 次 deadline 违约，说明当前分配策略对时延敏感任务保护不足。" % deadline)
        alpha = _num((weights or {}).get("alpha_deadline"), 0.35)
        tuning["alpha_deadline"] = round(min(0.60, alpha + 0.05), 2)

    if not risks:
        risks.append("本次仿真未发现明显风险：服务率、温度与 PUE 均在合理区间。")

    if not tuning:
        tuning = {
            "alpha_deadline": _num((weights or {}).get("alpha_deadline"), 0.35),
            "gamma_thermal": _num((weights or {}).get("gamma_thermal"), 0.20),
        }
        actions.append("当前权重已较均衡，可在自动调参实验中进一步搜索更优组合。")

    actions.append("把本轮结果与上一轮对比，确认调参带来的变化方向是否一致。")

    return {
        "summary": summary,
        "best_algorithm": best_energy,
        "risks": risks[:4],
        "tuning": tuning,
        "actions": actions[:4],
        "source": "rule",
    }


def _to_int(token: str) -> int:
    token = token.strip()
    if token.isdigit():
        return int(token)
    return CN_NUMBERS.get(token, 0)


def rule_parse(text: str, defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    defaults = defaults or {}
    params: Dict[str, Any] = {}
    notes: List[str] = []
    number = r"(\d+|[一二两三四五六七八九十]+)"
    servers = re.search(number + r"\s*(?:台|个|节点|服务器)", text)
    hours = re.search(number + r"\s*(?:个?小时|h\b)", text)
    days = re.search(number + r"\s*(?:天|日)", text)
    if servers:
        params["num_servers"] = _to_int(servers.group(1))
    elif defaults.get("num_servers"):
        params["num_servers"] = int(defaults["num_servers"])
    if hours:
        params["simulation_hours"] = _to_int(hours.group(1))
    elif days:
        params["simulation_hours"] = _to_int(days.group(1)) * 24
        notes.append("按 1 天 = 24 小时换算得到仿真时长。")
    elif defaults.get("simulation_hours"):
        params["simulation_hours"] = int(defaults["simulation_hours"])

    picked = []
    lowered = text.lower()
    for alias, algo in ALGO_ALIASES.items():
        if alias in lowered and algo not in picked:
            picked.append(algo)
    if picked:
        params["algorithms"] = picked
    elif defaults.get("algorithms"):
        params["algorithms"] = list(defaults["algorithms"])
        notes.append("未识别到具体算法，沿用当前运行的算法列表。")
    else:
        params["algorithms"] = list(ALL_ALGORITHMS)
        notes.append("未识别到具体算法，默认对比全部算法。")

    if any(word in text for word in ("大规模", "压测", "1000", "500")):
        params["run_mode"] = "large_scale"
    elif "分布式" in text:
        params["run_mode"] = "distributed"
    elif defaults.get("run_mode"):
        params["run_mode"] = defaults["run_mode"]

    if any(word in text for word in ("压住温度", "降温", "散热", "过热")):
        if "damo_ets" not in params["algorithms"]:
            params["algorithms"].append("damo_ets")
        notes.append("检测到温度控制意图，已加入 DAMO-ETS 并在建议中提高温度权重。")
    if "省电" in text or "能耗" in text or "节能" in text:
        notes.append("检测到节能意图，建议用能耗列做主指标。")

    if not params.get("num_servers"):
        if defaults.get("num_servers"):
            params["num_servers"] = int(defaults["num_servers"])
        else:
            params["num_servers"] = 10
            notes.append("未识别到服务器数量，将使用默认值 10。")

    if params.get("num_servers", 0) > 1000:
        params["num_servers"] = 1000
        notes.append("服务器数量超过系统上限，已按 1000 台处理。")
    if params.get("simulation_hours", 0) > 720:
        original = params["simulation_hours"]
        params["simulation_hours"] = 720
        notes.append("仿真时长 %d 小时超过系统上限 720 小时，已按上限处理。" % original)

    notes = [localize(item) for item in notes]
    return {"params": params, "notes": notes, "source": "rule"}


def rule_explain_tuning(sweep: Dict[str, Any]) -> Dict[str, Any]:
    rows = sweep.get("rows", [])
    best = sweep.get("best", {})
    baseline = sweep.get("baseline", {})
    if not rows or not best:
        return {"summary": "暂无可解释的寻优结果。", "reasons": [], "caution": ""}

    best_metrics = best["metrics"]
    base_metrics = baseline["metrics"]
    energy_delta = best_metrics["avg_energy"] - base_metrics["avg_energy"]
    temp_delta = best_metrics["max_temp"] - base_metrics["max_temp"]
    energy_pct = energy_delta / base_metrics["avg_energy"] * 100 if base_metrics["avg_energy"] else 0.0

    summary = (
        "%d 组方案中「%s」综合最优：平均能耗 %.0f W（较默认 %+.1f%%），最高温度 %.2f °C（较默认 %+.2f °C），"
        "服务率 %.1f%%。规模为 %d 台 / %d 小时，固定随机种子 %d。"
        % (sweep.get("candidates", len(rows)), best["name"], best_metrics["avg_energy"], energy_pct,
           best_metrics["max_temp"], temp_delta, best_metrics["service_rate"] * 100,
           sweep["scale"]["num_servers"], sweep["scale"]["simulation_hours"], sweep["scale"]["seed"])
    )

    reasons: List[str] = []
    base_weights = baseline.get("weights", {})
    changes = sorted(
        ((key, float(best.get("weights", {}).get(key, 0)) - float(base_weights.get(key, 0))) for key in WEIGHT_KEYS),
        key=lambda item: abs(item[1]),
        reverse=True,
    )
    if changes:
        key, delta = changes[0]
        direction = "提高" if delta > 0 else "降低"
        reasons.append(
            "该方案把 %s 从 %.2f %s到 %.2f，是相对默认权重最明显的调整；对应最高温度变化 %+.2f °C、能耗变化 %+.1f%%。"
            % (key, float(base_weights.get(key, 0)), direction, float(best.get("weights", {}).get(key, 0)), temp_delta, energy_pct)
        )

    thermal_rows = sorted(rows, key=lambda row: float(row.get("weights", {}).get("gamma_thermal", 0)), reverse=True)
    hottest_weight = thermal_rows[0] if thermal_rows else None
    if hottest_weight and hottest_weight["name"] != best["name"]:
        reasons.append(
            "对照发现「%s」把 gamma_thermal 加到 %.2f，但最高温度 %.2f °C 并不比最优方案 %.2f °C 更低，"
            "说明单纯加大温度权重会让调度过度保守、任务积压，并不能换取更低的温度。"
            % (hottest_weight["name"], float(hottest_weight.get("weights", {}).get("gamma_thermal", 0)),
               hottest_weight["metrics"]["max_temp"], best_metrics["max_temp"])
        )

    spread = max(row["metrics"]["avg_energy"] for row in rows) - min(row["metrics"]["avg_energy"] for row in rows)
    reasons.append(
        "全部候选方案的能耗极差为 %.0f W，说明在当前规模下权重对能耗的影响幅度有限，主要收益来自温度侧。"
        % spread
    )

    caution = (
        "本结论基于 %d 台 / %d 小时 / seed=%d 的单场景搜索，换规模或换种子后最优组合可能变化，"
        "正式结论建议至少在两种规模上复跑确认。"
        % (sweep["scale"]["num_servers"], sweep["scale"]["simulation_hours"], sweep["scale"]["seed"])
    )
    return {"summary": summary, "reasons": reasons[:3], "caution": caution, "source": "rule"}


TUNE_SYSTEM = """你是数据中心能效调度专家。用户给出 DAMO-ETS 权重寻优实验的结果表，
请输出 JSON：{"summary": "一句总结", "reasons": ["机理分析，最多3条"], "caution": "适用边界"}。
要求：只能引用给定数据，不要编造数值；要指出最优方案与默认权重的差异及其代价。
算法名称用中文或规范写法（贪心算法、遗传算法、轮询算法、DAMO-ETS、NSGA-II、MOEA-D），
权重参数用中文名（α 截止权重、β 能耗权重、γ 温度权重、δ 异构权重、ε 迁移权重）。"""


class AIService:
    def __init__(self) -> None:
        self.api_key, self.key_source, model_override = load_credentials()
        self.base_url = os.environ.get("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        self.model = model_override or os.environ.get("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL
        self.timeout = float(os.environ.get("DEEPSEEK_TIMEOUT", "90"))
        self.max_tokens = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "4096"))
        effort = os.environ.get("DEEPSEEK_REASONING_EFFORT", "none").strip().lower()
        self.reasoning_effort = "" if effort in ("", "auto", "default") else effort
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.last_error = ""
        self.last_ok_at = ""

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def reload_key(self) -> None:
        self.api_key, self.key_source, model_override = load_credentials()
        self.model = model_override or os.environ.get("DEEPSEEK_MODEL", "").strip() or DEFAULT_MODEL

    def status(self) -> Dict[str, Any]:
        reason = ""
        if not self.available:
            reason = "未配置 DEEPSEEK_API_KEY，当前使用本地专家规则"
        elif self.last_error:
            reason = "大模型最近一次调用失败，已回退本地规则"
        return {
            "available": self.available,
            "model": self.model,
            "key_source": self.key_source,
            "last_error": self.last_error,
            "last_ok_at": self.last_ok_at,
            "reason": reason,
        }

    def _post(self, payload: Dict[str, Any], use_env_proxy: bool) -> Dict[str, Any]:
        session = requests.Session()
        if not use_env_proxy:
            session.trust_env = False
        response = session.post(
            self.base_url + "/chat/completions",
            headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=self.timeout,
        )
        if response.status_code == 400 and "reasoning_effort" in response.text:
            logger.warning("当前接口不支持 reasoning_effort, 已自动去掉该参数重试")
            self.reasoning_effort = ""
            retry_payload = dict(payload)
            retry_payload.pop("reasoning_effort", None)
            return self._post(retry_payload, use_env_proxy)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def short_error(exc: Exception) -> str:
        text = str(exc)
        lowered = text.lower()
        if "token 预算" in text:
            return text
        if "proxy" in lowered:
            return "环境代理不可用，直连也失败，请检查网络或代理设置"
        if "timed out" in lowered or "timeout" in lowered:
            return "连接 DeepSeek 超时，请检查网络"
        if "401" in text or "unauthorized" in lowered or "invalid api key" in lowered:
            return "API Key 无效或已过期"
        if "402" in text or "insufficient balance" in lowered:
            return "DeepSeek 账户余额不足"
        if "403" in text:
            return "API Key 无权访问该模型"
        return text[:120]

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        return self.chat_messages(system, [{"role": "user", "content": user}], json_mode)

    def chat_messages(self, system: str, messages: List[Dict[str, str]],
                      json_mode: bool = False) -> str:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "temperature": 0.3,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort

        try:
            data = self._post(payload, True)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            logger.warning("经环境代理访问 DeepSeek 失败, 尝试直连: %s" % exc)
            data = self._post(payload, False)

        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if not content.strip():
            if choice.get("finish_reason") == "length":
                raise RuntimeError(
                    "模型思考占满了 token 预算, 未能给出结论; 可设置 DEEPSEEK_REASONING_EFFORT=none 关闭思考, 或提高 DEEPSEEK_MAX_TOKENS (当前 %d)" % self.max_tokens
                )
            raise RuntimeError("模型返回了空内容")
        return content

    @staticmethod
    def _extract_json(text: str) -> Dict[str, Any]:
        text = text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        return json.loads(text)

    def analyze(self, stats: Dict[str, Dict[str, Any]], weights: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        fallback = rule_analyze(stats, weights)
        if not self.available:
            return fallback

        cache_key = json.dumps(stats, sort_keys=True)
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        payload = {
            "当前指标": stats,
            "DAMO-ETS 当前权重": weights or {},
            "对照基线": "round_robin（轮询）",
        }
        try:
            content = self.chat(ANALYZE_SYSTEM, json.dumps(payload, ensure_ascii=False), json_mode=True)
            parsed = self._extract_json(content)
            result = {
                "summary": str(parsed.get("summary") or fallback["summary"]),
                "best_algorithm": str(parsed.get("best_algorithm") or fallback["best_algorithm"]),
                "risks": list(parsed.get("risks") or fallback["risks"])[:4],
                "tuning": dict(parsed.get("tuning") or fallback["tuning"]),
                "actions": list(parsed.get("actions") or fallback["actions"])[:4],
                "source": "llm",
                "model": self.model,
            }
            self._cache[cache_key] = result
            self.last_error = ""
            self.last_ok_at = time.strftime("%Y-%m-%d %H:%M:%S")
            return localize_result(result)
        except Exception as exc:
            logger.error("DeepSeek 诊断失败，回退本地规则: %s" % exc)
            self.last_error = self.short_error(exc)
            fallback["source"] = "rule"
            fallback["notice"] = "大模型调用失败，已回退本地专家规则"
            return localize_result(fallback)

    def explain_tuning(self, sweep: Dict[str, Any]) -> Dict[str, Any]:
        fallback = rule_explain_tuning(sweep)
        if not self.available:
            return localize_result(fallback)
        try:
            payload = {
                "择优规则": sweep.get("rule"),
                "实验规模": sweep.get("scale"),
                "结果表": [
                    {"方案": row["name"], "权重": row["weights"], "指标": row["metrics"]}
                    for row in sweep.get("rows", [])
                ],
                "最优方案": sweep.get("best", {}).get("name"),
                "默认方案": sweep.get("baseline", {}).get("name"),
            }
            content = self.chat(TUNE_SYSTEM, json.dumps(payload, ensure_ascii=False), json_mode=True)
            parsed = self._extract_json(content)
            return localize_result({
                "summary": str(parsed.get("summary") or fallback["summary"]),
                "reasons": list(parsed.get("reasons") or fallback["reasons"])[:3],
                "caution": str(parsed.get("caution") or fallback["caution"]),
                "source": "llm",
                "model": self.model,
            })
        except Exception as exc:
            logger.error("DeepSeek 寻优解释失败，回退本地规则: %s" % exc)
            self.last_error = self.short_error(exc)
            fallback["notice"] = "大模型调用失败，已回退本地规则解释"
            return localize_result(fallback)

    def parse_intent(self, text: str, defaults: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        fallback = rule_parse(text, defaults)
        if not self.available:
            return fallback
        try:
            content = self.chat(PARSE_SYSTEM, text, json_mode=True)
            parsed = self._extract_json(content)
            params = dict(fallback["params"])
            for key in ("num_servers", "simulation_hours", "run_mode"):
                if parsed.get(key):
                    params[key] = parsed[key]
            if parsed.get("algorithms"):
                valid = [a for a in parsed["algorithms"] if a in ALL_ALGORITHMS]
                if valid:
                    params["algorithms"] = valid
            notes = [localize(item) for item in fallback["notes"]]
            return {"params": params, "notes": notes, "source": "llm"}
        except Exception as exc:
            logger.error("DeepSeek 参数解析失败，回退本地规则: %s" % exc)
            self.last_error = self.short_error(exc)
            fallback["notice"] = "大模型调用失败，已回退本地规则解析"
            return fallback


_service: Optional[AIService] = None


def _fmt(value: Any, digits: int = 3) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "--"


def _pct_value(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}"
    except (TypeError, ValueError):
        return "--"


def _snapshot_reply(snapshot: Dict[str, Any]) -> str:
    names = "、".join(DISPLAY_NAMES.get(item, item) for item in snapshot.get("algorithms") or [])
    return (
        "当前回答基于最新一次运行：%s，%s 台服务器 × %s 小时，参与算法 %s。数据生成时间 %s。"
        "你重新点「开始运行」后，我会自动读取新结果。"
        % (
            snapshot.get("site") or "默认站点",
            snapshot.get("num_servers") or "未知",
            snapshot.get("simulation_hours") or "未知",
            names or "未知",
            snapshot.get("generated_at") or "未知",
        )
    )


def _best_hours_reply(insights: Dict[str, Any]) -> Optional[str]:
    safe_items = insights.get("safe_best_hours") or []
    overall_items = insights.get("overall_best_hours") or []
    items = safe_items or overall_items
    if not items:
        return None
    top = items[0]
    lines = [
        "按单位服务负载能耗，当前配置下性价比最高的时段是第 %d 小时：%s，%s W/负载，峰值温度 %s℃，平均 PUE %s，服务率 %s%%。"
        % (
            int(top.get("hour") or 0) + 1,
            DISPLAY_NAMES.get(top.get("algorithm"), top.get("algorithm") or "未知算法"),
            _fmt(top.get("energy_per_load")),
            _fmt(top.get("peak_temp"), 1),
            _fmt(top.get("pue")),
            _pct_value(top.get("service_rate")),
        )
    ]
    for item in items[1:3]:
        lines.append(
            "- 第 %d 小时：%s，%s W/负载，峰值温度 %s℃"
            % (
                int(item.get("hour") or 0) + 1,
                DISPLAY_NAMES.get(item.get("algorithm"), item.get("algorithm") or "未知算法"),
                _fmt(item.get("energy_per_load")),
                _fmt(item.get("peak_temp"), 1),
            )
        )
    if safe_items:
        lines.append("以上时段已排除峰值温度达到 70℃ 或出现过热服务器的时段。")
    if safe_items and overall_items:
        raw = overall_items[0]
        raw_key = (raw.get("hour"), raw.get("algorithm"))
        safe_key = (top.get("hour"), top.get("algorithm"))
        if raw_key != safe_key:
            lines.append(
                "纯能耗最低的是第 %d 小时（%s，%s W/负载），但峰值温度 %s℃，不建议作为常规配置。"
                % (
                    int(raw.get("hour") or 0) + 1,
                    DISPLAY_NAMES.get(raw.get("algorithm"), raw.get("algorithm") or "未知算法"),
                    _fmt(raw.get("energy_per_load")),
                    _fmt(raw.get("peak_temp"), 1),
                )
            )
    lines.append("这是当前一次仿真的观测结果；换城市、服务器数量或时长后需要重新运行。")
    return "\n".join(lines)


def _temperature_reply(insights: Dict[str, Any], threshold: Optional[float] = None,
                       above: bool = False) -> Optional[str]:
    if threshold is not None:
        key = "temperature_thresholds_above" if above else "temperature_thresholds"
        items = insights.get(key) or []
        if not items:
            return None
        record = min(items, key=lambda item: abs(float(item.get("threshold_c") or 0) - threshold))
        level = int(record.get("threshold_c") or 0)
        best = record.get("best_algorithm")
        data = (record.get("by_algorithm") or {}).get(best) if best else None
        if not best or not data:
            return "当前数据在这个温区没有足够样本，暂时给不出可靠建议。"
        hours = record.get("hours_above" if above else "hours_below") or 0
        direction = "高于或等于" if above else "低于"
        text = (
            "当峰值温度%s %d℃ 时，当前数据覆盖 %d 个运行小时；表现最好的是 %s："
            "单位服务负载能耗 %s W/负载，平均 PUE %s，服务率 %s%%，平均风扇转速 %s。"
            % (
                direction, level, hours,
                DISPLAY_NAMES.get(best, best),
                _fmt(data.get("avg_energy_per_load")),
                _fmt(data.get("avg_pue")),
                _pct_value(data.get("avg_service_rate")),
                _fmt(data.get("avg_fan_speed"), 2),
            )
        )
        if not record.get("best_reliable"):
            text += " 该温区服务率偏低，结论只作参考。"
        text += (
            "\n建议：这个温区优先用 %s；低温时可适当整合负载、减少空转服务器，"
            "高温时保留散热余量。当前数据没有验证改变风扇曲线后的效果，调整前建议重新仿真。"
            % DISPLAY_NAMES.get(best, best)
        )
        return text

    bands = insights.get("temperature_bands") or []
    lines = ["按当前数据，不同峰值温度区间的配置建议："]
    for band in bands:
        best = band.get("best_algorithm")
        data = (band.get("by_algorithm") or {}).get(best) if best else None
        if not best or not data:
            continue
        line = (
            "- %s（%d 小时）：优先 %s，单位服务负载能耗 %s W/负载，PUE %s，服务率 %s%%，风扇转速 %s。"
            % (
                band.get("label") or "未知温区",
                int(band.get("hours") or 0),
                DISPLAY_NAMES.get(best, best),
                _fmt(data.get("avg_energy_per_load")),
                _fmt(data.get("avg_pue")),
                _pct_value(data.get("avg_service_rate")),
                _fmt(data.get("avg_fan_speed"), 2),
            )
        )
        if not band.get("best_reliable"):
            line += " 服务率偏低，仅作参考。"
        lines.append(line)
    if len(lines) == 1:
        return None
    lines.append("低温区间可适当整合负载、减少空转服务器；高温区间要保留散热余量。以上是观测建议，改变配置前建议重新仿真验证。")
    return "\n".join(lines)


def _config_advice_reply(text: str, context: Dict[str, Any]) -> str:
    insights = context.get("insights") or {}
    snapshot = insights.get("snapshot") or context.get("data_snapshot") or {}
    config = context.get("config") or {}
    relationships = insights.get("relationships") or {}
    best = insights.get("overall_best")
    lines = ["当前数据没有实际运行过这个配置，我只能给出基于现有观测的方向性判断。"]
    if snapshot:
        lines.append(
            "当前快照：%s，%s 台服务器 × %s 小时。"
            % (snapshot.get("site") or "默认站点", snapshot.get("num_servers") or "未知",
               snapshot.get("simulation_hours") or "未知")
        )
    if best:
        lines.append("当前表现最好的算法是 %s。" % DISPLAY_NAMES.get(best, best))
    pairs = []
    if relationships.get("load_vs_peak_temp") is not None:
        pairs.append("负载与峰值温度相关系数 %s" % _fmt(relationships["load_vs_peak_temp"], 3))
    if relationships.get("ambient_vs_pue") is not None:
        pairs.append("环境温度与 PUE 相关系数 %s" % _fmt(relationships["ambient_vs_pue"], 3))
    if relationships.get("fan_vs_cooling") is not None:
        pairs.append("风扇转速与冷却功率相关系数 %s" % _fmt(relationships["fan_vs_cooling"], 3))
    if pairs:
        lines.append("当前观测：" + "，".join(pairs) + "。这些只是相关性，不是因果结论。")

    if any(word in text for word in ("服务器", "台", "节点", "冗余", "关掉")):
        lines.append("调整服务器数量会同时影响整合程度、空闲功耗和服务率：减少服务器可能省电，但当前服务率本就不高，继续减少大概率增加未分配任务；增加服务器能缓解容量瓶颈，但空闲功耗会抬高单位服务负载能耗。")
    elif any(word in text for word in ("风扇", "散热", "冷却")):
        lines.append("降低风扇转速或冷却功率会减少冷却能耗，但散热余量变小，峰值温度和过热小时数可能上升；高温时段的风险最大。")
    elif any(word in text for word in ("算法", "nsga", "moea", "damo", "贪心", "遗传", "轮询")):
        lines.append("更换算法会改变任务分配和热点分布，方向取决于当前瓶颈：容量不足时先看服务率，温度接近上限时先看峰值温度和过热小时数。")

    server_range = config.get("num_server_range") or []
    fan_range = config.get("fan_speed_range") or []
    if len(server_range) == 2 and server_range[0] is not None and server_range[1] is not None:
        lines.append(
            "当前允许的服务器数量范围是 %s–%s 台%s。"
            % (
                server_range[0], server_range[1],
                "，风扇转速范围是 %s–%s" % (fan_range[0], fan_range[1]) if len(fan_range) == 2 else "",
            )
        )
    lines.append(
        "要得到具体数值，需要按新参数重新运行仿真。网页表单可直接改服务器数量、时长、运行模式和算法；"
        "风扇曲线、冷却效率、算法权重需要修改配置文件（config 目录下的 yaml 文件）后重新运行。"
    )
    return "\n".join(lines)


def rule_chat(text: str, context: Dict[str, Any]) -> str:
    """Local fallback when the model is unavailable.

    It never invents numbers: every figure comes from the supplied context.
    """
    stats = context.get("stats") or {}
    ranking = context.get("ranking") or {}
    source = context.get("data_source") or {}
    insights = context.get("insights") or {}
    snapshot = insights.get("snapshot") or context.get("data_snapshot") or {}
    best = ranking.get("best")

    if any(word in text for word in ("数据来源", "哪来的", "出处", "真实吗", "可靠")):
        return ("气温来自 Open-Meteo/ERA5 的 345 个城市逐日数据，任务流来自阿里云 2025 GPU 集群轨迹（第 19-25 天窗口），"
                "碳因子来自生态环境部 2021 年公告。每一项都能在 /api/v1/data-source 里查到发布方、"
                "URL 与哈希。")

    if snapshot and any(word in text for word in ("更新", "最新", "这次运行", "本次运行", "刚才跑", "数据变")):
        return _snapshot_reply(snapshot)

    if any(word in text for word in ("怎么用", "如何", "操作", "运行", "开始")):
        return ("在「仿真控制」里先选城市（可输入一个字搜索），需要的话再选学校，然后点「开始运行」。"
                "结果会出现在下面的图表、指标表和受控排名里。")

    temperature_words = any(word in text for word in ("温度", "散热", "风扇", "配置", "温区"))
    temperature_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:度|℃|°c)", text, re.IGNORECASE)
    if temperature_words and temperature_match:
        above = any(word in text for word in ("高于", "超过", "以上"))
        reply = _temperature_reply(insights, float(temperature_match.group(1)), above=above)
        if reply:
            return reply

    if temperature_words and any(word in text for word in ("配置", "建议", "低于", "高于", "超过", "温区", "怎么", "如何")):
        reply = _temperature_reply(insights)
        if reply:
            return reply

    time_words = ("什么时候", "哪个时段", "最佳时间", "时段", "几点", "哪一小时", "性价比", "最划算")
    if any(word in text for word in time_words):
        reply = _best_hours_reply(insights)
        if reply:
            return reply

    config_words = ("如果", "改成", "增加", "减少", "调到", "风扇", "冷却", "冗余", "配置", "关掉", "换成", "试试")
    if any(word in text for word in config_words):
        return _config_advice_reply(text, context)

    if best and best in stats:
        metrics = stats[best]
        if any(word in text for word in ("温度", "热", "峰值")):
            return ("当前 %s 的峰值温度是 %.1f℃，95 分位 %.1f℃，平均 PUE %.3f。" % (
                DISPLAY_NAMES.get(best, best), float(metrics.get("max_temp") or 0),
                float(metrics.get("p95_temp") or 0), float(metrics.get("avg_pue") or 0)))
        if any(word in text for word in ("pue", "能效")):
            return ("当前 %s 的平均 PUE 是 %.3f，服务率 %.2f%%。" % (
                DISPLAY_NAMES.get(best, best), float(metrics.get("avg_pue") or 0), float(metrics.get("service_rate") or 0) * 100))
        if any(word in text for word in ("服务率", "丢", "违约", "deadline")):
            return ("当前 %s 的任务服务率 %.2f%%，累计 deadline 违约 %d 次。" % (
                DISPLAY_NAMES.get(best, best), float(metrics.get("service_rate") or 0) * 100,
                int(metrics.get("deadline_violations") or 0)))
        return ("当前站点 %s（%s），%s 排名第一：单位服务负载能耗 %.4f W/负载，"
                "服务率 %.2f%%，峰值温度 %.1f℃，平均 PUE %.3f。" % (
                    source.get("site_id") or "默认",
                    source.get("emission_region") or "未知区域",
                    DISPLAY_NAMES.get(best, best),
                    float(metrics.get("energy_per_served_load") or 0),
                    float(metrics.get("service_rate") or 0) * 100,
                    float(metrics.get("max_temp") or 0),
                    float(metrics.get("avg_pue") or 0)))

    return ("当前还没有可用的仿真结果。先点「开始运行」跑一次，我就能基于结果回答能耗、温度、"
            "PUE、服务率和排名问题。")


def get_ai_service() -> AIService:
    global _service
    if _service is None:
        _service = AIService()
    return _service

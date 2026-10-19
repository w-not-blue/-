"""End-to-end checks for the floating assistant's chat endpoint.

Run with the web service up:
    python tools/test_ai_chat.py
"""

import io
import json
import os
import sys
import time
from typing import Any, Dict, List

import requests

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:5000"
INTERNAL_FIELDS = ("max_temp", "avg_pue", "energy_per_served_load", "service_rate")
CODE_IDS = ("round_robin", "damo_ets", "greedy", "genetic", "nsga2", "moead")

RESULTS: List[Dict[str, Any]] = []
TRANSCRIPT: List[Dict[str, Any]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail})


def chat(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    started = time.time()
    response = requests.post(BASE + "/api/v1/ai/chat",
                             json={"messages": messages}, timeout=180)
    response.raise_for_status()
    payload = response.json()
    payload["_elapsed"] = round(time.time() - started, 2)
    return payload


def hygiene(reply: str) -> None:
    lowered = reply.lower()
    found_fields = [w for w in INTERNAL_FIELDS if w in lowered]
    found_ids = [w for w in CODE_IDS if w in lowered]
    check("无内部字段名", not found_fields, ", ".join(found_fields))
    check("无代码标识符", not found_ids, ", ".join(found_ids))


def main() -> int:
    print("=" * 64)
    print("AI 助手对话测试")
    print("=" * 64)

    status = requests.get(BASE + "/api/v1/ai/status", timeout=30).json()
    info = status.get("status", {})
    print("\n[0] 服务状态")
    print("  available=%s model=%s key_source=%s" % (
        info.get("available"), info.get("model"), info.get("key_source")))
    check("模型可用", bool(info.get("available")))
    check("Key 来自文件", info.get("key_source") == "file", str(info.get("key_source")))

    metrics = requests.get(BASE + "/api/v1/metrics", timeout=30).json()
    stats = metrics.get("data", {})
    ranking = metrics.get("ranking", {})
    best = ranking.get("best")
    best_metrics = stats.get(best, {}) if best else {}
    print("\n[1] 基准数据（/api/v1/metrics）")
    for algo, m in stats.items():
        print("  %-11s rate=%.4f W/load=%-7s peak=%-6s pue=%-7s co2=%s" % (
            algo, float(m.get("service_rate") or 0), m.get("energy_per_served_load"),
            m.get("max_temp"), m.get("avg_pue"), m.get("co2_kg")))
    print("  best=%s" % best)

    print("\n[2] 单轮问答")
    site = (metrics.get("data_source", {}) or {}).get("site_id") or ""
    cases = [
        ("排名/最省电", "当前排名如何？哪个算法最省电？",
         [best.replace("_", "-").upper()] if best else []),
        ("温度/PUE", "各算法的峰值温度和 PUE 是多少？", []),
        ("服务率", "任务服务率怎么样？有没有任务被丢弃？", []),
        ("碳排", "当前碳排是多少？", []),
        ("数据来源", "这些数据是哪来的？可靠吗？", ["ERA5", "阿里云", "生态环境部"]),
        ("使用方法", "怎么运行一次仿真？", []),
        ("数据更新", "当前数据更新了吗？", [site] if site else []),
        ("最佳时段", "什么时候性价比最高？", ["小时"]),
        ("低温配置", "温度低于60度怎么配置？", ["60"]),
        ("配置试算", "如果服务器增加到24台会怎样？", ["24"]),
    ]

    for label, question, must_contain in cases:
        payload = chat([{"role": "user", "content": question}])
        reply = payload.get("reply", "")
        print("\n  [%s] %s" % (label, question))
        print("    source=%s  %.2fs" % (payload.get("source"), payload.get("_elapsed", 0)))
        print("    " + reply.replace("\n", "\n    "))
        TRANSCRIPT.append({
            "label": label, "question": question, "reply": reply,
            "source": payload.get("source"), "elapsed": payload.get("_elapsed"),
        })
        check("%s: 有回复" % label, bool(reply.strip()), "empty")
        check("%s: 走大模型" % label, payload.get("source") == "deepseek",
              str(payload.get("source")))
        check("%s: 返回数据快照" % label,
              bool((payload.get("snapshot") or {}).get("generated_at")),
              str(payload.get("snapshot")))
        check("%s: 无内部字段名" % label,
              not any(word in reply.lower() for word in INTERNAL_FIELDS),
              ", ".join(word for word in INTERNAL_FIELDS if word in reply.lower()))
        if label == "配置试算":
            run = payload.get("suggested_run") or {}
            check("配置试算: 返回建议配置",
                  (run.get("params") or {}).get("num_servers") == 24,
                  str(run))
        for token in must_contain:
            if token:
                check("%s: 提到 %s" % (label, token), token.lower() in reply.lower(), token)

    print("\n[3] 数值准确性")
    payload = chat([{"role": "user", "content": "排名第一的算法，单位负载能耗、峰值温度、PUE 各是多少？"}])
    reply = payload.get("reply", "")
    print("    " + reply.replace("\n", "\n    "))
    if best_metrics:
        intensity = float(best_metrics.get("energy_per_served_load") or 0)
        peak = float(best_metrics.get("max_temp") or 0)
        pue = float(best_metrics.get("avg_pue") or 0)
        check("能耗数值一致", (str(round(intensity, 2)) in reply) or (str(round(intensity, 1)) in reply),
              "expect %.4f" % intensity)
        check("峰值温度一致", str(round(peak, 1)) in reply or str(int(peak)) in reply,
              "expect %.2f" % peak)
        check("PUE 一致", str(round(pue, 3)) in reply, "expect %.4f" % pue)

    print("\n[4] 多轮上下文")
    history = [{"role": "user", "content": "当前排名第一的是谁？"}]
    first = chat(history)
    print("  Q1: 当前排名第一的是谁？")
    print("    " + first.get("reply", "").replace("\n", "\n    "))
    history.append({"role": "assistant", "content": first.get("reply", "")})
    history.append({"role": "user", "content": "它的峰值温度是多少？"})
    second = chat(history)
    print("  Q2: 它的峰值温度是多少？")
    print("    " + second.get("reply", "").replace("\n", "\n    "))
    check("多轮指代正确", "DAMO" in second.get("reply", "").upper(),
          second.get("reply", "")[:80])

    print("\n[5] 格式规范")
    for payload in (first, second):
        hygiene(payload.get("reply", ""))

    print("\n[6] 本地兜底（模型不可用时）")
    try:
        from src.services.ai_service import rule_chat
        fallback = rule_chat("当前排名如何？", {
            "stats": stats,
            "ranking": ranking,
            "data_source": metrics.get("data_source", {}),
        })
        print("    " + fallback.replace("\n", "\n    "))
        check("兜底有回复", bool(fallback.strip()))
        check("兜底无内部字段名",
              not any(w in fallback.lower() for w in INTERNAL_FIELDS))
    except Exception as exc:
        check("兜底可调用", False, str(exc))

    passed = sum(1 for item in RESULTS if item["ok"])
    report_path = os.path.join(PROJECT_ROOT, "AI对话测试报告.md")
    lines = ["# AI 助手对话测试报告", "",
             "- 模型：%s" % info.get("model"),
             "- Key 来源：%s" % info.get("key_source"),
             "- 结果：**%d / %d 项通过**" % (passed, len(RESULTS)), ""]
    lines.append("## 基准数据")
    lines.append("")
    lines.append("| 算法 | 服务率 | 单位负载能耗 | 峰值温度 | PUE |")
    lines.append("|---|---|---|---|---|")
    for algo, m in stats.items():
        lines.append("| %s | %.4f | %s | %s | %.4f |" % (
            algo, float(m.get("service_rate") or 0), m.get("energy_per_served_load"),
            m.get("max_temp"), float(m.get("avg_pue") or 0)))
    lines.append("")
    lines.append("## 对话记录")
    for item in TRANSCRIPT:
        lines.append("")
        lines.append("### %s" % item["label"])
        lines.append("")
        lines.append("**问：** %s" % item["question"])
        lines.append("")
        lines.append("**答（%s，%.2fs）：**" % (item["source"], item["elapsed"] or 0))
        lines.append("")
        lines.append(item["reply"].replace("\n", "\n\n"))
    lines.append("")
    lines.append("## 检查项")
    lines.append("")
    for item in RESULTS:
        lines.append("- %s %s%s" % ("✅" if item["ok"] else "❌", item["name"],
                                    ("（%s）" % item["detail"]) if not item["ok"] else ""))
    with io.open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n报告已写入: %s" % report_path)
    print("\n" + "=" * 64)
    print("汇总: %d / %d 项通过" % (passed, len(RESULTS)))
    for item in RESULTS:
        if not item["ok"]:
            print("  FAIL: %s (%s)" % (item["name"], item["detail"]))
    print("=" * 64)
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())

"""Tests for the floating assistant's chat backend."""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import pandas as pd

from src.services.ai_service import rule_chat
from src.services.simulation_insights import build_insights
from src.simulation.metrics import rank_algorithms, summarize


def _context():
    stats = {
        "damo_ets": {
            "service_rate": 0.9912, "energy_per_served_load": 5.2333,
            "max_temp": 54.4, "p95_temp": 48.0, "avg_pue": 1.4638,
            "deadline_violations": 3,
        },
        "greedy": {
            "service_rate": 1.0, "energy_per_served_load": 5.604,
            "max_temp": 56.6, "p95_temp": 50.0, "avg_pue": 1.4509,
            "deadline_violations": 0,
        },
    }
    return {
        "stats": stats,
        "ranking": rank_algorithms(stats),
        "data_source": {"site_id": "北京市", "emission_region": "华北"},
    }



def _insight_context():
    rows = []
    for algorithm, values in {
        "greedy": [(10.0, 50.0, 1.50), (7.0, 60.0, 1.45), (5.0, 70.0, 1.40)],
        "damo_ets": [(8.0, 50.0, 1.42), (6.0, 60.0, 1.38), (5.5, 65.0, 1.35)],
    }.items():
        for hour, (intensity, temperature, pue) in enumerate(values):
            rows.append({
                "time": hour,
                "algorithm": algorithm,
                "total_load": 10.0,
                "served_load": 10.0,
                "total_energy": intensity * 10.0,
                "pue": pue,
                "peak_temperature": temperature,
                "avg_temperature": temperature - 2,
                "ambient_temperature": 25.0,
                "fan_speed": 0.5,
                "cooling_power": 100.0,
                "assigned": 10,
                "dropped": 0,
                "overheated_servers": 0,
            })
    df = pd.DataFrame(rows)
    meta = {
        "site_id": "测试市",
        "site_province": "测试省",
        "emission_region": "华北",
        "num_servers": 10,
        "simulation_hours": 3,
        "controlled": True,
    }
    insights = build_insights(df, meta, generated_at="2026-10-18 10:00:00")
    stats = summarize(df)
    return {
        "stats": stats,
        "ranking": rank_algorithms(stats),
        "insights": insights,
        "data_source": meta,
    }


def test_insights_pick_distinct_best_hours():
    insights = _insight_context()["insights"]
    first = insights["overall_best_hours"][0]
    assert first["hour"] == 2
    assert first["algorithm"] == "greedy"
    assert first["energy_per_load"] == 5.0
    assert insights["safe_best_hours"][0]["hour"] == 2


def test_insights_build_temperature_thresholds():
    insights = _insight_context()["insights"]
    threshold = next(item for item in insights["temperature_thresholds"] if item["threshold_c"] == 55)
    assert threshold["hours_below"] == 1
    assert threshold["best_algorithm"] == "damo_ets"


def test_rule_chat_answers_best_time():
    reply = rule_chat("什么时候性价比最高？", _insight_context())
    assert "第 3 小时" in reply
    assert "贪心算法" in reply


def test_rule_chat_answers_temperature_configuration():
    reply = rule_chat("温度低于55度怎么配置？", _insight_context())
    assert "低于 55℃" in reply
    assert "DAMO-ETS" in reply
    assert "只作参考" not in reply


def test_rule_chat_reports_latest_snapshot():
    reply = rule_chat("当前数据更新了吗？", _insight_context())
    assert "测试市" in reply
    assert "2026-10-18 10:00:00" in reply


def test_rule_chat_handles_free_form_configuration():
    reply = rule_chat("如果把服务器增加到24台会怎样？", _insight_context())
    assert "方向性判断" in reply
    assert "服务器数量" in reply
    assert "重新运行仿真" in reply


def test_suggest_run_keeps_current_defaults():
    from src.api.ai_api import _suggest_run
    context = _insight_context()
    context["data_snapshot"] = context["insights"]["snapshot"]
    context["config"] = {"num_server_range": [1, 1000], "fan_speed_range": [0.2, 1.0]}
    run = _suggest_run("如果服务器增加到24台会怎样？", context)
    assert run is not None
    assert run["params"]["num_servers"] == 24
    assert run["params"]["simulation_hours"] == 3
    assert run["params"]["algorithms"] == ["greedy", "damo_ets"]


def test_suggest_run_for_algorithm_change():
    from src.api.ai_api import _suggest_run
    context = _insight_context()
    context["data_snapshot"] = context["insights"]["snapshot"]
    context["config"] = {"num_server_range": [1, 1000], "fan_speed_range": [0.2, 1.0]}
    run = _suggest_run("如果换成NSGA-II和MOEA-D一起跑呢？", context)
    assert run is not None
    assert run["params"]["algorithms"] == ["nsga2", "moead"]
    assert run["params"]["num_servers"] == 10


def test_auto_run_budget_blocks_large_requests(monkeypatch):
    from src.api import ai_api

    class IdleManager:
        def active_run(self):
            return None

    monkeypatch.setattr(ai_api, "get_run_manager", lambda: IdleManager())
    budget = ai_api._auto_run_budget({"num_servers": 500, "simulation_hours": 24})
    assert budget["allowed"] is False
    assert "100" in budget["reason"]


def test_auto_run_budget_allows_small_requests(monkeypatch):
    from src.api import ai_api

    class IdleManager:
        def active_run(self):
            return None

    monkeypatch.setattr(ai_api, "get_run_manager", lambda: IdleManager())
    budget = ai_api._auto_run_budget({"num_servers": 24, "simulation_hours": 12})
    assert budget["allowed"] is True


def test_suggest_run_resolves_school_to_city():
    from src.api.ai_api import _suggest_run
    context = _insight_context()
    context["data_snapshot"] = dict(context["insights"]["snapshot"])
    context["data_snapshot"]["site"] = "上海市"
    context["data_snapshot"]["school_name"] = None
    context["config"] = {"num_server_range": [1, 1000], "fan_speed_range": [0.2, 1.0]}
    run = _suggest_run("用北京大学试算，服务器增加到30台会怎样？", context)
    assert run is not None
    assert run["params"]["school_name"] == "北京大学"
    assert run["params"]["site_id"] == "北京市"
    assert run["params"]["num_servers"] == 30


def test_suggest_run_allows_school_only_change():
    from src.api.ai_api import _suggest_run
    context = _insight_context()
    context["data_snapshot"] = dict(context["insights"]["snapshot"])
    context["data_snapshot"]["site"] = "上海市"
    context["data_snapshot"]["school_name"] = None
    context["config"] = {"num_server_range": [1, 1000], "fan_speed_range": [0.2, 1.0]}
    run = _suggest_run("用北京大学跑一下", context)
    assert run is not None
    assert run["params"]["school_name"] == "北京大学"
    assert run["params"]["site_id"] == "北京市"


def test_suggest_run_skips_identical_latest_config():
    from src.api.ai_api import _suggest_run
    context = _insight_context()
    context["data_snapshot"] = dict(context["insights"]["snapshot"])
    context["data_snapshot"].update({
        "site": "北京市",
        "school_name": "北京大学",
        "num_servers": 30,
        "simulation_hours": 24,
        "algorithms": ["greedy", "damo_ets"],
    })
    context["config"] = {"num_server_range": [1, 1000], "fan_speed_range": [0.2, 1.0]}
    run = _suggest_run("用北京大学试算，服务器增加到30台会怎样？", context)
    assert run is None

def test_rule_chat_names_the_best_algorithm():
    reply = rule_chat("哪个算法最省电？", _context())
    assert "DAMO-ETS" in reply
    assert "5.2333" in reply


def test_rule_chat_explains_data_source():
    reply = rule_chat("这些数据是哪来的？", _context())
    assert "ERA5" in reply or "阿里云" in reply
    assert "生态环境部" in reply


def test_rule_chat_without_results_asks_to_run():
    reply = rule_chat("排名", {"stats": {}, "ranking": {}, "data_source": {}})
    assert "开始运行" in reply


def test_ranking_reason_distinguishes_missing_intensity():
    ranking = rank_algorithms({
        "x": {"service_rate": 1.0, "energy_per_served_load": None, "max_temp": 50.0}
    })
    assert ranking["excluded"][0]["reason"] == "缺少单位服务负载能耗，无法参与排名"


def test_chat_endpoint_falls_back_when_model_unavailable(monkeypatch):
    import src.api.app  # noqa: F401  (registers the blueprint)
    from src.api import ai_api
    from src.api.app import app

    class Broken:
        model = "none"
        key_source = "none"

        def reload_key(self):
            pass

        def chat_messages(self, *args, **kwargs):
            raise RuntimeError("no network")

    monkeypatch.setattr(ai_api, "get_ai_service", lambda: Broken())
    client = app.test_client()
    response = client.post(
        "/api/v1/ai/chat",
        json={"messages": [{"role": "user", "content": "当前排名如何？"}]},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["source"] == "rule"
    assert payload["reply"]

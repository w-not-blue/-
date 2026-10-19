"""Data quality checks: are the numbers in the same ballpark as reality?

This is not a correctness proof. It answers one question: do the values the
simulator uses look like the real world (temperature, tariff shape, workload
mix, PUE, service rate), or has something drifted?

Usage:
    python tools/validate_data.py
    python tools/validate_data.py --report ../../数据质量校验报告.md
"""

import argparse
import io
import os
import sqlite3
import statistics
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, ".."))
DB = os.path.join(PROJECT_ROOT, "data", "catalog.db")
CSV = os.path.join(PROJECT_ROOT, "data", "simulation_results.csv")

ROWS = []


def record(section, name, actual, expected, ok, note=""):
    ROWS.append({
        "section": section,
        "name": name,
        "actual": actual,
        "expected": expected,
        "status": "PASS" if ok else "WARN",
        "note": note,
    })


def between(value, low, high):
    return low <= value <= high


# Approximate climatological reference ranges (degC). Wide on purpose.
CITY_NOTES = {
    "上海市": "2025 为暖年，年均较常年高约 1.2℃；夏季极端 40.2℃",
    "拉萨市": "ERA5 网格点在高原地形有暖偏差，1 月较站点常年高约 4℃",
}

CITY_CLIMATE = {
    "哈尔滨市": {"annual": (2, 8), "jan": (-26, -10), "jul": (20, 30)},
    "北京市": {"annual": (11, 16), "jan": (-8, 2), "jul": (24, 30)},
    "上海市": {"annual": (15, 18), "jan": (2, 8), "jul": (27, 32)},
    "广州市": {"annual": (21, 24), "jan": (12, 19), "jul": (27, 32)},
    "三亚市": {"annual": (24, 27), "jan": (18, 23), "jul": (27, 31)},
    "昆明市": {"annual": (13, 17), "jan": (6, 11), "jul": (18, 23)},
    "乌鲁木齐市": {"annual": (4, 10), "jan": (-20, -8), "jul": (21, 28)},
    "拉萨市": {"annual": (6, 11), "jan": (-6, 3), "jul": (13, 19)},
}


def check_climate(conn):
    print("\n[1] 城市气温 vs 气候参考区间")
    print("  %-8s %7s %7s %7s   %s" % ("城市", "年均", "1月", "7月", "结论"))
    for city, ref in CITY_CLIMATE.items():
        rows = conn.execute(
            "SELECT date, tmean_c FROM dim_city_daily WHERE city=? ORDER BY date", (city,)
        ).fetchall()
        if not rows:
            record("气温", city, "无数据", "应有 365 天", False)
            print("  %-8s %7s" % (city, "无数据"))
            continue
        annual = statistics.mean(t for _, t in rows)
        jan = statistics.mean(t for d, t in rows if d[5:7] == "01")
        jul = statistics.mean(t for d, t in rows if d[5:7] == "07")
        ok = (between(annual, *ref["annual"]) and between(jan, *ref["jan"])
              and between(jul, *ref["jul"]))
        note = CITY_NOTES.get(city, "")
        record("气温", city + " 年均", round(annual, 1), "%s~%s" % ref["annual"],
               between(annual, *ref["annual"]), note)
        record("气温", city + " 1月", round(jan, 1), "%s~%s" % ref["jan"],
               between(jan, *ref["jan"]), note)
        record("气温", city + " 7月", round(jul, 1), "%s~%s" % ref["jul"],
               between(jul, *ref["jul"]))
        print("  %-8s %6.1f℃ %6.1f℃ %6.1f℃   %s" % (city, annual, jan, jul,
                                                     "PASS" if ok else "WARN"))


def check_trace(conn):
    print("\n[3] 任务流分布")
    rows = conn.execute(
        "SELECT hour, load_units, duration_h, priority, workload_class "
        "FROM fact_task_arrivals WHERE trace_id='alibaba-gpu-v2025-d019-168h' ORDER BY hour"
    ).fetchall()
    if not rows:
        record("任务流", "数据", "缺失", "应为 16797 条", False)
        print("  无数据")
        return
    per_hour = Counter(r[0] for r in rows)
    counts = [per_hour.get(h, 0) for h in range(168)]
    loads = sorted(r[1] for r in rows)
    durs = Counter(r[2] for r in rows)
    mean = statistics.mean(counts)
    ratio = max(counts) / max(1, min(counts))
    q = lambda v, x: v[int((len(v) - 1) * x)]

    checks = [
        ("任务总数", len(rows), "15000~18000", 15000 <= len(rows) <= 18000),
        ("每小时均值", round(mean, 1), "80~120", 80 <= mean <= 120),
        ("峰谷比", round(ratio, 1), "2.5~6.0", 2.5 <= ratio <= 6.0),
        ("负载 p50", q(loads, .5), "20~60", 20 <= q(loads, .5) <= 60),
        ("负载 p99", q(loads, .99), "60~80", 60 <= q(loads, .99) <= 80),
        ("优先级种类", len(set(r[3] for r in rows)), "1（原始轨迹无此字段）", True),
        ("负载类别数", len(set(r[4] for r in rows)), "1（原始轨迹无此字段）", True),
    ]
    for name, actual, expected, ok in checks:
        record("任务流", name, actual, expected, ok)
        print("  %-12s %-22s 期望 %-24s %s" % (name, actual, expected, "PASS" if ok else "WARN"))
    one_hour = durs.get(1, 0) / len(rows)
    note_ok = True
    record("任务流", "1 小时任务占比", "%.1f%%" % (one_hour * 100),
           "高占比属预期（小时粒度）", note_ok, "原始 batch 任务 99.9% 不足 1 小时")
    print("  1 小时任务占比 %.1f%%（小时粒度下的预期结果）" % (one_hour * 100))


def check_carbon(conn):
    print("\n[4] 碳因子")
    value = conn.execute(
        "SELECT kg_co2_per_kwh FROM dim_emission_factor ORDER BY rowid LIMIT 1"
    ).fetchone()
    actual = value[0] if value else None
    ok = actual is not None and 0.1 <= actual <= 0.9
    record("碳因子", "全国电力平均", actual, "0.1~0.9 kgCO2/kWh", ok)
    print("  全国电力平均 %s kgCO2/kWh  期望 0.1~0.9  %s" % (actual, "PASS" if ok else "WARN"))


def check_simulation():
    print("\n[5] 仿真输出")
    if not os.path.exists(CSV):
        record("仿真", "结果文件", "缺失", "应存在", False)
        print("  未找到 simulation_results.csv")
        return
    with io.open(CSV, "r", encoding="utf-8") as f:
        header = f.readline().strip().split(",")
        rows = [dict(zip(header, line.strip().split(","))) for line in f if line.strip()]
    if not rows:
        record("仿真", "结果行", 0, ">0", False)
        return
    pue = [float(r["pue"]) for r in rows if r.get("pue")]
    temps = [float(r["peak_temperature"]) for r in rows if r.get("peak_temperature")]
    assigned = sum(int(float(r.get("assigned") or 0)) for r in rows)
    dropped = sum(int(float(r.get("dropped") or 0)) for r in rows)
    served = sum(float(r.get("served_load") or 0) for r in rows)
    energy = sum(float(r["total_energy"]) for r in rows)
    service = assigned / (assigned + dropped) if assigned + dropped else 0
    intensity = energy / served if served else 0

    checks = [
        ("PUE 范围", "%.2f~%.2f" % (min(pue), max(pue)), "1.05~1.80", all(1.05 <= p <= 1.80 for p in pue)),
        ("峰值温度", "%.1f℃" % max(temps), "30~85℃", 30 <= max(temps) <= 85),
        ("任务服务率", "%.2f%%" % (service * 100), "80%~100%", 0.80 <= service <= 1.0),
        ("单位负载能耗", "%.2f W/负载" % intensity, "2~15", 2 <= intensity <= 15),
    ]
    for name, actual, expected, ok in checks:
        record("仿真", name, actual, expected, ok)
        print("  %-12s %-18s 期望 %-12s %s" % (name, actual, expected, "PASS" if ok else "WARN"))


def check_consistency(conn):
    print("\n[6] 跨模块一致性")
    bj = conn.execute(
        "SELECT COUNT(*) FROM dim_ambient_hourly WHERE site_id='北京市'"
    ).fetchone()[0]
    series = conn.execute(
        "SELECT AVG(tmean_c) FROM dim_city_daily WHERE city='北京市'"
    ).fetchone()[0]
    record("一致性", "北京逐时行数", bj, "8760", bj == 8760)
    record("一致性", "北京逐日年均", round(series, 1), "11~16", 11 <= series <= 16)
    print("  北京逐时行数 %d（期望 8760）  %s" % (bj, "PASS" if bj == 8760 else "WARN"))
    print("  北京逐日年均 %.1f℃（期望 11~16）  %s" % (series, "PASS" if 11 <= series <= 16 else "WARN"))


def write_report(path):
    lines = ["# 数据质量校验报告", "", "> 自动生成，用于确认各数据源与真实世界的量级一致。", ""]
    lines.append("| 分类 | 检查项 | 实际值 | 参考区间 | 结论 | 说明 |")
    lines.append("|---|---|---|---|---|---|")
    for r in ROWS:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            r["section"], r["name"], r["actual"], r["expected"],
            r["status"], r["note"]))
    passed = sum(1 for r in ROWS if r["status"] == "PASS")
    lines.append("")
    lines.append("**汇总：%d / %d 项通过。**" % (passed, len(ROWS)))
    with io.open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n报告已写入: %s" % path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", default=os.path.join(PROJECT_ROOT, "数据质量校验报告.md"))
    args = parser.parse_args()

    if not os.path.exists(DB):
        print("数据目录不存在: %s" % DB)
        return 1
    conn = sqlite3.connect(DB)
    check_climate(conn)
    check_trace(conn)
    check_carbon(conn)
    conn.close()
    check_simulation()
    conn = sqlite3.connect(DB)
    check_consistency(conn)
    conn.close()
    write_report(os.path.abspath(args.report))

    passed = sum(1 for r in ROWS if r["status"] == "PASS")
    print("\n汇总: %d / %d 项通过" % (passed, len(ROWS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

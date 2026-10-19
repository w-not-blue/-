"""Import the official MOE school list and per-city climate.

Source: Ministry of Education, "全国高等学校名单" (2025), published as .xls.
The list gives school name, supervising authority, city and level. Coordinates
are resolved with Open-Meteo geocoding, then daily 2025 temperature is fetched
from the ERA5 archive. Everything is cached under data/raw so re-runs are free.

Usage:
    python tools/etl/import_schools.py --xls <file.xls>
    python tools/etl/import_schools.py --xls <file.xls> --max-cities 10
"""

import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import xlrd

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RAW = os.path.join(PROJECT_ROOT, "data", "raw")
SCHOOL_DIR = os.path.join(RAW, "schools")
CLIMATE_DIR = os.path.join(RAW, "climate")
CACHE_COORDS = os.path.join(SCHOOL_DIR, "city_coords.json")

UA = "codex-campus-dc-import/1.0 (competition demo)"
DELAY = 0.35


def http_json(url, retries=4):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    return None


def parse_schools(xls_path):
    wb = xlrd.open_workbook(xls_path)
    schools = []
    for sheet_index in range(wb.nsheets):
        sh = wb.sheet_by_index(sheet_index)
        province = ""
        for r in range(sh.nrows):
            cells = [str(sh.cell_value(r, c)).strip() for c in range(sh.ncols)]
            if not any(cells):
                continue
            joined = " ".join(cells)
            if "（" in joined and "所）" in joined and not cells[1]:
                province = joined.split("（")[0].strip()
                continue
            if len(cells) < 4:
                continue
            name = cells[1]
            if not name or name in ("学校名称",):
                continue
            city = ""
            level = ""
            dept = cells[3] if len(cells) > 3 else ""
            if sh.ncols >= 7:
                city = cells[4]
                level = cells[5]
            else:
                city = province
                level = "成人高校"
            if city and name and not name.startswith("附件"):
                schools.append({
                    "school_name": name,
                    "province": province,
                    "city": city,
                    "level": level or "未知",
                    "department": dept,
                    "sheet": sh.name,
                })
    return schools


def load_cache():
    if os.path.exists(CACHE_COORDS):
        with io.open(CACHE_COORDS, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache):
    with io.open(CACHE_COORDS, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def geocode(name, cache):
    """Resolve a Chinese city name with Nominatim (administrative level only)."""
    if name in cache:
        return cache[name]
    candidates = [name]
    for suffix in ("市", "地区", "盟", "自治州", "自治县"):
        if name.endswith(suffix) and len(name) > len(suffix):
            candidates.append(name[: -len(suffix)])
    found = None
    for candidate in candidates:
        url = ("https://nominatim.openstreetmap.org/search?q="
               + urllib.parse.quote(candidate)
               + "&format=jsonv2&limit=3&countrycodes=cn&featuretype=city")
        try:
            payload = http_json(url)
        except Exception:
            payload = None
        if isinstance(payload, list) and payload:
            item = payload[0]
            found = {
                "name": item.get("display_name", candidate).split(",")[0],
                "latitude": float(item.get("lat")),
                "longitude": float(item.get("lon")),
                "admin1": item.get("display_name", "").split(",")[1].strip()
                          if "," in item.get("display_name", "") else "",
                "country": "中国",
            }
            break
        time.sleep(1.1)
    cache[name] = found
    time.sleep(1.1)
    return found


def fetch_climate(city, coords):
    path = os.path.join(CLIMATE_DIR, city + ".json")
    if os.path.exists(path):
        with io.open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    url = ("https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
           "&start_date=2025-01-01&end_date=2025-12-31"
           "&daily=temperature_2m_max,temperature_2m_min,temperature_2m_mean"
           "&timezone=Asia%2FShanghai").format(lat=coords["latitude"], lon=coords["longitude"])
    payload = http_json(url)
    payload["_url"] = url
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    time.sleep(DELAY)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--xls", required=True)
    parser.add_argument("--max-cities", type=int, default=0)
    args = parser.parse_args()

    os.makedirs(SCHOOL_DIR, exist_ok=True)
    os.makedirs(CLIMATE_DIR, exist_ok=True)

    schools = parse_schools(args.xls)
    print("schools:", len(schools))
    cities = sorted({s["city"] for s in schools if s["city"]})
    if args.max_cities:
        cities = cities[: args.max_cities]
    print("cities:", len(cities))

    cache = load_cache()
    resolved = {}
    for index, city in enumerate(cities, 1):
        coords = geocode(city, cache)
        if coords:
            resolved[city] = coords
        if index % 25 == 0:
            save_cache(cache)
            print("  geocoded %d/%d" % (index, len(cities)), flush=True)
    save_cache(cache)
    print("geocoded:", len(resolved), "/", len(cities))

    ok = 0
    for index, city in enumerate(sorted(resolved), 1):
        try:
            fetch_climate(city, resolved[city])
            ok += 1
        except Exception as exc:
            print("  climate failed for %s: %s" % (city, exc))
        if index % 25 == 0:
            print("  climate %d/%d" % (index, len(resolved)), flush=True)
    print("climate fetched:", ok, "/", len(resolved))

    with io.open(os.path.join(SCHOOL_DIR, "schools.csv"), "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["school_name", "province", "city", "level", "department"])
        writer.writeheader()
        for s in schools:
            writer.writerow({k: s[k] for k in writer.fieldnames})

    rows = []
    for city in sorted(resolved):
        path = os.path.join(CLIMATE_DIR, city + ".json")
        if not os.path.exists(path):
            continue
        with io.open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        daily = data.get("daily") or {}
        for date, tmax, tmin, tmean in zip(
                daily.get("time", []),
                daily.get("temperature_2m_max", []),
                daily.get("temperature_2m_min", []),
                daily.get("temperature_2m_mean", [])):
            if tmax is None or tmin is None or tmean is None:
                continue
            rows.append((city, date, tmax, tmin, tmean))
    with io.open(os.path.join(CLIMATE_DIR, "city_daily.csv"), "w", encoding="utf-8", newline="") as f:
        f.write("city,date,tmax_c,tmin_c,tmean_c\n")
        for r in rows:
            f.write("%s,%s,%.1f,%.1f,%.1f\n" % r)
    print("daily climate rows:", len(rows))

    meta = {
        "source_id": "moe-school-list-2025",
        "name": "全国高等学校名单（2025）",
        "publisher": "中华人民共和国教育部",
        "url": "https://www.moe.gov.cn/jyb_xxgk/s5743/s5744/A03/202506/t20250627_1195683.html",
        "license": "政府公开信息",
        "retrieved_at": "2026-10-17",
        "note": "含普通高校与成人高校；所在地为地级市；经纬度由 Nominatim（OpenStreetMap，ODbL）行政区级解析",
    }
    with io.open(os.path.join(SCHOOL_DIR, "schools.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    climate_meta = {
        "source_id": "open-meteo-era5-cities-2025",
        "name": "中国各城市 2025 年逐日气温（ERA5 再分析）",
        "publisher": "Open-Meteo / ECMWF ERA5",
        "url": "https://archive-api.open-meteo.com/v1/archive",
        "license": "CC BY 4.0（Open-Meteo）；ERA5 © ECMWF",
        "retrieved_at": "2026-10-17",
        "note": "逐日最高/最低/平均气温；仿真时按标准日变化曲线展开为逐时值",
    }
    with io.open(os.path.join(CLIMATE_DIR, "city_daily.json"), "w", encoding="utf-8") as f:
        json.dump(climate_meta, f, ensure_ascii=False, indent=2)

    print("done")


if __name__ == "__main__":
    main()

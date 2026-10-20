import json
import os
from datetime import datetime

from flask import Blueprint, jsonify

from src.data.catalog import Catalog, region_for_province
from src.services import run_store
from src.simulation.metrics import rank_algorithms
from src.utils.logger import setup_logger

map_bp = Blueprint('map', __name__)
logger = setup_logger('map_api')

_API_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_API_DIR)
_PROJECT_ROOT = os.path.dirname(_SRC_DIR)
_FRONTEND_DIR = os.path.join(_PROJECT_ROOT, 'frontend')
_COORDS_PATH = os.path.join(_FRONTEND_DIR, 'static', 'maps', 'city_coords.json')


def _load_coords():
    with open(_COORDS_PATH, 'r', encoding='utf-8') as handle:
        return json.load(handle)


@map_bp.route('/api/v1/map/data', methods=['GET'])
def map_data():
    try:
        catalog = Catalog()
        if not catalog.available():
            return jsonify({'success': False, 'message': '数据目录不可用'}), 503

        coords = _load_coords()
        cities = catalog.cities()
        climate = {row['city']: row.get('avg_temp') for row in catalog.city_climate_summary()}
        emission_cache = {}

        city_payload = []
        province_acc = {}
        total_schools = 0

        for row in cities:
            city = row.get('city') or ''
            province = row.get('province') or ''
            school_count = int(row.get('school_count') or 0)
            total_schools += school_count
            region = region_for_province(province)
            if region not in emission_cache:
                emission_cache[region] = catalog.emission_factor(region)
            carbon = emission_cache.get(region)
            avg_temp = climate.get(city)

            acc = province_acc.setdefault(province, {
                'name': province,
                'region': region,
                'school_count': 0,
                'temp_sum': 0.0,
                'temp_weight': 0,
                'carbon_factor': carbon,
            })
            acc['school_count'] += school_count
            if avg_temp is not None:
                acc['temp_sum'] += float(avg_temp) * school_count
                acc['temp_weight'] += school_count

            coord = coords.get(city)
            if not coord:
                continue
            city_payload.append({
                'city': city,
                'province': province,
                'region': region,
                'latitude': coord.get('latitude'),
                'longitude': coord.get('longitude'),
                'school_count': school_count,
                'avg_temp': round(float(avg_temp), 2) if avg_temp is not None else None,
                'carbon_factor': carbon,
            })

        provinces = []
        for acc in province_acc.values():
            avg_temp = None
            if acc['temp_weight']:
                avg_temp = round(acc['temp_sum'] / acc['temp_weight'], 2)
            provinces.append({
                'name': acc['name'],
                'region': acc['region'],
                'school_count': acc['school_count'],
                'avg_temp': avg_temp,
                'carbon_factor': acc['carbon_factor'],
            })

        city_payload.sort(key=lambda item: (-item['school_count'], item['city']))
        provinces.sort(key=lambda item: (-item['school_count'], item['name']))
        return jsonify({
            'success': True,
            'year': 2025,
            'totals': {
                'schools': total_schools,
                'cities': len(city_payload),
                'provinces': len(provinces),
            },
            'provinces': provinces,
            'cities': city_payload,
            'sources': catalog.provenance(),
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        })
    except Exception as exc:
        logger.error('Map data error: %s', exc)
        return jsonify({'success': False, 'message': '地图数据加载失败'}), 500

def _monthly_climate(catalog, city):
    daily = catalog.city_daily(city) or []
    buckets = {}
    for row in daily:
        date_text = str(row.get('date') or '')
        try:
            month = int(date_text[5:7])
        except (TypeError, ValueError, IndexError):
            continue
        item = buckets.setdefault(month, {'tmean': [], 'tmax': [], 'tmin': []})
        for key in ('tmean', 'tmax', 'tmin'):
            value = row.get(key + '_c')
            if value is not None:
                item[key].append(float(value))

    months = []
    for month in range(1, 13):
        item = buckets.get(month)
        if not item or not item['tmean']:
            months.append({'month': month, 'tmean': None, 'tmax': None, 'tmin': None})
            continue
        months.append({
            'month': month,
            'tmean': round(sum(item['tmean']) / len(item['tmean']), 2),
            'tmax': round(max(item['tmax']), 2) if item['tmax'] else None,
            'tmin': round(min(item['tmin']), 2) if item['tmin'] else None,
        })
    return months


def _latest_city_run(city):
    for run in run_store.list_runs(100):
        params = run.get('params') or {}
        if run.get('status') != 'success' or params.get('site_id') != city:
            continue
        summary = run.get('summary') or {}
        if not summary:
            continue
        ranking = rank_algorithms(summary)
        best = ranking.get('best')
        if not best:
            continue
        metrics = summary.get(best) or {}
        return {
            'run_id': run.get('run_id'),
            'created_at': run.get('created_at'),
            'num_servers': params.get('num_servers'),
            'simulation_hours': params.get('simulation_hours'),
            'algorithm': best,
            'metrics': {
                'avg_pue': metrics.get('avg_pue'),
                'max_temp': metrics.get('max_temp'),
                'service_rate': metrics.get('service_rate'),
                'energy_per_served_load': metrics.get('energy_per_served_load'),
            },
        }
    return None


@map_bp.route('/api/v1/map/city/<path:city>', methods=['GET'])
def map_city(city):
    try:
        catalog = Catalog()
        if not catalog.available():
            return jsonify({'success': False, 'message': '数据目录不可用'}), 503

        city_name = str(city or '').strip()[:64]
        rows = [row for row in catalog.cities() if row.get('city') == city_name]
        if not rows:
            return jsonify({'success': False, 'message': '城市不存在'}), 404

        row = rows[0]
        province = row.get('province') or catalog.city_province(city_name) or ''
        region = region_for_province(province)
        school_count = int(row.get('school_count') or 0)
        climate = {item['city']: item.get('avg_temp') for item in catalog.city_climate_summary()}
        avg_temp = climate.get(city_name)
        carbon = catalog.emission_factor(region)
        latest = _latest_city_run(city_name)

        if latest:
            metrics = latest.get('metrics') or {}
            recommendation = {
                'servers': latest.get('num_servers') or 48,
                'algorithm': latest.get('algorithm'),
                'hours': latest.get('simulation_hours') or 24,
                'pue': metrics.get('avg_pue'),
                'max_temp': metrics.get('max_temp'),
                'service_rate': metrics.get('service_rate'),
                'source': 'latest_run',
                'run_id': latest.get('run_id'),
            }
        else:
            recommendation = {
                'servers': 48,
                'algorithm': 'damo_ets',
                'hours': 24,
                'pue': None,
                'max_temp': None,
                'service_rate': None,
                'source': 'baseline',
                'run_id': None,
            }

        return jsonify({
            'success': True,
            'city': city_name,
            'province': province,
            'region': region,
            'school_count': school_count,
            'avg_temp': round(float(avg_temp), 2) if avg_temp is not None else None,
            'carbon_factor': carbon,
            'months': _monthly_climate(catalog, city_name),
            'latest_run': latest,
            'recommendation': recommendation,
        })
    except Exception as exc:
        logger.error('Map city error: %s', exc)
        return jsonify({'success': False, 'message': '城市详情加载失败'}), 500

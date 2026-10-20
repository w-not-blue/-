from flask import Flask, request, jsonify, send_from_directory, redirect, session
from flask_cors import CORS
from src.plugin import PluginManager
from src.plugin.simulation_plugin import SimulationPlugin
from src.utils import load_simulation_data
from src.utils.logger import setup_logger
from src.utils.validation import (
    validate_int, validate_str, validate_algorithms,
    validate_simulation_params, security_headers
)
from src.api.download_api import download_bp
from src.api.speech_api import speech_bp
from src.api.ai_api import ai_bp
from src.api.run_api import run_bp
from src.api.auth_api import auth_bp
from src.api.map_api import map_bp
from src.services.run_manager import get_run_manager
from src.algorithms import PLUGIN_ALGORITHMS
from src.simulation.metrics import RANKING_RULE, SERVICE_RATE_FLOOR, rank_algorithms, summarize
from src.utils import load_run_metadata
import os
import mimetypes
import secrets

# ============================================================================
# Windows MIME 修正
# Windows 注册表 HKLM\SOFTWARE\Classes\.js 常把 .js 注册为 text/plain,
# 而本项目设置了 X-Content-Type-Options: nosniff, 浏览器会拒绝执行
# Content-Type 为 text/plain 的脚本 -> 前端 JS 全部不执行 (指标卡片停在 --,
# 数据分析图表空白, 插件列表卡在"正在加载")。
# 这里显式注册 Web 资源 MIME, 不依赖操作系统注册表。
# ============================================================================
mimetypes.add_type('text/javascript', '.js')
mimetypes.add_type('text/css', '.css')
mimetypes.add_type('text/html', '.html')
mimetypes.add_type('application/json', '.json')
mimetypes.add_type('application/manifest+json', '.webmanifest')
mimetypes.add_type('image/svg+xml', '.svg')
mimetypes.add_type('font/woff2', '.woff2')
mimetypes.add_type('font/woff', '.woff')
mimetypes.add_type('font/ttf', '.ttf')
# ============================================================================
# 路径解析 (#3: 基于 __file__ 的绝对路径, 不依赖 CWD)
# ============================================================================
_API_DIR = os.path.dirname(os.path.abspath(__file__))           # .../src/api/
_SRC_DIR = os.path.dirname(_API_DIR)                            # .../src/
PROJECT_ROOT = os.path.dirname(_SRC_DIR)                        # .../CampusDataCenterScheduler/
FRONTEND_DIR = os.path.join(PROJECT_ROOT, 'frontend')           # .../frontend


def _load_session_secret():
    env_secret = os.environ.get('DCS_SECRET_KEY')
    if env_secret:
        return env_secret
    secret_path = os.path.join(PROJECT_ROOT, 'data', '.session_secret')
    try:
        if os.path.isfile(secret_path):
            with open(secret_path, 'r', encoding='utf-8') as handle:
                value = handle.read().strip()
                if value:
                    return value
        os.makedirs(os.path.dirname(secret_path), exist_ok=True)
        value = secrets.token_hex(32)
        with open(secret_path, 'w', encoding='utf-8') as handle:
            handle.write(value)
        return value
    except OSError:
        return 'campus-datacenter-local-session-secret'

app = Flask(__name__, static_folder=FRONTEND_DIR, template_folder=FRONTEND_DIR)
app.secret_key = _load_session_secret()
app.config.update(
    SESSION_COOKIE_NAME='dcs_session',
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=False,
)
CORS(app)

app.register_blueprint(download_bp)
app.register_blueprint(speech_bp)
app.register_blueprint(ai_bp)
app.register_blueprint(run_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(map_bp)

def algorithm_catalog():
    """One source of truth for the algorithm list exposed to clients."""
    catalog = [
        {'name': 'greedy', 'description': '贪心调度算法', 'type': '实时调度', 'core': True},
        {'name': 'genetic', 'description': '遗传优化算法', 'type': '全局优化', 'core': True},
        {'name': 'round_robin', 'description': '轮询调度算法', 'type': '基准算法', 'core': True},
        {'name': 'damo_ets', 'description': 'DAMO-ETS 多目标能效调度（能耗+温度+时延+异构匹配）',
         'type': '多目标优化', 'core': True},
    ]
    for name, spec in PLUGIN_ALGORITHMS.items():
        catalog.append({
            'name': name,
            'description': '{0} 插件算法'.format(spec.get('plugin')),
            'type': '插件算法',
            'plugin': spec.get('plugin'),
            'core': False,
        })
    return catalog


plugin_manager = PluginManager()
plugin_manager.register_plugin(SimulationPlugin)
plugin_manager.discover_plugins(plugin_dir='src/plugins')

logger = setup_logger("api")

@app.after_request
def add_security_headers(response):
    for key, value in security_headers().items():
        response.headers.setdefault(key, value)
    return response

@app.route('/')
def index():
    if not session.get('user_id'):
        return redirect('/login.html')
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/index.html')
def index_html():
    if not session.get('user_id'):
        return redirect('/login.html')
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/login.html')
def login_page():
    return send_from_directory(app.static_folder, 'login.html')


@app.route('/register.html')
def register_page():
    return send_from_directory(app.static_folder, 'register.html')


@app.route('/auth.css')
def auth_css():
    return send_from_directory(app.static_folder, 'auth.css')


@app.route('/auth.js')
def auth_js():
    return send_from_directory(app.static_folder, 'auth.js')


@app.route('/map.html')
def map_page():
    if not session.get('user_id'):
        return redirect('/login.html')
    return send_from_directory(app.static_folder, 'map.html')


@app.route('/map.css')
def map_css():
    return send_from_directory(app.static_folder, 'map.css')


@app.route('/map.js')
def map_js():
    return send_from_directory(app.static_folder, 'map.js')


@app.route('/echarts.min.js')
def echarts_js():
    return send_from_directory(app.static_folder, 'echarts.min.js')


@app.route('/static/maps/<path:filename>')
def static_maps(filename):
    return send_from_directory(os.path.join(app.static_folder, 'static', 'maps'), filename)


@app.route('/profile.html')
def profile_page():
    if not session.get('user_id'):
        return redirect('/login.html')
    return send_from_directory(app.static_folder, 'profile.html')


@app.route('/profile.css')
def profile_css():
    return send_from_directory(app.static_folder, 'profile.css')


@app.route('/profile.js')
def profile_js():
    return send_from_directory(app.static_folder, 'profile.js')

@app.route('/download.html')
def download_page():
    return send_from_directory(app.static_folder, 'download.html')

@app.route('/speech_to_text.html')
def speech_page():
    return send_from_directory(app.static_folder, 'speech_to_text.html')

@app.route('/download.js')
def download_js():
    return send_from_directory(app.static_folder, 'download.js')

@app.route('/speech_to_text.js')
def speech_js():
    return send_from_directory(app.static_folder, 'speech_to_text.js')

@app.route('/app.js')
def app_js():
    return send_from_directory(app.static_folder, 'app.js')


@app.route('/assistant.js')
def assistant_js():
    return send_from_directory(app.static_folder, 'assistant.js')

@app.route('/prediction.js')
def prediction_js():
    return send_from_directory(app.static_folder, 'prediction.js')

@app.route('/cross_platform.css')
def cross_platform_css():
    return send_from_directory(app.static_folder, 'cross_platform.css')


@app.route('/theme.css')
def theme_css():
    return send_from_directory(app.static_folder, 'theme.css')


@app.route('/logo.png')
def logo_png():
    return send_from_directory(app.static_folder, 'logo.png')

@app.route('/manifest.json')
def manifest():
    return send_from_directory(app.static_folder, 'manifest.json')

@app.route('/service-worker.js')
def service_worker():
    return send_from_directory(app.static_folder, 'service-worker.js')

@app.route('/sw-register.js')
def sw_register():
    return send_from_directory(app.static_folder, 'sw-register.js')

@app.route('/chart.js')
def chart_js():
    return send_from_directory(app.static_folder, 'chart.js')

@app.route('/perf-benchmark.js')
def perf_benchmark_js():
    return send_from_directory(app.static_folder, 'perf-benchmark.js')

@app.route('/platform-utils.js')
def platform_utils_js():
    return send_from_directory(app.static_folder, 'platform-utils.js')

@app.route('/compat-tester.js')
def compat_tester_js():
    return send_from_directory(app.static_folder, 'compat-tester.js')

@app.route('/bootstrap.min.css')
def bootstrap_css():
    return send_from_directory(app.static_folder, 'bootstrap.min.css')

@app.route('/font-awesome.min.css')
def font_awesome_css():
    return send_from_directory(app.static_folder, 'font-awesome.min.css')

@app.route('/bootstrap.bundle.min.js')
def bootstrap_js():
    return send_from_directory(app.static_folder, 'bootstrap.bundle.min.js')

@app.route('/webfonts/<path:filename>')
def webfonts(filename):
    return send_from_directory(os.path.join(app.static_folder, 'webfonts'), filename)

# PWA 图标: 前端与 manifest.json 使用 /static/icons/... , 但 Flask 静态根是
# frontend/ , 真实文件在 frontend/static/icons/ , 需显式映射 (否则 404)
@app.route('/static/assistant/<path:filename>')
def static_assistant(filename):
    return send_from_directory(os.path.join(app.static_folder, 'static', 'assistant'), filename)


@app.route('/static/icons/<path:filename>')
def static_icons(filename):
    return send_from_directory(os.path.join(app.static_folder, 'static', 'icons'), filename)
@app.route('/install.html')
def install_page():
    """Legacy mobile-install link: that page was never shipped, so send the
    visitor to the download page instead of a bare 404."""
    return redirect('/download.html', code=302)


@app.route('/api/v1/plugins', methods=['GET'])
def list_plugins():
    try:
        plugins = []
        for plugin_name, plugin_class in plugin_manager._plugin_classes.items():
            plugin_instance = plugin_manager._plugin_instances.get(plugin_name)
            try:
                manifest = plugin_instance.get_manifest() if plugin_instance else plugin_class().get_manifest()
                plugins.append({
                    'name': manifest.name,
                    'version': manifest.version,
                    'description': ",".join(manifest.supported_operations),
                    'category': manifest.category,
                    'loaded': plugin_name in plugin_manager._plugin_instances
                })
            except Exception as e:
                logger.warning(f"Failed to get metadata for plugin {plugin_name}: {e}")
                plugins.append({
                    'name': plugin_name,
                    'version': 'unknown',
                    'description': '',
                    'category': 'algorithm',
                    'loaded': plugin_name in plugin_manager._plugin_instances
                })
        return jsonify({'success': True, 'plugins': plugins})
    except Exception as e:
        logger.error(f"Error listing plugins: {e}")
        return jsonify({'success': False, 'message': 'Failed to list plugins'}), 500

@app.route('/api/v1/plugins/<plugin_name>/load', methods=['POST'])
def load_plugin(plugin_name):
    try:
        plugin_name = validate_str(plugin_name, max_len=64, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$',
                                   name='plugin_name')
        config = request.json or {}
        success = plugin_manager.load_plugin(plugin_name, config)
        if success:
            info = plugin_manager.get_plugin_info(plugin_name)
            metadata = info.to_dict() if info else {}
            return jsonify({'success': True, 'message': 'Plugin loaded', 'metadata': metadata})
        return jsonify({'success': False, 'message': 'Failed to load plugin'}), 500
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error loading plugin {plugin_name}: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/v1/plugins/<plugin_name>/unload', methods=['POST'])
def unload_plugin(plugin_name):
    try:
        plugin_name = validate_str(plugin_name, max_len=64, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$',
                                   name='plugin_name')
        plugin_manager.unload_plugin(plugin_name)
        return jsonify({'success': True, 'message': 'Plugin unloaded'})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error unloading plugin {plugin_name}: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/v1/plugins/<plugin_name>/execute', methods=['POST'])
def execute_plugin(plugin_name):
    try:
        plugin_name = validate_str(plugin_name, max_len=64, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$',
                                   name='plugin_name')
        params = request.json or {}
        result = plugin_manager.execute_plugin(plugin_name, **params)
        if result is None:
            return jsonify({'success': False, 'message': 'Failed to execute plugin'}), 500
        return jsonify(result)
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error executing plugin {plugin_name}: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/v1/plugins/<plugin_name>/config', methods=['GET'])
def get_plugin_config(plugin_name):
    try:
        plugin_name = validate_str(plugin_name, max_len=64, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$',
                                   name='plugin_name')
        plugin = plugin_manager._plugin_instances.get(plugin_name)
        if plugin:
            manifest = plugin.get_manifest()
            config = {
                'name': manifest.name,
                'version': manifest.version,
                'description': ",".join(manifest.supported_operations)
            }
            if hasattr(plugin, 'num_servers'):
                config['num_servers'] = plugin.num_servers
            if hasattr(plugin, 'simulation_hours'):
                config['simulation_hours'] = plugin.simulation_hours
            return jsonify({'success': True, 'config': config})
        return jsonify({'success': False, 'message': 'Plugin not loaded'}), 404
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error getting plugin config {plugin_name}: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/v1/plugins/<plugin_name>/config', methods=['PUT'])
def update_plugin_config(plugin_name):
    try:
        plugin_name = validate_str(plugin_name, max_len=64, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$',
                                   name='plugin_name')
        plugin = plugin_manager._plugin_instances.get(plugin_name)
        if not plugin:
            return jsonify({'success': False, 'message': 'Plugin not loaded'}), 404

        config = request.json or {}
        if 'num_servers' in config:
            plugin.num_servers = validate_int(config['num_servers'], min_val=1, max_val=1000,
                                              default=plugin.num_servers, name='num_servers')
        if 'simulation_hours' in config:
            plugin.simulation_hours = validate_int(config['simulation_hours'], min_val=1, max_val=720,
                                                   default=plugin.simulation_hours, name='simulation_hours')

        return jsonify({'success': True, 'message': 'Config updated'})
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error updating plugin config {plugin_name}: {e}")
        return jsonify({'success': False, 'message': 'Internal server error'}), 500

@app.route('/api/v1/simulation', methods=['POST'])
def run_simulation():
    try:
        params = request.json or {}
        run_id, result = get_run_manager().run_sync(params, origin="web")
        payload = result.to_dict()
        payload["run_id"] = run_id
        return jsonify(payload)
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Error running simulation: {str(e)}")
        return jsonify({'success': False, 'message': 'Simulation failed'}), 500

def _current_data_source():
    meta = load_run_metadata() or {}
    return {
        'data_mode': meta.get('data_mode', 'synthetic'),
        'ambient_source': meta.get('ambient_source', 'synthetic-sine'),
        'scenario_source': meta.get('scenario_source'),
        'scenario_digest': meta.get('scenario_digest'),
        'sla_slack': meta.get('sla_slack'),
        'completion_mode': meta.get('completion_mode'),
        'emission_source': meta.get('emission_source'),
        'site_id': meta.get('site_id'),
        'school_name': meta.get('school_name'),
        'site_province': meta.get('site_province'),
        'emission_region': meta.get('emission_region'),
        'service_rate_floor': meta.get('service_rate_floor', SERVICE_RATE_FLOOR),
        'ranking_rule': RANKING_RULE,
    }


def _current_controlled():
    meta = load_run_metadata() or {}
    return meta.get('controlled')


@app.route('/api/v1/metrics', methods=['GET'])
def get_metrics():
    try:
        df = load_simulation_data()
        if df is None:
            return jsonify({'success': False, 'message': 'No simulation data found'}), 404

        result = summarize(df)
        ranking = rank_algorithms(result)
        ranks = {item['algorithm']: item['rank'] for item in ranking['eligible']}
        eligible = set(ranks)

        for algo, metrics in result.items():
            metrics['eligible'] = algo in eligible
            metrics['rank'] = ranks.get(algo)

        return jsonify({
            'success': True,
            'data': result,
            'ranking': ranking,
            'data_source': _current_data_source(),
            'controlled': _current_controlled(),
        })
    except Exception as e:
        logger.error(f"Error getting metrics: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/cities', methods=['GET'])
def list_cities():
    try:
        from src.data.catalog import Catalog
        catalog = Catalog()
        if not catalog.available():
            return jsonify({'success': False, 'message': '数据目录不可用'}), 503
        data = catalog.cities()
        return jsonify({'success': True, 'data': data, 'total': len(data)})
    except Exception as e:
        logger.error(f"Error listing cities: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/schools', methods=['GET'])
def list_schools():
    try:
        query = (request.args.get('q') or '').strip()
        city = (request.args.get('city') or '').strip() or None
        try:
            limit = int(request.args.get('limit', 50))
        except ValueError:
            limit = 50
        limit = max(1, min(limit, 200))

        from src.data.catalog import Catalog
        catalog = Catalog()
        if not catalog.available():
            return jsonify({'success': False, 'message': '数据目录不可用'}), 503
        return jsonify({
            'success': True,
            'data': catalog.schools(query or None, city=city, limit=limit),
            'total': catalog.count_schools(query or None, city=city),
        })
    except Exception as e:
        logger.error(f"Error listing schools: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/data-source', methods=['GET'])
def get_data_source():
    try:
        payload = dict(_current_data_source())
        meta = load_run_metadata() or {}
        payload['first_hour_offered'] = meta.get('first_hour_offered')
        payload['first_hour_served'] = meta.get('first_hour_served')
        payload['controlled'] = meta.get('controlled')
        payload['generated_at'] = meta.get('generated_at')
        payload['catalog'] = None

        try:
            from src.data.catalog import Catalog
            catalog = Catalog()
            if catalog.available():
                payload['catalog'] = {
                    'db_path': catalog.db_path,
                    'sources': catalog.provenance(),
                    'ambient_hours': len(catalog.ambient_series() or []),
                }
        except Exception as exc:
            logger.warning(f"Catalog unavailable: {exc}")

        return jsonify({'success': True, 'data': payload})
    except Exception as e:
        logger.error(f"Error getting data source: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/v1/data', methods=['GET'])
def get_data():
    try:
        df = load_simulation_data()
        if df is None:
            return jsonify({'success': False, 'message': 'No simulation data found'}), 404

        return jsonify({
            'success': True,
            'data': df.to_dict('records')
        })
    except Exception as e:
        logger.error(f"Error getting data: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/v1/algorithms', methods=['GET'])
def list_algorithms():
    return jsonify({'algorithms': algorithm_catalog()})

@app.route('/api/v1/system/status', methods=['GET'])
def get_system_status():
    loaded_plugins = len(plugin_manager._plugin_instances)
    registered_plugins = len(plugin_manager._plugin_classes)
    
    status = {
        'loaded_plugins': loaded_plugins,
        'registered_plugins': registered_plugins,
        'status': 'healthy'
    }
    return jsonify({'success': True, 'status': status})

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy', 'service': 'datacenter-simulation-api'})

def run(host: str = '0.0.0.0', port: int = 5000, debug: bool = False):
    logger.info(f"Starting API server on {host}:{port}")
    app.run(host=host, port=port, debug=debug)

if __name__ == '__main__':
    run()
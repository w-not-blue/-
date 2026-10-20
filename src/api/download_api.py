"""
应用下载API模块

提供安全下载服务:
- 下载令牌验证 (防刷/防盗链)
- 文件完整性校验 (SHA256)
- 下载统计上报
- 版本信息查询
- 自动更新检测与通知
- 全平台支持: Android / iOS / Windows / macOS
"""

import os
import hashlib
import secrets
import time
import re
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from flask import Blueprint, request, jsonify, send_file, Response, stream_with_context
from src.utils.logger import setup_logger
from src.utils.download_stats import get_download_stats
from src.utils.validation import (
    validate_str, sanitize_filename, security_headers, RateLimiter, hash_sha256
)

logger = setup_logger("download_api")

download_bp = Blueprint('download', __name__)

# ============================================================================
# 路径解析 (基于 __file__ 的绝对路径, 不依赖 CWD)
#
#   _API_DIR        = .../PC端安装包/output/CampusDataCenterScheduler/src/api/
#   _SRC_DIR        = .../PC端安装包/output/CampusDataCenterScheduler/src/
#   _PROJECT_ROOT   = .../PC端安装包/output/CampusDataCenterScheduler/
#   _OUTPUT_DIR     = .../PC端安装包/output/   (构建产物目录, 含 zip/exe)
#   _RELEASES_DIR   = .../PC端安装包/releases/ (将来正式发布目录)
# ============================================================================
_API_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_API_DIR)
_PROJECT_ROOT = os.path.dirname(_SRC_DIR)
_OUTPUT_DIR = os.path.dirname(_PROJECT_ROOT)         # .../PC端安装包/output/
_RELEASES_DIR = os.path.join(_OUTPUT_DIR, '..', 'releases')
_RELEASES_DIR = os.path.abspath(_RELEASES_DIR)
_DOWNLOAD_DIR = os.path.join(_PROJECT_ROOT, 'downloads')
_PORTABLE_BASENAME = 'CampusDataCenterScheduler_v1.0.0_portable.zip'
_PORTABLE_ZIP = os.path.join(_OUTPUT_DIR, _PORTABLE_BASENAME)


def _portable_zip_path() -> Optional[str]:
    for folder in (_RELEASES_DIR, _OUTPUT_DIR, _DOWNLOAD_DIR):
        candidate = os.path.join(folder, _PORTABLE_BASENAME)
        if os.path.isfile(candidate):
            return candidate
    return None

# ============================================================================
# 配置
# ============================================================================
TOKEN_EXPIRY_SEC = 600
TOKEN_RE = re.compile(r'^[a-f0-9]{64}$')
_active_tokens: Dict[str, Dict[str, Any]] = {}
_rate_limiter = RateLimiter(max_requests=30, window_sec=60.0)

# ============================================================================
# 文件解析: platform/filename → 实际文件路径
# ============================================================================
FILE_BASENAMES = {
    'mobile_android':  'CampusDataCenterScheduler_v1.0.0_android.apk',
    'mobile_ios':      'CampusDataCenterScheduler_v1.0.0_ios.ipa',
    'windows_x64':     'CampusDataCenterScheduler_Setup_v1.0.0.exe',
    'windows_arm64':   'CampusDataCenterScheduler_v1.0.0_portable.zip',
    'macos':           'CampusDataCenterScheduler_v1.0.0_macos.dmg',
}

CONTENT_TYPES = {
    '.exe': 'application/x-msdownload',
    '.apk': 'application/vnd.android.package-archive',
    '.ipa': 'application/octet-stream',
    '.dmg': 'application/x-apple-diskimage',
    '.zip': 'application/zip',
}


def _app_key_for(platform: str, filename: str) -> Optional[str]:
    """根据 platform + filename 解析出 app_key (mobile_android / windows_x64 ...)"""
    fn = filename.lower()
    if platform == 'android' and (fn.endswith('.apk') or fn.endswith('.zip')):
        return 'mobile_android'
    if platform == 'ios' and (fn.endswith('.ipa') or fn.endswith('.app') or fn.endswith('.zip')):
        return 'mobile_ios'
    if platform == 'macos' and (fn.endswith('.dmg') or fn.endswith('.zip')):
        return 'macos'
    if platform == 'windows':
        if 'arm' in fn:
            return 'windows_arm64'
        return 'windows_x64'
    if platform == 'windows_arm':
        return 'windows_arm64'
    return None


def _resolve_file_path(platform: str, filename: str) -> Optional[str]:
    """
    将 platform/filename 解析为绝对文件路径.

    查找顺序:
      1. releases/<basename>      (生产构建产物)
      2. output/<basename>        (CI 直接产物)
      3. Windows x64 退化到 portable zip (开发期替代)
    """
    app_key = _app_key_for(platform, filename)
    if not app_key:
        return None
    basename = FILE_BASENAMES.get(app_key)
    if not basename:
        return None

    for d in (_RELEASES_DIR, _OUTPUT_DIR, _DOWNLOAD_DIR):
        candidate = os.path.join(d, basename)
        if os.path.isfile(candidate):
            return candidate

    # 开发期/安装版退化: Windows x64 没有真 .exe 时, 用 portable zip 替代
    if app_key == 'windows_x64':
        portable = _portable_zip_path()
        if portable:
            return portable

    return None


APP_KEY_PLATFORM = {
    'mobile_android': 'android',
    'mobile_ios': 'ios',
    'windows_x64': 'windows',
    'windows_arm64': 'windows_arm',
    'macos': 'macos',
}


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _file_size_mb(path: str) -> float:
    return round(os.path.getsize(path) / (1024 * 1024), 2)


def _resolved_app_file(app_key: str) -> Optional[str]:
    basename = FILE_BASENAMES.get(app_key)
    if not basename:
        return None
    platform = APP_KEY_PLATFORM.get(app_key, '')
    return _resolve_file_path(platform, basename)


def _app_metadata(app_key: str) -> Dict[str, Any]:
    item = dict(APP_VERSIONS.get(app_key, {}))
    path = _resolved_app_file(app_key)
    if path:
        item['sha256'] = _file_sha256(path)
        item['size_mb'] = _file_size_mb(path)
    return item


# ============================================================================
# Token 管理
# ============================================================================
def _generate_token(platform: str, app_type: str, filename: str,
                    file_path: Optional[str]) -> str:
    token = secrets.token_hex(32)
    _active_tokens[token] = {
        'platform': platform,
        'app_type': app_type,
        'filename': filename,
        'file_path': file_path,           # #4: 绑定具体文件路径, 防 token 滥用
        'app_key': _app_key_for(platform, filename),
        'created_at': time.time(),
        'expires_at': time.time() + TOKEN_EXPIRY_SEC,
        'used': False,
        'ip': None,
    }
    return token


def _cleanup_expired_tokens() -> None:
    now = time.time()
    expired = [t for t, d in _active_tokens.items() if d['expires_at'] < now]
    for t in expired:
        del _active_tokens[t]


# ============================================================================
# 路由: 下载令牌验证
# ============================================================================
@download_bp.route('/api/v1/download/verify', methods=['POST'])
def verify_download():
    """
    下载安全验证接口

    Request JSON:
      { platform, app_type, filename }

    Response:
      { success, token, expires_in, message, file_available, file_size }
    """
    try:
        data = request.json or {}
        client_ip = request.remote_addr or '0.0.0.0'

        if not _rate_limiter.is_allowed(client_ip):
            return jsonify({'success': False, 'message': '请求过于频繁'}), 429

        platform = validate_str(
            data.get('platform', ''), max_len=32,
            pattern=r'^(android|ios|windows|windows_arm|macos)$',
            default='', name='platform'
        )
        app_type = validate_str(
            data.get('app_type', ''), max_len=32,
            pattern=r'^(mobile|desktop|windows)$',
            default='', name='app_type'
        )
        filename = sanitize_filename(data.get('filename', ''), max_len=128)

        if not platform or not filename:
            return jsonify({'success': False, 'message': '参数缺失'}), 400

        referrer = request.headers.get('Referer', '')
        if referrer and 'datacenter' not in referrer.lower():
            logger.warning(f"可疑的Referrer: {referrer}")

        # #4: 在 verify 时解析出真实文件路径, 绑定到 token
        file_path = _resolve_file_path(platform, filename)
        file_available = file_path is not None
        file_size = os.path.getsize(file_path) if file_available else 0

        _cleanup_expired_tokens()
        token = _generate_token(platform, app_type, filename, file_path)
        _active_tokens[token]['ip'] = client_ip

        logger.info(
            f"Download token issued: {platform}/{filename} "
            f"file_available={file_available} size={file_size}"
        )

        return jsonify({
            'success': True,
            'token': token,
            'expires_in': TOKEN_EXPIRY_SEC,
            'file_available': file_available,
            'file_size': file_size,
            'message': '验证通过' if file_available else '验证通过, 但安装包暂未就绪'
        })
    except Exception as e:
        logger.error(f"Download verify error: {e}")
        return jsonify({'success': False, 'message': '服务器错误'}), 500


# ============================================================================
# 路由: 文件下载 (核心修复 #1)
# ============================================================================
@download_bp.route('/api/v1/download/file/<token>', methods=['GET'])
def download_file(token: str):
    """
    通过 token 下载文件 (前端 download.js 第 257 行调用)

    安全约束:
      - token 必须是 64 位 hex
      - token 必须存在且未过期
      - token 必须绑定到一个真实存在的文件 (#4)
      - token 单次使用, 用后失效 (防重放)
    """
    try:
        # 1. 格式校验
        if not TOKEN_RE.match(token or ''):
            logger.warning(f"非法 token 格式: {token[:16] if token else 'None'}...")
            return jsonify({'success': False, 'message': '非法 token'}), 400

        # 2. 存在性 + 过期
        _cleanup_expired_tokens()
        record = _active_tokens.get(token)
        if not record:
            return jsonify({'success': False, 'message': 'token 无效或已过期'}), 404

        # 3. 重放保护 (无论文件是否存在, 已用过的 token 都不能再用)
        if record.get('used'):
            logger.warning(f"token 重放: {token[:16]}... (已用过)")
            return jsonify({'success': False, 'message': 'token 已使用'}), 410

        # 4. 文件存在性 (#4: 路径由 verify 时绑定, 不接受客户端传入路径)
        file_path = record.get('file_path')
        if not file_path or not os.path.isfile(file_path):
            # 文件缺失时, 不消耗 token (允许重试到文件就绪)
            logger.warning(f"文件不存在: {file_path} (token={token[:16]}...)")
            return jsonify({
                'success': False,
                'message': '安装包暂未就绪, 请稍后再试',
                'platform': record.get('platform'),
                'filename': record.get('filename'),
            }), 404

        # 5. 标记已用 (单次有效, 防下载链接外泄后被滥用) - 只在文件存在时消耗
        record['used'] = True

        # 6. 推断 Content-Type
        ext = os.path.splitext(file_path)[1].lower()
        content_type = CONTENT_TYPES.get(ext, 'application/octet-stream')
        download_name = record['filename'] or os.path.basename(file_path)

        logger.info(
            f"文件下载: {download_name} ({os.path.getsize(file_path)} bytes) "
            f"token={token[:16]}..."
        )

        # 7. 流式发送 (大文件分块, 避免 OOM)
        return _send_file_stream(file_path, download_name, content_type)

    except Exception as e:
        logger.error(f"File download error: {e}")
        return jsonify({'success': False, 'message': '服务器错误'}), 500


def _send_file_stream(file_path: str, download_name: str, content_type: str):
    """分块流式发送文件, 支持 Range (大文件断点续传)"""
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get('Range', None)

    # 解析 Range
    start = 0
    end = file_size - 1
    status_code = 200
    headers = {
        'Content-Type': content_type,
        'Content-Disposition': f'attachment; filename="{download_name}"',
        'Accept-Ranges': 'bytes',
        'X-Content-Type-Options': 'nosniff',
    }

    if range_header:
        m = re.match(r'bytes=(\d+)-(\d*)', range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1
            status_code = 206
            headers['Content-Range'] = f'bytes {start}-{end}/{file_size}'
            headers['Content-Length'] = str(length)

    def generate():
        chunk_size = 64 * 1024  # 64KB
        with open(file_path, 'rb') as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers['Content-Length'] = headers.get('Content-Length', str(file_size))
    return Response(
        stream_with_context(generate()),
        status=status_code,
        headers=headers,
    )


# ============================================================================
# 路由: 静态路径下载 (兼容 APP_VERSIONS 里的 download_url)
# ============================================================================
@download_bp.route('/api/v1/download/file/<platform>/<arch>', methods=['GET'])
def download_file_legacy(platform: str, arch: str):
    """
    兼容 APP_VERSIONS 中的静态 download_url, 例如:
      /api/v1/download/file/windows/x64
      /api/v1/download/file/macos/dmg

    与 token 路由不同, 此类直接走 IP 限流 + 简单 referrer 校验。
    """
    try:
        client_ip = request.remote_addr or '0.0.0.0'
        if not _rate_limiter.is_allowed(client_ip):
            return jsonify({'success': False, 'message': '请求过于频繁'}), 429

        # 映射: (platform, arch) → 内部 platform 标识
        if platform == 'windows' and arch == 'x64':
            real_platform, real_arch = 'windows', 'x64'
        elif platform == 'windows' and arch == 'arm64':
            real_platform, real_arch = 'windows_arm', 'arm64'
        elif platform == 'macos' and arch == 'dmg':
            real_platform, real_arch = 'macos', 'dmg'
        elif platform == 'android' and arch == 'apk':
            real_platform, real_arch = 'android', 'apk'
        elif platform == 'ios' and arch in ('ipa', 'app'):
            real_platform, real_arch = 'ios', 'ipa'
        else:
            return jsonify({'success': False, 'message': '不支持的平台/架构'}), 400

        app_key = {
            'android': 'mobile_android',
            'ios': 'mobile_ios',
            'macos': 'macos',
            'windows': 'windows_x64',
            'windows_arm': 'windows_arm64',
        }.get(real_platform)
        basename = FILE_BASENAMES.get(app_key) if app_key else None
        if not basename:
            return jsonify({'success': False, 'message': '无此安装包'}), 404

        file_path = None
        for d in (_RELEASES_DIR, _OUTPUT_DIR, _DOWNLOAD_DIR):
            candidate = os.path.join(d, basename)
            if os.path.isfile(candidate):
                file_path = candidate
                break
        if not file_path and app_key == 'windows_x64':
            file_path = _portable_zip_path()

        if not file_path:
            return jsonify({'success': False, 'message': '安装包暂未就绪'}), 404

        ext = os.path.splitext(file_path)[1].lower()
        content_type = CONTENT_TYPES.get(ext, 'application/octet-stream')
        return _send_file_stream(file_path, basename, content_type)

    except Exception as e:
        logger.error(f"Legacy download error: {e}")
        return jsonify({'success': False, 'message': '服务器错误'}), 500


# ============================================================================
# 路由: SHA256 校验
# ============================================================================

APP_VERSIONS = {
    'mobile_android': {
        'version': 'v1.0.0',
        'build': '1000',
        'size_mb': 0,
        'min_os': 'Android 8.0+ / Chrome',
        'arch': 'ARM64 / x86_64',
        'release_date': '2026.10.19',
        'sha256': '',
        'release_notes': 'Android 客户端安装包',
        'download_url': '/api/v1/download/file/android/apk',
        'web_url': None,
        'app_store_url': None,
        'platform_family': 'mobile',
        'display_name': 'Android 安装包',
        'icon': 'fab fa-android'
    },
    'mobile_ios': {
        'version': 'v1.0.0',
        'build': '1000',
        'size_mb': 0,
        'min_os': 'iOS 15.0+ / Safari',
        'arch': 'ARM64',
        'release_date': '2026.10.19',
        'sha256': '',
        'release_notes': 'iOS 客户端安装包',
        'download_url': '/api/v1/download/file/ios/ipa',
        'web_url': None,
        'app_store_url': None,
        'platform_family': 'mobile',
        'display_name': 'iOS 安装包',
        'icon': 'fab fa-app-store-ios'
    },
    'windows_x64': {
        'version': 'v1.0.0',
        'build': '1000',
        'size_mb': 90.07,
        'min_os': 'Windows 10 / Windows 11',
        'arch': 'x64',
        'release_date': '2026.10.18',
        'sha256': '',
        'release_notes': '当前源码完整打包，包含真实数据、AI 助手、语音对话与运行历史',
        'download_url': '/api/v1/download/file/windows/x64',
        'web_url': None,
        'app_store_url': None,
        'platform_family': 'desktop',
        'display_name': 'Windows x64 安装包',
        'icon': 'fab fa-windows'
    },
    'windows_arm64': {
        'version': 'v1.0.0',
        'build': '1000',
        'size_mb': 90.06,
        'min_os': 'Windows 11 ARM64',
        'arch': 'ARM64 / Python 3.12',
        'release_date': '2026.10.18',
        'sha256': '',
        'release_notes': 'Windows ARM64 使用便携版，解压后运行 bin/start.bat',
        'download_url': '/api/v1/download/file/windows/arm64',
        'web_url': '/',
        'app_store_url': None,
        'platform_family': 'desktop',
        'display_name': 'Windows ARM64 便携版',
        'icon': 'fab fa-windows'
    },
    'macos': {
        'version': 'v1.0.0',
        'build': '1000',
        'size_mb': 0,
        'min_os': 'macOS 12+ / Safari 或 Chrome',
        'arch': 'Universal / Intel & Apple Silicon',
        'release_date': '2026.10.19',
        'sha256': '',
        'release_notes': 'macOS 桌面客户端安装包',
        'download_url': '/api/v1/download/file/macos/dmg',
        'web_url': None,
        'app_store_url': None,
        'platform_family': 'desktop',
        'display_name': 'macOS 安装包',
        'icon': 'fab fa-apple'
    }
}

# (旧版 _generate_token / _cleanup_expired_tokens / verify_download 已上移并增强,
#  此处不再重复定义)


@download_bp.route('/api/v1/download/checksum', methods=['POST'])
def verify_checksum():
    """
    SHA256完整性校验接口
    
    客户端下载完成后将本地SHA256发送至服务端比对。
    """
    try:
        data = request.json or {}
        filename = data.get('filename', '')
        client_sha256 = data.get('sha256', '')

        if not filename or not client_sha256:
            return jsonify({'valid': False, 'message': '校验参数缺失'}), 400

        key_map = {
            'CampusDataCenterScheduler_Setup_v1.0.0.exe': 'windows_x64',
            'CampusDataCenterScheduler_v1.0.0_portable.zip': 'windows_arm64',
            'CampusDataCenterScheduler_v1.0.0_android.apk': 'mobile_android',
            'CampusDataCenterScheduler_v1.0.0_ios.ipa': 'mobile_ios',
            'CampusDataCenterScheduler_v1.0.0_macos.dmg': 'macos',
        }

        app_key = key_map.get(filename, '')
        app_info = APP_VERSIONS.get(app_key, None) if app_key else None
        
        if app_info:
            expected = app_info.get('sha256') or ''
            if not expected:
                path = _resolved_app_file(app_key)
                if path:
                    expected = _file_sha256(path)
            valid = bool(expected) and client_sha256.lower() == expected.lower()
            logger.info(f"SHA256 check for {filename}: {'MATCH' if valid else 'MISMATCH'}")
            return jsonify({'valid': valid, 'message': '校验通过' if valid else '文件校验失败'})
        
        return jsonify({'valid': True, 'message': '无校验基准'})

    except Exception as e:
        logger.error(f"Checksum error: {e}")
        return jsonify({'valid': False, 'message': str(e)}), 500

@download_bp.route('/api/v1/download/versions', methods=['GET'])
def get_versions():
    """获取所有平台的最新版本信息"""
    return jsonify({
        'success': True,
        'versions': {
            'android': _app_metadata('mobile_android'),
            'ios': _app_metadata('mobile_ios'),
            'windows_x64': _app_metadata('windows_x64'),
            'windows_arm64': _app_metadata('windows_arm64'),
            'macos': _app_metadata('macos')
        },
        'latest_version': 'v1.0.0',
        'latest_build': '1000'
    })

@download_bp.route('/api/v1/download/check-update', methods=['POST'])
def check_update():
    """
    客户端版本更新检测接口
    
    客户端发送当前版本号和服务端比对，
    返回是否需要更新及最新版本信息。
    
    Request JSON:
    {
        "platform": "android" | "ios" | "windows" | "windows_arm" | "macos",
        "current_version": "v3.1.0",
        "current_build": "3100"
    }
    """
    try:
        data = request.json or {}
        platform = validate_str(data.get('platform', ''), max_len=32,
                                pattern=r'^(android|ios|windows|windows_arm|macos)$',
                                default='', name='platform')
        current_version = validate_str(data.get('current_version', ''), max_len=16,
                                       pattern=r'^v?\d+\.\d+\.\d+$',
                                       default='', name='current_version')
        current_build = validate_str(data.get('current_build', ''), max_len=10,
                                     pattern=r'^\d+$',
                                     default='0', name='current_build')

        platform_key_map = {
            'android': 'mobile_android',
            'ios': 'mobile_ios',
            'windows': 'windows_x64',
            'windows_arm': 'windows_arm64',
            'macos': 'macos'
        }

        app_key = platform_key_map.get(platform)
        if not app_key:
            return jsonify({'success': False, 'message': '不支持的平台'}), 400

        latest_info = APP_VERSIONS.get(app_key, {})
        if not latest_info:
            return jsonify({'success': False, 'message': '版本信息不可用'}), 404

        latest_build_num = int(latest_info.get('build', '0'))
        current_build_num = int(current_build) if current_build else 0
        update_available = latest_build_num > current_build_num

        return jsonify({
            'success': True,
            'update_available': update_available,
            'current_version': current_version or 'unknown',
            'latest_version': latest_info['version'],
            'latest_build': latest_info['build'],
            'size_mb': latest_info['size_mb'],
            'release_date': latest_info['release_date'],
            'release_notes': latest_info['release_notes'],
            'download_url': latest_info.get('download_url'),
            'app_store_url': latest_info.get('app_store_url'),
            'min_os': latest_info['min_os'],
            'message': '发现新版本！' if update_available else '当前已是最新版本'
        })
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)}), 400
    except Exception as e:
        logger.error(f"Update check error: {e}")
        return jsonify({'success': False, 'message': '服务器错误'}), 500

@download_bp.route('/api/v1/download/stats', methods=['GET'])
def get_stats():
    """获取下载统计数据"""
    try:
        stats = get_download_stats()
        return jsonify(stats.get_summary())
    except Exception as e:
        logger.error(f"Download stats error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@download_bp.route('/api/v1/download/stats', methods=['POST'])
def record_stats():
    """上报下载统计"""
    try:
        data = request.json or {}
        platform = data.get('platform', 'unknown')
        app_type = data.get('app_type', 'unknown')
        filename = data.get('filename', '')
        size_mb = float(data.get('size_mb', 0))
        user_agent = data.get('user_agent', '')[:200]
        referrer = data.get('referrer', '')[:200]
        client_ip = request.remote_addr or '0.0.0.0'

        stats = get_download_stats()
        record = stats.record_download(
            platform=platform, app_type=app_type,
            filename=filename, size_mb=size_mb,
            user_agent=user_agent, ip_address=client_ip
        )

        summary = stats.get_summary()
        return jsonify({
            'success': True,
            'record': record,
            'total': summary['total']
        })
    except Exception as e:
        logger.error(f"Download stats recording error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@download_bp.route('/api/v1/download/stats/reset', methods=['POST'])
def reset_stats():
    """重置下载统计（管理接口）"""
    try:
        valid_key = os.environ.get('DCS_ADMIN_KEY')
        if not valid_key:
            logger.error("DCS_ADMIN_KEY environment variable not set")
            return jsonify({'success': False, 'message': '服务配置错误'}), 500
        api_key = request.headers.get('X-API-Key', '')
        if not api_key or api_key != valid_key:
            logger.warning(f"Unauthorized stats reset attempt from {request.remote_addr}")
            return jsonify({'success': False, 'message': '未授权'}), 403

        stats = get_download_stats()
        stats.reset_stats()
        return jsonify({'success': True, 'message': '统计已重置'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

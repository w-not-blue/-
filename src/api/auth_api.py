import base64
import io
import os
import random
import re
import secrets
import time

from flask import Blueprint, jsonify, request, session
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from src.services.user_store import get_user_store
from src.utils.logger import setup_logger
from src.utils.validation import RateLimiter

auth_bp = Blueprint('auth', __name__)
logger = setup_logger('auth_api')

_CAPTCHA_TTL = 120
_CAPTCHA_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
_login_limiter = RateLimiter(max_requests=12, window_sec=60.0)
_USERNAME_RE = re.compile(r'^[A-Za-z0-9_\u4e00-\u9fa5]{3,32}$')


def _captcha_font(size=30):
    font_dir = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
    for name in ('arialbd.ttf', 'arial.ttf', 'segoeuib.ttf', 'segoeui.ttf', 'calibrib.ttf'):
        path = os.path.join(font_dir, name)
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    try:
        return ImageFont.truetype('DejaVuSans-Bold.ttf', size)
    except OSError:
        return ImageFont.load_default()


def _render_captcha(code):
    width, height = 132, 44
    image = Image.new('RGB', (width, height), (238, 242, 247))
    draw = ImageDraw.Draw(image)

    for _ in range(5):
        draw.line(
            [
                (random.randint(0, width), random.randint(0, height)),
                (random.randint(0, width), random.randint(0, height)),
            ],
            fill=(random.randint(155, 205), random.randint(165, 215), random.randint(175, 225)),
            width=1,
        )

    for _ in range(90):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        color = (random.randint(120, 190), random.randint(130, 195), random.randint(145, 210))
        draw.point((x, y), fill=color)

    font = _captcha_font(29)
    colors = [
        (30, 64, 175),
        (22, 104, 220),
        (15, 118, 110),
        (124, 45, 18),
        (91, 33, 182),
    ]
    for index, char in enumerate(code):
        x = 12 + index * 29 + random.randint(-2, 2)
        y = random.randint(1, 8)
        draw.text((x, y), char, font=font, fill=random.choice(colors))

    image = image.filter(ImageFilter.SMOOTH)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    return base64.b64encode(buffer.getvalue()).decode('ascii')


def _consume_captcha(value):
    expected = session.pop('captcha_code', None)
    expires = session.pop('captcha_expires', 0)
    if not expected or time.time() > float(expires or 0):
        return False
    return (value or '').strip().upper() == expected.upper()


def _public_user(row):
    data = {'id': row['id'], 'username': row['username']}
    for key in ('created_at', 'last_login'):
        try:
            data[key] = row[key]
        except (IndexError, KeyError):
            pass
    return data


@auth_bp.route('/api/v1/auth/captcha', methods=['GET'])
def get_captcha():
    code = ''.join(secrets.choice(_CAPTCHA_CHARS) for _ in range(4))
    session['captcha_code'] = code
    session['captcha_expires'] = time.time() + _CAPTCHA_TTL
    image = _render_captcha(code)
    return jsonify({
        'success': True,
        'image': 'data:image/png;base64,' + image,
        'expires_in': _CAPTCHA_TTL,
    })


@auth_bp.route('/api/v1/auth/login', methods=['POST'])
def login():
    client_ip = request.remote_addr or '0.0.0.0'
    if not _login_limiter.is_allowed(client_ip):
        return jsonify({'success': False, 'message': '尝试过于频繁，请稍后再试'}), 429

    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    captcha = (data.get('captcha') or '').strip()

    if not username or not password:
        return jsonify({'success': False, 'message': '请输入账号和密码'}), 400
    if not captcha:
        return jsonify({'success': False, 'message': '请输入图形验证码'}), 400
    if not _consume_captcha(captcha):
        return jsonify({'success': False, 'message': '验证码错误或已过期，请刷新后重试'}), 400

    user = get_user_store().verify_user(username, password)
    if user is None:
        return jsonify({'success': False, 'message': '账号或密码错误'}), 401

    session.clear()
    session['user_id'] = user['id']
    session['username'] = user['username']
    session.permanent = False
    logger.info('User login: %s', user['username'])
    return jsonify({'success': True, 'user': user})


@auth_bp.route('/api/v1/auth/register', methods=['POST'])
def register():
    data = request.get_json(silent=True) or {}
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    confirm_password = data.get('confirm_password') or ''

    if not _USERNAME_RE.match(username):
        return jsonify({'success': False, 'message': '用户名需为 3-32 位字母、数字、下划线或中文'}), 400
    if len(password) < 6 or len(password) > 64:
        return jsonify({'success': False, 'message': '密码长度需为 6-64 位'}), 400
    if password != confirm_password:
        return jsonify({'success': False, 'message': '两次输入的密码不一致'}), 400

    try:
        user = get_user_store().create_user(username, password)
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 409

    logger.info('User registered: %s', user['username'])
    return jsonify({'success': True, 'message': '注册成功，请登录', 'user': _public_user(user)}), 201


@auth_bp.route('/api/v1/auth/me', methods=['GET'])
def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': True, 'authenticated': False, 'user': None})

    user = get_user_store().get_by_id(user_id)
    if user is None:
        session.clear()
        return jsonify({'success': True, 'authenticated': False, 'user': None})

    return jsonify({'success': True, 'authenticated': True, 'user': _public_user(user)})


@auth_bp.route('/api/v1/auth/change-password', methods=['POST'])
def change_password():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    data = request.get_json(silent=True) or {}
    current_password = data.get('current_password') or ''
    new_password = data.get('new_password') or ''
    confirm_password = data.get('confirm_password') or ''

    if not current_password:
        return jsonify({'success': False, 'message': '请输入当前密码'}), 400
    if len(new_password) < 6 or len(new_password) > 64:
        return jsonify({'success': False, 'message': '新密码长度需为 6-64 位'}), 400
    if new_password != confirm_password:
        return jsonify({'success': False, 'message': '两次输入的新密码不一致'}), 400

    if not get_user_store().change_password(user_id, current_password, new_password):
        return jsonify({'success': False, 'message': '当前密码不正确'}), 400

    logger.info('Password changed: %s', session.get('username'))
    return jsonify({'success': True, 'message': '密码已修改'})


@auth_bp.route('/api/v1/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True, 'message': '已退出登录'})
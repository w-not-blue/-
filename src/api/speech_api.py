from flask import Blueprint, request, jsonify
from src.services.speech_service import (
    get_speech_service, SpeechService, RecognitionState,
    SUPPORTED_LANGUAGES
)
from src.utils.logger import setup_logger
from src.utils.validation import validate_str

logger = setup_logger("speech_api")
speech_bp = Blueprint('speech', __name__)

@speech_bp.route('/api/v1/speech/session', methods=['POST'])
def create_session():
    """创建语音识别会话"""
    try:
        data = request.json or {}
        language = validate_str(
            data.get('language', 'zh-CN'), max_len=10,
            pattern=r'^[a-z]{2,3}(-[A-Z]{2,3})?$',
            default='zh-CN', name='language'
        )
        svc = get_speech_service()
        session = svc.create_session(language)

        lang_info = SUPPORTED_LANGUAGES.get(language, SUPPORTED_LANGUAGES['zh-CN'])

        return jsonify({
            'success': True,
            'session_id': session.session_id,
            'language': language,
            'language_name': lang_info['name'],
            'offline_available': lang_info['offline_available'],
            'web_api_code': lang_info['web_api_code']
        })
    except Exception as e:
        logger.error(f"Speech session creation error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@speech_bp.route('/api/v1/speech/languages', methods=['GET'])
def list_languages():
    """获取支持的语言列表"""
    try:
        svc = get_speech_service()
        langs = svc.get_supported_languages()
        return jsonify({'success': True, 'languages': langs})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@speech_bp.route('/api/v1/speech/result', methods=['POST'])
def submit_result():
    """提交识别结果"""
    try:
        data = request.json or {}
        session_id = validate_str(data.get('session_id', ''), max_len=64, name='session_id')
        text = validate_str(data.get('text', ''), max_len=5000, name='text')
        confidence = float(data.get('confidence', 0.0))
        is_final = bool(data.get('is_final', False))
        engine_used = data.get('engine_used', 'web_api')
        language = data.get('language', 'zh-CN')

        svc = get_speech_service()
        result = svc.add_result(session_id, text, confidence,
                                is_final=is_final, engine_used=engine_used)

        return jsonify({
            'success': True,
            'result': {
                'text': result.text if result else text,
                'confidence': confidence,
                'is_final': is_final
            }
        })
    except Exception as e:
        logger.error(f"Speech result submission error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@speech_bp.route('/api/v1/speech/session/<session_id>', methods=['GET'])
def get_session(session_id):
    """获取会话状态和全部文字"""
    try:
        svc = get_speech_service()
        session = svc.get_session(session_id)
        if not session:
            return jsonify({'success': False, 'message': '会话不存在'}), 404

        all_text = svc.get_all_text(session_id, final_only=False)

        return jsonify({
            'success': True,
            'session_id': session_id,
            'language': session.language,
            'state': session.state.value,
            'text': all_text,
            'result_count': len(session.results)
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@speech_bp.route('/api/v1/speech/session/<session_id>', methods=['DELETE'])
def cleanup_session(session_id):
    """清理会话"""
    try:
        svc = get_speech_service()
        svc.cleanup_session(session_id)
        return jsonify({'success': True, 'message': '会话已清理'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@speech_bp.route('/api/v1/speech/health', methods=['GET'])
def speech_health():
    """语音服务健康检查"""
    try:
        svc = get_speech_service()
        return jsonify({
            'success': True,
            'supported_languages': len(SUPPORTED_LANGUAGES),
            'engine': svc._engine.value if hasattr(svc._engine, 'value') else str(svc._engine),
            'active_sessions': len(svc._sessions)
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

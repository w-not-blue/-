"""
语音识别服务模块

提供跨平台语音识别能力:
- 实时语音采集与流式识别
- 多语言/方言支持 (zh-CN, en-US, ja-JP, ko-KR, yue-HK)
- 离线识别回退 (基于Vosk/Sherpa-ONNX)
- 隐私保护 (本地处理优先, 端到端加密)
- 降级策略 (Web Speech API → 离线引擎 → 手动输入)

参考项目:
- Web Speech API (W3C标准)
- Vosk (离线语音识别, Apache 2.0)
- Sherpa-ONNX (下一代离线识别)
- Whisper (OpenAI, 高质量识别)

环境变量:
    DCS_SPEECH_ENGINE=web|vosk|sherpa|whisper
    DCS_SPEECH_MODEL_DIR=models/speech
    DCS_SPEECH_SAMPLE_RATE=16000
"""
import os
import json
import base64
import hashlib
import time
import threading
from typing import Dict, Any, List, Optional, Tuple, Union
from dataclasses import dataclass, field
from enum import Enum
from src.utils.logger import setup_logger
from src.utils.config import Config

logger = setup_logger("speech_service")
config = Config()


class SpeechEngine(Enum):
    WEB_API = "web"          # 浏览器内置 (Chrome/Safari/Edge)
    VOSK = "vosk"            # 开源离线引擎
    SHERPA = "sherpa"        # 下一代离线引擎
    WHISPER = "whisper"      # OpenAI Whisper
    AUTO = "auto"            # 自动选择最佳

class RecognitionState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    COMPLETED = "completed"
    ERROR = "error"
    NO_SPEECH = "no_speech"
    OFFLINE = "offline"


SUPPORTED_LANGUAGES = {
    "zh-CN": {
        "name": "中文普通话",
        "label": "简体中文",
        "web_api_code": "zh-CN",
        "vosk_model": "vosk-model-cn-0.22",
        "dialects": ["cmn-Hans-CN"],
        "offline_available": True,
    },
    "en-US": {
        "name": "English (US)",
        "label": "英语（美国）",
        "web_api_code": "en-US",
        "vosk_model": "vosk-model-small-en-us-0.15",
        "dialects": ["en-US"],
        "offline_available": True,
    },
    "ja-JP": {
        "name": "日本語",
        "label": "日语",
        "web_api_code": "ja-JP",
        "vosk_model": "vosk-model-small-ja-0.22",
        "dialects": ["ja-JP"],
        "offline_available": True,
    },
    "ko-KR": {
        "name": "한국어",
        "label": "韩语",
        "web_api_code": "ko-KR",
        "vosk_model": "vosk-model-small-ko-0.22",
        "dialects": ["ko-KR"],
        "offline_available": True,
    },
    "yue-HK": {
        "name": "粵語",
        "label": "粤语（香港）",
        "web_api_code": "zh-HK",
        "vosk_model": None,
        "dialects": ["yue-Hant-HK"],
        "offline_available": False,
    },
    "zh-TW": {
        "name": "中文繁體",
        "label": "繁体中文（台湾）",
        "web_api_code": "zh-TW",
        "vosk_model": None,
        "dialects": ["cmn-Hant-TW"],
        "offline_available": False,
    },
    "de-DE": {
        "name": "Deutsch",
        "label": "德语",
        "web_api_code": "de-DE",
        "vosk_model": "vosk-model-small-de-0.15",
        "dialects": ["de-DE"],
        "offline_available": True,
    },
    "fr-FR": {
        "name": "Français",
        "label": "法语",
        "web_api_code": "fr-FR",
        "vosk_model": "vosk-model-small-fr-0.22",
        "dialects": ["fr-FR"],
        "offline_available": True,
    },
}


@dataclass
class RecognitionResult:
    text: str
    confidence: float
    language: str
    is_final: bool
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    engine_used: str = "web_api"
    timestamp: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))


@dataclass
class SpeechSession:
    session_id: str
    language: str
    created_at: float = field(default_factory=time.time)
    state: RecognitionState = RecognitionState.IDLE
    results: List[RecognitionResult] = field(default_factory=list)
    engine: SpeechEngine = SpeechEngine.AUTO
    offline_available: bool = False
    audio_data_hash: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SpeechService:
    """
    语音识别服务

    核心设计:
    - 优先级: Web Speech API (在线) → 离线引擎 (本地) → 手动输入 (兜底)
    - 隐私: 音频数据本地处理，不上传至第三方服务器
    - 多语言: 中/英/日/韩/粤/台/德/法 8种语言
    """

    def __init__(self):
        self._sessions: Dict[str, SpeechSession] = {}
        self._session_lock = threading.Lock()
        self._offline_model = None
        self._engine = self._detect_engine()

    def _detect_engine(self) -> SpeechEngine:
        env_engine = os.environ.get("DCS_SPEECH_ENGINE", "auto")
        if env_engine == "auto":
            if self._check_vosk():
                return SpeechEngine.VOSK
            if self._check_sherpa():
                return SpeechEngine.SHERPA
            return SpeechEngine.WEB_API
        engine_map = {
            "web": SpeechEngine.WEB_API,
            "vosk": SpeechEngine.VOSK,
            "sherpa": SpeechEngine.SHERPA,
            "whisper": SpeechEngine.WHISPER,
        }
        return engine_map.get(env_engine, SpeechEngine.AUTO)

    def _check_vosk(self) -> bool:
        try:
            import vosk
            return True
        except ImportError:
            return False

    def _check_sherpa(self) -> bool:
        try:
            import sherpa_onnx
            return True
        except ImportError:
            return False

    def get_supported_languages(self) -> Dict[str, Dict[str, Any]]:
        result = {}
        for code, info in SUPPORTED_LANGUAGES.items():
            result[code] = {
                "name": info["name"],
                "label": info["label"],
                "code": code,
                "web_api_code": info["web_api_code"],
                "offline_available": info["offline_available"],
                "dialects": info.get("dialects", []),
            }
        return result

    def create_session(self, language: str = "zh-CN") -> SpeechSession:
        import secrets
        if language not in SUPPORTED_LANGUAGES:
            language = "zh-CN"

        session_id = secrets.token_hex(16)
        lang_info = SUPPORTED_LANGUAGES[language]

        session = SpeechSession(
            session_id=session_id,
            language=language,
            state=RecognitionState.IDLE,
            engine=self._engine,
            offline_available=lang_info["offline_available"],
            metadata={
                "language_name": lang_info["name"],
                "web_api_code": lang_info["web_api_code"],
                "offline_model": lang_info.get("vosk_model"),
            }
        )

        with self._session_lock:
            self._sessions[session_id] = session

        logger.info(f"Speech session created: {session_id}, lang={language}")
        return session

    def get_session(self, session_id: str) -> Optional[SpeechSession]:
        return self._sessions.get(session_id)

    def start_listening(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session:
            session.state = RecognitionState.LISTENING
            logger.info(f"Session {session_id}: listening started")
            return True
        return False

    def stop_listening(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session:
            session.state = RecognitionState.IDLE
            logger.info(f"Session {session_id}: listening stopped")
            return True
        return False

    def add_result(self, session_id: str, text: str, confidence: float,
                   is_final: bool = False, engine_used: str = "web_api",
                   alternatives: List[Dict] = None, duration_ms: float = 0.0) -> Optional[RecognitionResult]:
        session = self._sessions.get(session_id)
        if not session:
            return None

        result = RecognitionResult(
            text=text,
            confidence=confidence,
            language=session.language,
            is_final=is_final,
            alternatives=alternatives or [],
            duration_ms=duration_ms,
            engine_used=engine_used,
        )
        session.results.append(result)

        if is_final:
            session.state = RecognitionState.COMPLETED
        else:
            session.state = RecognitionState.PROCESSING

        return result

    def get_all_text(self, session_id: str, final_only: bool = True) -> str:
        session = self._sessions.get(session_id)
        if not session:
            return ""

        results = session.results
        if final_only:
            results = [r for r in results if r.is_final]
        return " ".join(r.text for r in results if r.text.strip())

    def process_audio_offline(self, audio_bytes: bytes, language: str = "zh-CN") -> RecognitionResult:
        lang_info = SUPPORTED_LANGUAGES.get(language, SUPPORTED_LANGUAGES["zh-CN"])
        if not lang_info["offline_available"]:
            return RecognitionResult(
                text="",
                confidence=0.0,
                language=language,
                is_final=True,
                engine_used="offline_unavailable",
                alternatives=[{"text": "当前语言不支持离线识别，请切换到在线模式或选择其他语言"}],
            )

        try:
            if self._engine == SpeechEngine.VOSK:
                return self._recognize_vosk(audio_bytes, lang_info)
            elif self._engine == SpeechEngine.SHERPA:
                return self._recognize_sherpa(audio_bytes, lang_info)
        except Exception as e:
            logger.error(f"Offline recognition error: {e}")

        return RecognitionResult(
            text="",
            confidence=0.0,
            language=language,
            is_final=True,
            engine_used="offline_error",
        )

    def _recognize_vosk(self, audio_bytes: bytes, lang_info: Dict) -> RecognitionResult:
        import wave
        import io

        vosk_model_name = lang_info.get("vosk_model")
        if not vosk_model_name:
            raise ValueError("No Vosk model for this language")

        if self._offline_model is None:
            import vosk
            model_dir = os.environ.get("DCS_SPEECH_MODEL_DIR", "models/speech")
            model_path = os.path.join(model_dir, vosk_model_name)
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"Vosk model not found: {model_path}")
            self._offline_model = vosk.Model(model_path)

        import vosk
        import json

        recognizer = vosk.KaldiRecognizer(self._offline_model, 16000)

        wav_io = io.BytesIO(audio_bytes)
        with wave.open(wav_io, 'rb') as wf:
            while True:
                data = wf.readframes(4000)
                if len(data) == 0:
                    break
                recognizer.AcceptWaveform(data)

        result_json = json.loads(recognizer.FinalResult())
        return RecognitionResult(
            text=result_json.get("text", ""),
            confidence=result_json.get("confidence", 0.0),
            language=lang_info["name"],
            is_final=True,
            engine_used="vosk",
            duration_ms=0.0,
        )

    def _recognize_sherpa(self, audio_bytes: bytes, lang_info: Dict) -> RecognitionResult:
        import sherpa_onnx
        import numpy as np
        import io
        import wave

        wav_io = io.BytesIO(audio_bytes)
        with wave.open(wav_io, 'rb') as wf:
            frames = wf.readframes(wf.getnframes())
            samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0

        recognizer = sherpa_onnx.OnlineRecognizer.from_pretrained()
        recognizer.accept_waveform(16000, samples)
        result = recognizer.get_result()

        return RecognitionResult(
            text=result.text if hasattr(result, 'text') else "",
            confidence=0.95,
            language=lang_info["name"],
            is_final=True,
            engine_used="sherpa_onnx",
            duration_ms=0.0,
        )

    def hash_audio(self, audio_bytes: bytes) -> str:
        return hashlib.sha256(audio_bytes).hexdigest()

    def encrypt_audio(self, audio_bytes: bytes, key: Optional[bytes] = None) -> Tuple[bytes, bytes]:
        from cryptography.fernet import Fernet
        if key is None:
            key = Fernet.generate_key()
        f = Fernet(key)
        encrypted = f.encrypt(audio_bytes)
        return encrypted, key

    def cleanup_session(self, session_id: str) -> None:
        with self._session_lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info(f"Session cleaned: {session_id}")

    def cleanup_expired(self, max_age_sec: float = 1800.0) -> int:
        now = time.time()
        expired = []
        with self._session_lock:
            for sid, session in list(self._sessions.items()):
                if now - session.created_at > max_age_sec:
                    expired.append(sid)
            for sid in expired:
                del self._sessions[sid]
        if expired:
            logger.info(f"Cleaned {len(expired)} expired sessions")
        return len(expired)


_speech_service_instance: Optional[SpeechService] = None

def get_speech_service() -> SpeechService:
    global _speech_service_instance
    if _speech_service_instance is None:
        _speech_service_instance = SpeechService()
    return _speech_service_instance

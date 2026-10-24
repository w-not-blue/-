const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;

let recognition = null;
let isListening = false;
let manualStop = false;
let currentLang = 'zh-CN';
let finalTranscript = '';
let interimTranscript = '';
let restartTimer = null;

const LANGUAGES = {
    'zh-CN': { name: '中文普通话', webCode: 'zh-CN', emoji: '🇨🇳' },
    'en-US': { name: 'English', webCode: 'en-US', emoji: '🇺🇸' },
    'ja-JP': { name: '日本語', webCode: 'ja-JP', emoji: '🇯🇵' },
    'ko-KR': { name: '한국어', webCode: 'ko-KR', emoji: '🇰🇷' },
    'yue-HK': { name: '粵語', webCode: 'zh-HK', emoji: '🇭🇰' },
    'zh-TW': { name: '繁體中文', webCode: 'zh-TW', emoji: '🇹🇼' },
    'de-DE': { name: 'Deutsch', webCode: 'de-DE', emoji: '🇩🇪' },
    'fr-FR': { name: 'Français', webCode: 'fr-FR', emoji: '🇫🇷' }
};

document.addEventListener('DOMContentLoaded', function () {
    checkBrowserSupport();
    renderLangChips();
    updateUIForIdle();
    setStatus('idle', '就绪');
});

function checkBrowserSupport() {
    const button = document.getElementById('toggleBtn');
    if (!SpeechRecognitionAPI) {
        setStatus('error', '浏览器不支持语音识别');
        showError('当前浏览器不支持语音识别，请使用最新版 Edge、Chrome 或 Safari。');
        if (button) button.disabled = true;
        const badge = document.getElementById('engineBadge');
        if (badge) badge.textContent = '引擎: 不支持';
        return;
    }
    const badge = document.getElementById('engineBadge');
    if (badge) badge.textContent = '引擎: 浏览器语音识别';
}

function renderLangChips() {
    const container = document.getElementById('langChips');
    if (!container) return;
    container.innerHTML = Object.keys(LANGUAGES).map(function (code) {
        const info = LANGUAGES[code];
        return '<span class="lang-chip ' + (code === currentLang ? 'active' : '') + '" ' +
            'onclick="switchLanguage(\'' + code + '\')" title="' + info.name + '">' +
            info.emoji + ' ' + info.name + '</span>';
    }).join('');
}

function switchLanguage(langCode) {
    const wasListening = isListening;
    if (wasListening) stopListening();
    currentLang = langCode;
    renderLangChips();
    setStatus('idle', '已切换至 ' + LANGUAGES[langCode].name);
    if (wasListening) {
        setTimeout(startListening, 350);
    }
}

function toggleListening() {
    if (isListening) {
        stopListening();
    } else {
        startListening();
    }
}

function startListening() {
    if (isListening) return;
    if (!SpeechRecognitionAPI) {
        showError('当前浏览器不支持语音识别，请使用最新版 Edge、Chrome 或 Safari。');
        return;
    }

    hideError();
    const langInfo = LANGUAGES[currentLang] || LANGUAGES['zh-CN'];
    manualStop = false;

    try {
        recognition = new SpeechRecognitionAPI();
        recognition.lang = langInfo.webCode;
        recognition.continuous = true;
        recognition.interimResults = true;
        recognition.maxAlternatives = 1;

        recognition.onstart = function () {
            isListening = true;
            updateUIForListening();
            setStatus('listening', '正在聆听...');
        };

        recognition.onresult = function (event) {
            let interim = '';
            let confidence = 0;
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const result = event.results[i];
                if (result.isFinal) {
                    finalTranscript += result[0].transcript;
                    confidence = result[0].confidence || confidence;
                } else {
                    interim += result[0].transcript;
                }
            }
            interimTranscript = interim;
            updateResultBox(finalTranscript, interimTranscript);
            if (confidence > 0) {
                showConfidence(confidence);
            }
        };

        recognition.onerror = function (event) {
            handleRecognitionError(event);
        };

        recognition.onend = function () {
            if (isListening && !manualStop) {
                scheduleRestart();
            } else {
                isListening = false;
                updateUIForIdle();
                if (!manualStop) setStatus('idle', '识别已停止');
            }
        };

        recognition.onspeechend = function () {
            setStatus('processing', '处理中...');
        };

        recognition.start();
    } catch (error) {
        showError('无法启动语音识别：' + (error.message || '请检查麦克风权限'));
        setStatus('error', '启动失败');
        updateUIForIdle();
    }
}

function stopListening() {
    manualStop = true;
    isListening = false;
    clearTimeout(restartTimer);
    restartTimer = null;
    if (recognition) {
        try { recognition.stop(); } catch (error) {}
    }
    updateUIForIdle();
    setStatus('idle', '已停止');
}

function scheduleRestart() {
    clearTimeout(restartTimer);
    restartTimer = setTimeout(function () {
        if (!isListening || manualStop || !recognition) return;
        try {
            recognition.start();
        } catch (error) {
            isListening = false;
            updateUIForIdle();
            setStatus('error', '自动恢复失败');
        }
    }, 300);
}

function handleRecognitionError(event) {
    manualStop = true;
    isListening = false;
    updateUIForIdle();

    const code = event.error || 'unknown';
    const messages = {
        'not-allowed': '麦克风权限被拒绝，请在浏览器地址栏的站点设置中允许麦克风访问。',
        'service-not-allowed': '浏览器语音服务被禁用，请检查浏览器设置。',
        'no-speech': '没有检测到语音，请靠近麦克风重新开始。',
        'audio-capture': '未检测到可用的麦克风设备。',
        'network': '浏览器语音识别服务不可用，请检查网络后重试。',
        'aborted': '语音识别已中断，请重新开始。'
    };
    if (code === 'aborted' && manualStop) return;
    const message = messages[code] || ('识别失败：' + code);
    showError(message);
    setStatus('error', '识别失败');
}

function retryRecognition() {
    hideError();
    if (isListening) stopListening();
    setTimeout(startListening, 300);
}

function updateResultBox(finalText, interimText) {
    const box = document.getElementById('resultBox');
    const placeholder = document.getElementById('placeholderText');
    if (!box) return;
    if (placeholder) placeholder.style.display = 'none';
    box.innerHTML = escapeHtml(finalText || '') +
        (interimText ? '<span class="interim">' + escapeHtml(interimText) + '</span>' : '');
    box.scrollTop = box.scrollHeight;
}

function showConfidence(value) {
    const badge = document.getElementById('confidenceBadge');
    if (!badge) return;
    const pct = Math.max(0, Math.min(100, Math.round(value * 100)));
    badge.textContent = '置信度 ' + pct + '%';
    badge.className = pct >= 90 ? 'conf-badge' : 'badge bg-warning text-dark';
    badge.style.visibility = 'visible';
}

function updateUIForListening() {
    const button = document.getElementById('toggleBtn');
    const ring = document.getElementById('micRing');
    const box = document.getElementById('resultBox');
    if (button) {
        button.innerHTML = '<i class="fas fa-stop me-1"></i>停止录音';
        button.style.background = '#dc2626';
    }
    if (ring) {
        ring.classList.add('pulse');
        ring.innerHTML = '<i class="fas fa-microphone"></i>';
    }
    if (box) box.classList.add('listening');
    const indicator = document.getElementById('statusIndicator');
    if (indicator) indicator.className = 'status-indicator active';
}

function updateUIForIdle() {
    const button = document.getElementById('toggleBtn');
    const ring = document.getElementById('micRing');
    const box = document.getElementById('resultBox');
    if (button) {
        button.innerHTML = '<i class="fas fa-play me-1"></i>开始录音';
        button.style.background = '#7c3aed';
    }
    if (ring) {
        ring.classList.remove('pulse');
        ring.innerHTML = '<i class="fas fa-microphone"></i>';
    }
    if (box) box.classList.remove('listening');
    const indicator = document.getElementById('statusIndicator');
    if (indicator) indicator.className = 'status-indicator';
}

function setStatus(state, text) {
    const statusText = document.getElementById('statusText');
    const indicator = document.getElementById('statusIndicator');
    if (statusText) statusText.textContent = text;
    if (!indicator) return;
    indicator.className = 'status-indicator';
    if (state === 'listening') indicator.classList.add('active');
    else if (state === 'processing') indicator.classList.add('processing');
    else if (state === 'completed') indicator.classList.add('done');
}

function showError(message) {
    const box = document.getElementById('errorBox');
    const text = document.getElementById('errorMessage');
    if (text) text.textContent = message;
    if (box) box.classList.add('show');
}

function hideError() {
    const box = document.getElementById('errorBox');
    if (box) box.classList.remove('show');
}

function copyText() {
    const text = (finalTranscript || '').trim();
    if (!text) {
        showToast('没有可复制的文字', 'warning');
        return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(function () {
            showToast('已复制到剪贴板', 'success');
        }).catch(fallbackCopy);
    } else {
        fallbackCopy();
    }
}

function fallbackCopy() {
    const textarea = document.createElement('textarea');
    textarea.value = finalTranscript;
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    document.body.removeChild(textarea);
    showToast('已复制到剪贴板', 'success');
}

function clearText() {
    finalTranscript = '';
    interimTranscript = '';
    const box = document.getElementById('resultBox');
    const placeholder = document.getElementById('placeholderText');
    const badge = document.getElementById('confidenceBadge');
    if (box) box.innerHTML = '';
    if (placeholder) {
        placeholder.style.display = 'block';
        box.appendChild(placeholder);
    }
    if (badge) badge.style.visibility = 'hidden';
}

function downloadText() {
    const text = (finalTranscript || '').trim();
    if (!text) {
        showToast('没有可导出的文字', 'warning');
        return;
    }
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'speech_' + new Date().toISOString().slice(0, 19).replace(/:/g, '-') + '.txt';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}

function escapeHtml(value) {
    return String(value == null ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

function showToast(message, type) {
    const toast = document.createElement('div');
    toast.className = 'toast position-fixed bottom-0 end-0 m-4 bg-' + (type || 'success') + ' text-white';
    toast.style.zIndex = '9999';
    toast.innerHTML = '<div class="toast-body">' + escapeHtml(message) + '</div>';
    document.body.appendChild(toast);
    setTimeout(function () { toast.remove(); }, 2500);
}
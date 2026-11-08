const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;

let recognition = null;
let isListening = false;
let manualStop = false;
let currentLang = 'zh-CN';
let finalTranscript = '';
let interimTranscript = '';
let restartTimer = null;
let restartAttempts = 0;      // 连续自动恢复次数
let hadSpeech = false;        // 本轮是否已经识别到过内容

// 这些错误一旦出现就无法继续，必须让用户处理
const FATAL_ERRORS = {
    'not-allowed': '麦克风权限被拒绝，请在浏览器地址栏的站点设置中允许麦克风访问。',
    'service-not-allowed': '浏览器语音服务被禁用，请检查浏览器设置。',
    'audio-capture': '未检测到可用的麦克风设备，请插好麦克风后重试。',
    'language-not-supported': '当前浏览器不支持所选语言，请换一种语言。'
};

// 这些错误是可恢复的：静音太久、网络抖动、会话被浏览器主动结束
const RECOVERABLE_ERRORS = ['no-speech', 'network', 'aborted'];

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
    manualStop = false;
    restartAttempts = 0;
    hadSpeech = false;
    beginRecognition();
}

/* 创建一个识别实例并启动。每次重启都新建实例：
   浏览器在静音或网络抖动后会自己结束会话，复用旧实例容易出现 InvalidStateError。 */
function beginRecognition() {
    if (manualStop) return;
    const langInfo = LANGUAGES[currentLang] || LANGUAGES['zh-CN'];

    // 丢掉旧实例的监听，避免它结束时再触发一次重启
    detachRecognition();
    clearTimeout(restartTimer);
    restartTimer = null;

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
            if (finalTranscript || interim) {
                hadSpeech = true;
                restartAttempts = 0;   // 有内容就说明链路正常，重置恢复计数
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

        // 浏览器自己结束会话（静音超时、网络中断）时继续听，只有用户点停止才真停
        recognition.onend = function () {
            if (manualStop) {
                isListening = false;
                updateUIForIdle();
                return;
            }
            scheduleRestart();
        };

        // 连续模式下说话停顿也会触发 speechend，这里不要切到"处理中"，保持聆听状态
        recognition.onspeechend = function () {
            if (!manualStop) setStatus('listening', '正在聆听...');
        };

        recognition.start();
    } catch (error) {
        const msg = error && error.message ? error.message : '';
        if (/already started|InvalidStateError/i.test(msg)) {
            scheduleRestart();          // 实例还在启动中，稍后重试
            return;
        }
        showError('无法启动语音识别：' + (msg || '请检查麦克风权限'));
        setStatus('error', '启动失败');
        manualStop = true;
        isListening = false;
        updateUIForIdle();
    }
}

function detachRecognition() {
    if (!recognition) return;
    try {
        recognition.onstart = null;
        recognition.onresult = null;
        recognition.onerror = null;
        recognition.onend = null;
        recognition.onspeechend = null;
        recognition.abort();
    } catch (error) {}
    recognition = null;
}

function stopListening() {
    manualStop = true;
    isListening = false;
    clearTimeout(restartTimer);
    restartTimer = null;
    detachRecognition();
    updateUIForIdle();
    setStatus('idle', '已停止');
}

/* 自动恢复：浏览器结束会话后 0.3~1.5 秒内重新开始 */
function scheduleRestart() {
    if (manualStop) return;
    clearTimeout(restartTimer);
    restartAttempts += 1;

    if (restartAttempts > 20) {
        manualStop = true;
        isListening = false;
        updateUIForIdle();
        setStatus('error', '识别中断');
        showError('语音识别多次中断，请检查麦克风是否被其他程序占用、网络是否正常，然后再点开始录音。');
        return;
    }

    const delay = Math.min(300 + restartAttempts * 250, 1500);
    setStatus('listening', '正在聆听...');
    restartTimer = setTimeout(function () {
        if (manualStop) return;
        beginRecognition();
    }, delay);
}

function handleRecognitionError(event) {
    const code = (event && event.error) || 'unknown';

    // 致命错误：停下来并且提示用户处理
    if (FATAL_ERRORS[code]) {
        manualStop = true;
        isListening = false;
        clearTimeout(restartTimer);
        updateUIForIdle();
        setStatus('error', '识别失败');
        showError(FATAL_ERRORS[code]);
        return;
    }

    // 可恢复错误：保持聆听状态，交给 onend 触发自动重启
    if (RECOVERABLE_ERRORS.indexOf(code) >= 0) {
        if (code === 'no-speech') {
            setStatus('listening', '正在聆听...（未检测到语音）');
        } else if (code === 'network') {
            setStatus('listening', '网络波动，正在重连语音服务...');
        }
        return;
    }

    // 未知错误也不直接停：记录后让 onend 决定是否重启
    setStatus('listening', '正在聆听...');
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
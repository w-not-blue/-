(function () {
    const API_BASE = '/api/v1';
    const ASSISTANT_AVATAR = '/static/assistant/avatar.png';
    const WAKE_PHRASES = ['你好', '您好'];
    const FOOT_DEFAULT = '说「你好，yumo」启动语音对话；说「结束」退出；麦克风按钮可手动转文字';

    function normalizeSpeech(text) {
        return String(text || '')
            .toLowerCase()
            .replace(/[\s,，。.!！？、:：;；"'“”‘’]/g, '');
    }

    function isWakeWord(text) {
        const normalized = normalizeSpeech(text);
        return WAKE_PHRASES.some(function (phrase) {
            return normalized.indexOf(phrase) >= 0;
        });
    }

    const state = {
        open: false,
        busy: false,
        history: [],
        wake: false,
        wakeRec: null,
        listening: false,
        resumeWake: false,
        footTimer: null,
        audioCtx: null,
        dragState: null,
        suppressClickUntil: 0,
        autoRun: false,
        pendingTrial: null,
        dictationRec: null,
        dictationMode: null,
        dictationStopRequested: false,
        dictationBaseText: '',
        dictationFinalText: '',
        dictationRestartTimer: null,
        dictationError: '',
        micChecking: false,
        micGranted: false,
        micError: '',
        micResultSeen: false,
        conversation: false,
        conversationRec: null,
        wakeBlocked: false,
        wakeRetryAt: 0,
        wakeActive: false,
        ttsActive: false
    };

    const POSITION_KEY = 'aiAssistantPosition';
    let preferredVoice = null;

    const el = {};

    function cache() {
        el.root = document.getElementById('aiAssistant');
        el.orb = document.getElementById('aiOrb');
        el.panel = document.getElementById('aiPanel');
        el.messages = document.getElementById('aiMessages');
        el.input = document.getElementById('aiInput');
        el.form = document.getElementById('aiForm');
        el.send = document.getElementById('aiSend');
        el.mic = document.getElementById('aiMic');
        el.close = document.getElementById('aiClose');
        el.engine = document.getElementById('aiAssistantEngine');
        el.dot = document.getElementById('aiDot');
        el.foot = document.getElementById('aiFoot');
        el.quick = document.getElementById('aiQuick');
        el.autoRun = document.getElementById('aiAutoRun');
    }

    function escapeHtml(value) {
        return String(value === null || value === undefined ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function playBeep() {
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return;
            if (!state.audioCtx) state.audioCtx = new Ctx();
            const ctx = state.audioCtx;
            if (ctx.state === 'suspended') ctx.resume();
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = 'sine';
            osc.frequency.value = 880;
            gain.gain.setValueAtTime(0.0001, ctx.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.2, ctx.currentTime + 0.02);
            gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.2);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start();
            osc.stop(ctx.currentTime + 0.22);
        } catch (error) {
             
        }
    }

    function setOrbFlag(name, on) {
        if (el.orb) el.orb.classList.toggle(name, !!on);
    }

    function setWakeListening(on) {
        setOrbFlag('is-wake', on);
    }

    function setListening(on) {
        setOrbFlag('is-listening', on);
    }

    function setSpeaking(on) {
        setOrbFlag('is-speaking', on);
    }

    function flashOrb() {
        if (!el.orb) return;
        el.orb.classList.add('is-heard');
        setTimeout(function () { el.orb.classList.remove('is-heard'); }, 1200);
    }

    function setFoot(text, timeoutMs) {
        if (!el.foot) return;
        el.foot.textContent = text;
        clearTimeout(state.footTimer);
        if (timeoutMs) {
            state.footTimer = setTimeout(function () {
                if (state.wake) {
                    el.foot.textContent = '语音唤醒已开启：说「你好」或「你好，yumo」即可唤起';
                }
            }, timeoutMs);
        }
    }

    function renderMarkdown(value) {
        const lines = escapeHtml(value).split('\n');
        let html = '';
        let listTag = '';
        lines.forEach(function (rawLine) {
            const line = rawLine.replace(/\s+$/, '');
            const bullet = line.match(/^\s*[-*]\s+(.+)$/);
            const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
            if (bullet || numbered) {
                const tag = bullet ? 'ul' : 'ol';
                if (listTag !== tag) {
                    if (listTag) html += '</' + listTag + '>';
                    html += '<' + tag + '>';
                    listTag = tag;
                }
                html += '<li>' + inlineMarkdown(bullet ? bullet[1] : numbered[1]) + '</li>';
            } else {
                if (listTag) { html += '</' + listTag + '>'; listTag = ''; }
                if (line) html += '<p>' + inlineMarkdown(line) + '</p>';
            }
        });
        if (listTag) html += '</' + listTag + '>';
        return html || '<p></p>';
    }

    function inlineMarkdown(text) {
        return text
            .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
            .replace(/`([^`]+)`/g, '<code>$1</code>');
    }

    function avatarNode(className) {
        const image = document.createElement('img');
        image.className = className;
        image.src = ASSISTANT_AVATAR;
        image.alt = '';
        return image;
    }

    async function applySuggestedRun(run) {
        if (!run || !run.params) return;
        const button = document.getElementById('runBtn');
        if (button && button.disabled) return;
        const params = run.params;
        const servers = document.getElementById('numServers');
        const hours = document.getElementById('simHours');
        const mode = document.getElementById('runMode');
        if (servers && params.num_servers) servers.value = params.num_servers;
        if (hours && params.simulation_hours) hours.value = params.simulation_hours;
        if (mode && params.run_mode) mode.value = params.run_mode;
        if (params.site_id && typeof window.applySimulationSite === 'function') {
            await window.applySimulationSite(params.site_id);
        }
        if (params.school_name && typeof window.applySimulationSchool === 'function') {
            await window.applySimulationSchool(params.school_name, params.site_id);
        }
        if (Array.isArray(params.algorithms)) {
            document.querySelectorAll('input[name="algorithms"]').forEach(function (box) {
                box.checked = params.algorithms.indexOf(box.value) >= 0;
            });
        }
        const form = document.getElementById('simulationForm');
        if (!form) return;
        window.pendingSimulationOverride = {
            site_id: params.site_id || '',
            school_name: params.school_name || ''
        };
        const lastUser = state.history.slice().reverse().find(function (item) {
            return item.role === 'user';
        });
        state.pendingTrial = {
            label: run.label || '建议配置',
            question: lastUser ? lastUser.content : '',
            startedAt: Date.now()
        };
        form.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setFoot('已填入建议配置，正在按新参数试算...', 6000);
        setTimeout(function () {
            if (form.requestSubmit) form.requestSubmit();
            else form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
        }, 500);
    }

    function renderRunCard(run) {
        if (!run) return null;
        const card = document.createElement('div');
        const status = run.status || 'running';
        card.className = 'ai-run-card is-' + status;
        card.dataset.runId = run.run_id || '';
        const head = document.createElement('div');
        const title = document.createElement('strong');
        title.textContent = status === 'success' ? '运行完成' : (status === 'failed' ? '运行失败' : '运行中');
        head.appendChild(title);
        const id = document.createElement('span');
        id.textContent = '运行编号：' + (run.run_id || '');
        head.appendChild(id);
        card.appendChild(head);
        const meta = document.createElement('div');
        meta.className = 'ai-run-meta';
        const params = run.params || {};
        if (params.school_name) meta.appendChild(document.createTextNode(params.school_name));
        if (params.num_servers) meta.appendChild(document.createTextNode(params.num_servers + ' 台'));
        if (params.simulation_hours) meta.appendChild(document.createTextNode(params.simulation_hours + ' 小时'));
        if (Array.isArray(params.algorithms)) meta.appendChild(document.createTextNode(params.algorithms.length + ' 个算法'));
        if (run.label) meta.appendChild(document.createTextNode(run.label));
        card.appendChild(meta);
        if (run.error) {
            const error = document.createElement('div');
            error.textContent = run.error;
            card.appendChild(error);
        }
        return card;
    }

    function scrollCardIntoView(card) {
        if (!card || !card.scrollIntoView) return;
        const run = function () { card.scrollIntoView({ block: 'start' }); };
        if (typeof requestAnimationFrame === 'function') {
            requestAnimationFrame(run);
            setTimeout(run, 60);
        } else {
            run();
        }
    }

    function optimizationStatusText(status) {
        if (status === 'success') return '寻优完成';
        if (status === 'failed') return '寻优失败';
        if (status === 'queued') return '排队中';
        return '寻优中';
    }

    function renderOptimizationCard(job) {
        const card = document.createElement('div');
        card.className = 'ai-opt-card';
        card.dataset.jobId = job.job_id || '';
        const head = document.createElement('div');
        head.className = 'ai-opt-head';
        const title = document.createElement('strong');
        title.textContent = 'AI 方案寻优';
        const status = document.createElement('span');
        status.className = 'ai-opt-status';
        head.appendChild(title);
        head.appendChild(status);
        card.appendChild(head);
        const progress = document.createElement('div');
        progress.className = 'ai-opt-progress';
        const bar = document.createElement('div');
        bar.className = 'ai-opt-progress-bar';
        progress.appendChild(bar);
        card.appendChild(progress);
        const body = document.createElement('div');
        body.className = 'ai-opt-body';
        card.appendChild(body);
        updateOptimizationCard(card, job);
        return card;
    }

    function updateOptimizationCard(card, job) {
        if (!card || !job) return;
        card.dataset.jobId = job.job_id || card.dataset.jobId;
        const status = card.querySelector('.ai-opt-status');
        if (status) {
            status.textContent = optimizationStatusText(job.status);
            status.dataset.status = job.status || '';
        }
        const bar = card.querySelector('.ai-opt-progress-bar');
        if (bar) bar.style.width = Math.max(0, Math.min(100, Number(job.progress) || 0)) + '%';
        const body = card.querySelector('.ai-opt-body');
        if (!body) return;
        if (job.status === 'failed') {
            body.innerHTML = '<p class="ai-opt-note">' + escapeHtml(job.error || job.message || '寻优失败') + '</p>';
            scrollCardIntoView(card);
            return;
        }
        if (!job.result) {
            body.innerHTML = '<p class="ai-opt-note">' + escapeHtml(job.message || '正在评估候选方案…') + '</p>';
            scrollCardIntoView(card);
            return;
        }
        const result = job.result;
        const explanation = result.explanation || {};
        let html = '<p class="ai-opt-summary">' + escapeHtml(explanation.summary || '') + '</p>';
        html += '<div class="ai-opt-plans">';
        (result.top || []).forEach(function (plan) {
            const metrics = plan.metrics || {};
            html += '<div class="ai-opt-plan' + (plan.rank === 1 ? ' is-best' : '') + '">';
            html += '<div class="ai-opt-plan-head"><span>' + plan.num_servers + ' 台 · ' +
                escapeHtml(plan.algorithm_label || plan.algorithm) + '</span><em>' +
                (plan.rank === 1 ? '推荐' : '备选') + '</em></div>';
            if (plan.weight_label) {
                html += '<div class="ai-opt-plan-weight">' + escapeHtml(plan.weight_label) + '</div>';
            }
            html += '<div class="ai-opt-plan-metrics">';
            html += '<span>PUE ' + (metrics.avg_pue != null ? metrics.avg_pue.toFixed(3) : '--') + '</span>';
            html += '<span>单位能耗 ' + (metrics.energy_per_served_load != null ? metrics.energy_per_served_load.toFixed(2) : '--') + '</span>';
            html += '<span>峰值温度 ' + (metrics.max_temp != null ? metrics.max_temp.toFixed(1) + '℃' : '--') + '</span>';
            html += '<span>服务率 ' + (metrics.service_rate != null ? (metrics.service_rate * 100).toFixed(1) + '%' : '--') + '</span>';
            html += '</div></div>';
        });
        html += '</div>';
        if (explanation.reasons && explanation.reasons.length) {
            html += '<ul class="ai-opt-reasons">' + explanation.reasons.slice(0, 4).map(function (line) {
                return '<li>' + escapeHtml(line) + '</li>';
            }).join('') + '</ul>';
        }
        if (job.status === 'success') {
            html += '<div class="ai-opt-actions"><button type="button" class="ai-opt-apply">应用推荐方案并运行</button>' +
                '<span class="ai-opt-note">候选 ' + result.evaluated + ' 组 · Pareto ' + result.pareto_count + ' 个</span></div>';
        }
        body.innerHTML = html;
        const apply = body.querySelector('.ai-opt-apply');
        if (apply) {
            apply.addEventListener('click', function () {
                applyOptimization(job.job_id, card);
            });
        }
        scrollCardIntoView(card);
    }

    function pollOptimization(jobId, card) {
        if (!jobId) return;
        const timer = setInterval(async function () {
            try {
                const response = await fetch(API_BASE + '/ai/optimize/' + jobId);
                const result = await response.json();
                if (!result.success) return;
                updateOptimizationCard(card, result.job);
                if (result.job.status === 'success' || result.job.status === 'failed') {
                    clearInterval(timer);
                }
            } catch (error) {
                 
            }
        }, 2000);
    }

    async function applyOptimization(jobId, card) {
        const button = card.querySelector('.ai-opt-apply');
        if (button) {
            button.disabled = true;
            button.textContent = '启动中…';
        }
        try {
            const response = await fetch(API_BASE + '/ai/optimize/' + jobId + '/apply', { method: 'POST' });
            const result = await response.json();
            if (!result.success) throw new Error(result.message || '应用失败');
            if (button) button.textContent = '已启动运行';
            const note = card.querySelector('.ai-opt-note');
            if (note) note.textContent = '运行编号：' + result.result.run_id + '，完成后页面数据会自动刷新。';
            if (typeof setFoot === 'function') setFoot('已按推荐方案启动运行，完成后页面会自动刷新。', 8000);
        } catch (error) {
            if (button) {
                button.disabled = false;
                button.textContent = '应用推荐方案并运行';
            }
            const note = card.querySelector('.ai-opt-note');
            if (note) note.textContent = '应用失败：' + error.message;
        }
    }

    function addMessage(role, text, meta, actions, run) {
        const item = document.createElement('div');
        item.className = 'ai-msg ' + (role === 'user' ? 'is-user' : 'is-bot');
        if (role !== 'user') item.appendChild(avatarNode('ai-avatar'));
        const body = document.createElement('div');
        body.className = 'ai-msg-body';
        const bubble = document.createElement('div');
        bubble.className = 'ai-bubble';
        bubble.innerHTML = renderMarkdown(text);
        body.appendChild(bubble);
        if (meta) {
            const tag = document.createElement('span');
            tag.className = 'ai-msg-meta';
            tag.textContent = meta;
            body.appendChild(tag);
        }
        if (run) {
            const card = renderRunCard(run);
            if (card) body.appendChild(card);
        }
        if (actions && actions.length) {
            const bar = document.createElement('div');
            bar.className = 'ai-msg-actions';
            actions.forEach(function (action) {
                const actionButton = document.createElement('button');
                actionButton.type = 'button';
                actionButton.textContent = action.label;
                actionButton.addEventListener('click', function () {
                    applySuggestedRun(action.run);
                });
                bar.appendChild(actionButton);
            });
            body.appendChild(bar);
        }
        item.appendChild(body);
        el.messages.appendChild(item);
        el.messages.scrollTop = el.messages.scrollHeight;
        return body;
    }

    function addTyping() {
        const item = document.createElement('div');
        item.className = 'ai-msg is-bot';
        item.id = 'aiTyping';
        item.appendChild(avatarNode('ai-avatar'));
        const body = document.createElement('div');
        body.className = 'ai-msg-body';
        const bubble = document.createElement('div');
        bubble.className = 'ai-bubble ai-typing';
        bubble.innerHTML = '<span></span><span></span><span></span>';
        body.appendChild(bubble);
        item.appendChild(body);
        el.messages.appendChild(item);
        el.messages.scrollTop = el.messages.scrollHeight;
        return body;
    }

    function removeTyping() {
        const node = document.getElementById('aiTyping');
        if (node) node.remove();
    }

    function setBusy(busy) {
        state.busy = busy;
        if (el.send) el.send.disabled = busy;
        if (el.input) el.input.disabled = busy;
    }

    async function refreshStatus() {
        try {
            const response = await fetch(API_BASE + '/ai/status');
            const result = await response.json();
            if (!result.success) return;
            const status = result.status || {};
            const online = !!status.available && !status.last_error;
            if (el.dot) el.dot.classList.toggle('is-online', online);
            if (el.engine) {
                el.engine.textContent = online ? (status.model || 'DeepSeek') : '本地规则';
                el.engine.title = status.reason || '';
            }
        } catch (error) {
            if (el.engine) el.engine.textContent = '本地规则';
        }
    }

    async function send(text) {
        const content = String(text || '').trim();
        if (!content || state.busy) return null;
        if (!state.open) openPanel();
        addMessage('user', content);
        state.history.push({ role: 'user', content: content });
        if (el.input) el.input.value = '';
        setBusy(true);
        addTyping();
        if (state.autoRun) setFoot('自动试算已开启，小助会先运行仿真再回答...', 12000);
        try {
            const response = await fetch(API_BASE + '/ai/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    messages: state.history.slice(-10),
                    auto_run: state.autoRun
                })
            });
            const result = await response.json();
            removeTyping();
            if (!result.success) throw new Error(result.message || '对话失败');
            const stamp = result.snapshot && result.snapshot.generated_at
                ? result.snapshot.generated_at.slice(5, 16)
                : '';
            const engine = result.source === 'deepseek'
                ? 'DeepSeek · ' + (result.model || '')
                : (result.source === 'optimizer' ? '方案寻优引擎' : '本地规则（大模型不可用）');
            const meta = engine + (stamp ? ' · 数据 ' + stamp : '');
            const actions = result.suggested_run
                ? [{ label: result.suggested_run.label || '按建议配置试算', run: result.suggested_run }]
                : [];
            const body = addMessage('assistant', result.reply, meta, actions, result.run);
            if (result.optimization && body) {
                const optCard = renderOptimizationCard(result.optimization);
                body.appendChild(optCard);
                scrollCardIntoView(optCard);
                if (result.optimization.status !== 'success' && result.optimization.status !== 'failed') {
                    pollOptimization(result.optimization.job_id, optCard);
                }
            }
            state.history.push({ role: 'assistant', content: result.reply });
            if (state.history.length > 20) state.history = state.history.slice(-20);
            return result.reply;
        } catch (error) {
            removeTyping();
            addMessage('assistant', '对话失败：' + error.message, '错误');
            return null;
        } finally {
            setBusy(false);
            if (el.input) el.input.focus();
        }
    }

    function clampPosition(left, top) {
        const margin = 8;
        const rect = el.root.getBoundingClientRect();
        const maxLeft = Math.max(margin, window.innerWidth - rect.width - margin);
        const maxTop = Math.max(margin, window.innerHeight - rect.height - margin);
        return {
            left: Math.min(Math.max(margin, left), maxLeft),
            top: Math.min(Math.max(margin, top), maxTop)
        };
    }

    function applyPosition(left, top) {
        const position = clampPosition(left, top);
        el.root.style.left = position.left + 'px';
        el.root.style.top = position.top + 'px';
        el.root.style.right = 'auto';
        el.root.style.bottom = 'auto';
        return position;
    }

    function restorePosition() {
        try {
            const saved = JSON.parse(localStorage.getItem(POSITION_KEY) || 'null');
            if (saved && typeof saved.left === 'number' && typeof saved.top === 'number') {
                applyPosition(saved.left, saved.top);
            }
        } catch (error) {
             
        }
    }

    function savePosition() {
        const rect = el.root.getBoundingClientRect();
        try {
            localStorage.setItem(POSITION_KEY, JSON.stringify({
                left: Math.round(rect.left),
                top: Math.round(rect.top)
            }));
        } catch (error) {
             
        }
    }

    function positionPanel() {
        if (!el.panel || el.panel.hidden) return;
        const rect = el.root.getBoundingClientRect();
        const panelWidth = el.panel.offsetWidth || 360;
        const panelHeight = el.panel.offsetHeight || 480;
        const margin = 12;

        const openRight = rect.left + panelWidth <= window.innerWidth - margin;
        el.panel.style.left = openRight ? '0' : 'auto';
        el.panel.style.right = openRight ? 'auto' : '0';

        const openAbove = rect.top >= panelHeight + 80;
        el.panel.style.bottom = openAbove ? '72px' : 'auto';
        el.panel.style.top = openAbove ? 'auto' : '72px';
    }

    function startDrag(event, handle) {
        if (event.button !== undefined && event.button !== 0) return;
        if (event.target.closest('button, input, label, a, select, textarea') && handle !== el.orb) return;
        const rect = el.root.getBoundingClientRect();
        state.dragState = {
            startX: event.clientX,
            startY: event.clientY,
            originLeft: rect.left,
            originTop: rect.top,
            moved: false,
            handle: handle,
            pointerId: event.pointerId
        };
        try { handle.setPointerCapture(event.pointerId); } catch (error) {}
        handle.classList.add('is-dragging');
        event.preventDefault();
    }

    function moveDrag(event) {
        const drag = state.dragState;
        if (!drag) return;
        const dx = event.clientX - drag.startX;
        const dy = event.clientY - drag.startY;
        if (!drag.moved && Math.sqrt(dx * dx + dy * dy) < 5) return;
        drag.moved = true;
        el.root.classList.add('is-dragging');
        applyPosition(drag.originLeft + dx, drag.originTop + dy);
    }

    function endDrag(event) {
        const drag = state.dragState;
        if (!drag) return;
        try { drag.handle.releasePointerCapture(drag.pointerId); } catch (error) {}
        drag.handle.classList.remove('is-dragging');
        el.root.classList.remove('is-dragging');
        state.dragState = null;
        if (drag.moved) {
            savePosition();
            positionPanel();
            state.suppressClickUntil = Date.now() + 300;
        }
    }

    function openPanel() {
        state.open = true;
        if (el.root) el.root.classList.add('is-open');
        if (el.panel) el.panel.hidden = false;
        positionPanel();
        if (el.messages && !el.messages.dataset.greeted) {
            el.messages.dataset.greeted = '1';
            addMessage('assistant', '你好，我是小助。说「你好」或「你好，yumo」唤醒我，也可以直接在这里提问。');
        }
        setTimeout(function () { if (el.input) el.input.focus(); }, 60);
    }

    function closePanel() {
        if (state.conversation) endConversation('');
        state.open = false;
        if (el.root) el.root.classList.remove('is-open');
        if (el.panel) el.panel.hidden = true;
    }

    function togglePanel() {
        if (state.open) closePanel(); else openPanel();
    }

    function speechSupported() {
        return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
    }

    function createRecognition() {
        const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!Ctor) return null;
        const rec = new Ctor();
        rec.lang = 'zh-CN';
        rec.interimResults = true;
        rec.continuous = false;
        return rec;
    }

    function startWakeWord() {
        if (!speechSupported()) {
            showMicFailure('当前浏览器不支持语音识别，请使用 Chrome 或 Edge');
            return;
        }
        if (state.conversation || state.listening || state.micChecking) return;
        if (state.ttsActive || (window.speechSynthesis && (window.speechSynthesis.speaking || window.speechSynthesis.pending))) {
            setTimeout(startWakeWord, 800);
            return;
        }
        if (state.wakeRec) {
            try { state.wakeRec.stop(); } catch (error) {}
            state.wakeRec = null;
        }
        state.wake = true;
        state.wakeActive = false;
        state.wakeBlocked = false;
        setFoot('正在启动语音唤醒…', 0);
        listenForWake();
        setTimeout(function () {
            if (state.wake && !state.wakeActive) {
                if (state.wakeRec) {
                    try { state.wakeRec.stop(); } catch (error) {}
                    state.wakeRec = null;
                }
                state.wake = false;
                state.wakeActive = false;
                state.wakeBlocked = true;
                setFoot('语音唤醒未启动，请点击页面任意位置重试', 8000);
            }
        }, 3000);
    }

    function listenForWake() {
        if (!state.wake) return;
        const rec = createRecognition();
        if (!rec) return;
        rec.continuous = true;
        rec.onstart = function () {
            state.wakeActive = true;
            setWakeListening(true);
            setFoot('语音唤醒已开启：说「你好，yumo」启动对话', 0);
        };
        rec.onresult = function (event) {
            state.wakeActive = true;
            setWakeListening(true);
            let text = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
                text += event.results[i][0].transcript;
            }
            const heard = String(text || '').trim();
            if (heard) {
                setFoot('听到：' + heard.slice(0, 24), 1800);
            }
            const hit = isWakeWord(text);
            if (hit) {
                state.wakeBlocked = false;
                stopWakeWord(false);
                beginVoiceConversation();
            }
        };
        rec.onerror = function (event) {
            const code = event && event.error;
            if (code === 'not-allowed' || code === 'service-not-allowed') {
                state.wake = false;
                state.wakeActive = false;
                state.wakeBlocked = true;
                setFoot('语音唤醒需要麦克风权限；点击页面或麦克风授权后可重试', 8000);
            }
        };
        rec.onend = function () {
            state.wakeActive = false;
            setWakeListening(false);
            if (state.wake) setTimeout(listenForWake, 500);
        };
        try {
            rec.start();
            state.wakeRec = rec;
        } catch (error) {
            state.wakeRec = null;
            state.wake = false;
            state.wakeActive = false;
            state.wakeBlocked = true;
            setFoot('语音唤醒启动失败；点击页面或麦克风授权后可重试', 7000);
        }
    }

    function stopWakeWord(updateUi) {
        state.wake = false;
        state.wakeActive = false;
        setWakeListening(false);
        if (state.wakeRec) {
            try { state.wakeRec.stop(); } catch (error) {}
            state.wakeRec = null;
        }
        if (updateUi !== false) {
            setFoot(FOOT_DEFAULT);
        }
    }

    function isConversationEnd(text) {
        const normalized = normalizeSpeech(text);
        return ['结束', '结束对话', '退出', '停止', '不用了', '就到这里'].some(function (word) {
            return normalized.indexOf(normalizeSpeech(word)) === 0;
        });
    }

    function plainTextForSpeech(text) {
        let result = String(text || '')
            .replace(/```[\s\S]*?```/g, ' ')
            .replace(/`([^`]+)`/g, '$1')
            .replace(/\*\*(.*?)\*\*/g, '$1')
            .replace(/\[(.*?)\]\(.*?\)/g, '$1')
            .replace(/^[\s>*#-]+/gm, '')
            .replace(/\s+/g, ' ')
            .trim();
        if (result.length > 320) {
            result = result.slice(0, 320) + '。详细内容请查看对话框。';
        }
        return result;
    }

    function pickChineseFemaleVoice() {
        if (!window.speechSynthesis) return null;
        const voices = window.speechSynthesis.getVoices() || [];
        const chinese = voices.filter(function (voice) {
            return /^zh([-_]CN)?$/i.test(voice.lang || '')
                || /chinese|中文|普通话/i.test(voice.name || '');
        });
        if (!chinese.length) return null;
        const naturalFemale = chinese.find(function (voice) {
            return /natural/i.test(voice.name || '')
                && /xiaoxiao|xiaoyi|xiaomeng|xiaohan|xiaomo|xiaoxuan|xiaoyan/i.test(voice.name || '');
        });
        if (naturalFemale) return naturalFemale;
        const hints = ['Xiaoxiao', 'Xiaoyi', 'Yaoyao', 'Huihui', 'Xiaomeng', 'Xiaohan', 'Xiaomo', 'Xiaoxuan', 'Xiaoyan', 'Female', '女'];
        for (let i = 0; i < hints.length; i++) {
            const hint = hints[i].toLowerCase();
            const found = chinese.find(function (voice) {
                return (voice.name || '').toLowerCase().indexOf(hint) >= 0;
            });
            if (found) return found;
        }
        const google = chinese.find(function (voice) {
            return /google/i.test(voice.name || '');
        });
        return google || chinese[0] || null;
    }

    function speak(text, done) {
        const speech = plainTextForSpeech(text);
        if (!speech || !window.speechSynthesis || !window.SpeechSynthesisUtterance) {
            state.ttsActive = false;
            setSpeaking(false);
            if (done) setTimeout(done, 300);
            return;
        }
        try {
            window.speechSynthesis.cancel();
            state.ttsActive = true;
            setSpeaking(true);
            if (!preferredVoice) preferredVoice = pickChineseFemaleVoice();
            const utterance = new SpeechSynthesisUtterance(speech);
            if (preferredVoice) utterance.voice = preferredVoice;
            utterance.lang = preferredVoice ? (preferredVoice.lang || 'zh-CN') : 'zh-CN';
            utterance.rate = 1.1;
            utterance.pitch = 1.03;
            utterance.volume = 1.0;
            console.log('[TTS]', preferredVoice ? preferredVoice.name : 'default', 'rate=1.1');
            let finished = false;
            let started = false;
            let startTimer = null;
            const finish = function () {
                if (finished) return;
                finished = true;
                if (startTimer) clearTimeout(startTimer);
                state.ttsActive = false;
                setSpeaking(false);
                if (done) done();
            };
            utterance.onstart = function () {
                started = true;
                if (startTimer) {
                    clearTimeout(startTimer);
                    startTimer = null;
                }
            };
            utterance.onend = finish;
            utterance.onerror = finish;
            window.speechSynthesis.speak(utterance);
            startTimer = setTimeout(function () {
                if (!started && !window.speechSynthesis.speaking) finish();
            }, 2500);
            setTimeout(function () {
                if (window.speechSynthesis.speaking) {
                    try { window.speechSynthesis.cancel(); } catch (error) {}
                }
                finish();
            }, 180000);
        } catch (error) {
            state.ttsActive = false;
            setSpeaking(false);
            if (done) setTimeout(done, 300);
        }
    }

    function endConversation(message) {
        state.conversation = false;
        setListening(false);
        setSpeaking(false);
        if (state.conversationRec) {
            try { state.conversationRec.stop(); } catch (error) {}
            state.conversationRec = null;
        }
        if (window.speechSynthesis) {
            try { window.speechSynthesis.cancel(); } catch (error) {}
        }
        if (message) {
            addMessage('assistant', message, '语音对话');
            speak(message, function () {
                setFoot(FOOT_DEFAULT);
                startWakeWord();
            });
        } else {
            setFoot(FOOT_DEFAULT);
            startWakeWord();
        }
    }

    async function handleConversationText(text) {
        if (!state.conversation) return;
        setFoot('正在思考…', 0);
        const reply = await send(text);
        if (!state.conversation) return;
        if (!reply) {
            speak('对话失败，请稍后再试。', function () {
                startConversationTurn();
            });
            return;
        }
        speak(reply, function () {
            startConversationTurn();
        });
    }

    function startConversationTurn() {
        if (!state.conversation) return;
        if (state.ttsActive || (window.speechSynthesis && (window.speechSynthesis.speaking || window.speechSynthesis.pending))) {
            setTimeout(function () {
                if (state.conversation) startConversationTurn();
            }, 600);
            return;
        }
        if (state.conversationRec) {
            try { state.conversationRec.stop(); } catch (error) {}
            state.conversationRec = null;
        }
        if (!speechSupported()) {
            endConversation('当前浏览器不支持语音识别，请使用 Chrome 或 Edge。');
            return;
        }
        const rec = createRecognition();
        if (!rec) {
            endConversation('当前浏览器不支持语音识别，请使用 Chrome 或 Edge。');
            return;
        }
        rec.continuous = false;
        rec.interimResults = true;
        state.conversationRec = rec;
        let finalText = '';
        setListening(true);
        setFoot('我在听，请说你的需求；说「结束」可以退出', 0);
        rec.onresult = function (event) {
            let interim = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript;
                if (event.results[i].isFinal) finalText += transcript;
                else interim += transcript;
            }
            if (el.input) el.input.value = (finalText + interim).trim();
        };
        rec.onerror = function (event) {
            const code = event && event.error;
            setListening(false);
            if (code === 'not-allowed' || code === 'service-not-allowed') {
                state.conversationRec = null;
                endConversation('语音对话需要麦克风和语音识别权限，请检查浏览器权限。');
            } else if (code === 'network') {
                state.conversationRec = null;
                endConversation('语音识别服务需要联网，请检查网络。');
            }
        };
        rec.onend = function () {
            state.conversationRec = null;
            setListening(false);
            if (!state.conversation) return;
            const text = (finalText || (el.input ? el.input.value : '')).trim();
            if (!text) {
                setTimeout(function () {
                    if (state.conversation) startConversationTurn();
                }, 600);
                return;
            }
            if (el.input) el.input.value = '';
            if (isConversationEnd(text)) {
                endConversation('好的，语音对话已结束。');
                return;
            }
            handleConversationText(text);
        };
        try {
            rec.start();
        } catch (error) {
            state.conversationRec = null;
            setListening(false);
            setTimeout(function () {
                if (state.conversation) startConversationTurn();
            }, 800);
        }
    }

    function beginVoiceConversation() {
        if (state.conversation) return;
        state.conversation = true;
        stopWakeWord(false);
        openPanel();
        playBeep();
        flashOrb();
        if (typeof window.showToast === 'function') {
            window.showToast('听到「你好，yumo」了，语音对话已开始', 'success');
        }
        const prompt = '你好，我在。请说你的需求。说「结束」可以退出语音对话。';
        addMessage('assistant', prompt, '语音对话');
        setFoot('语音对话已开始：请说你的需求，说「结束」退出', 0);
        setTimeout(function () {
            startConversationTurn();
        }, 500);
    }

    function resetDictationState() {
        state.listening = false;
        setListening(false);
        state.dictationRec = null;
        state.dictationMode = null;
        state.dictationStopRequested = false;
        state.dictationBaseText = '';
        state.dictationFinalText = '';
        state.dictationError = '';
        state.micGranted = false;
        state.micError = '';
        state.micResultSeen = false;
        if (state.dictationRestartTimer) {
            clearTimeout(state.dictationRestartTimer);
            state.dictationRestartTimer = null;
        }
        if (el.mic) el.mic.classList.remove('is-active');
    }

    function finishDictation() {
        const wasManual = state.dictationMode === 'manual';
        const errorText = state.dictationError;
        const micError = state.micError;
        const text = (state.dictationBaseText + ' ' + state.dictationFinalText).trim();
        if (el.input) el.input.value = text;
        resetDictationState();
        if (errorText) {
            showMicFailure(errorText);
            return;
        }
        if (wasManual) {
            if (text) {
                setFoot('已转成文字，可编辑后发送', 6000);
                if (el.input) el.input.focus();
            } else if (micError) {
                showMicFailure(micError);
            } else {
                setFoot('没有识别到内容，请再试一次', 5000);
            }
            setTimeout(function () {
                if (!state.conversation && !state.listening && !state.micChecking) {
                    startWakeWord();
                }
            }, 1800);
        } else {
            setFoot(FOOT_DEFAULT);
            if (text) send(text);
            else if (micError) showMicFailure(micError);
        }
        if (state.resumeWake) {
            state.resumeWake = false;
            startWakeWord();
        }
    }

    function stopDictation() {
        if (!state.listening) return;
        state.dictationStopRequested = true;
        setFoot('正在把刚才的话转成文字…', 6000);
        if (state.dictationRestartTimer) {
            clearTimeout(state.dictationRestartTimer);
            state.dictationRestartTimer = null;
        }
        if (state.dictationRec) {
            try { state.dictationRec.stop(); } catch (error) { finishDictation(); }
        } else {
            finishDictation();
        }
    }

    function beginDictationRecognition() {
        if (!state.listening || state.dictationStopRequested) return;
        const manual = state.dictationMode === 'manual';
        const rec = createRecognition();
        if (!rec) {
            showMicFailure('当前浏览器不支持语音识别，请使用 Chrome 或 Edge');
            resetDictationState();
            return;
        }
        rec.continuous = manual;
        state.dictationRec = rec;
        rec.onresult = function (event) {
            if (!state.micResultSeen) {
                state.micResultSeen = true;
                state.micGranted = true;
                state.micError = '';
                setFoot(manual
                    ? '麦克风已打开，请继续说话；说完再点麦克风停止'
                    : '麦克风已打开，正在听…', 0);
            }
            let interim = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
                const transcript = event.results[i][0].transcript;
                if (event.results[i].isFinal) state.dictationFinalText += transcript;
                else interim += transcript;
            }
            if (el.input) {
                el.input.value = (state.dictationBaseText + ' ' + state.dictationFinalText + interim).trim();
            }
        };
        rec.onerror = function (event) {
            const code = event && event.error;
            if (code === 'not-allowed' || code === 'service-not-allowed') {
                state.dictationStopRequested = true;
                state.dictationError = state.micGranted
                    ? '麦克风已经打开，但浏览器的语音识别服务不可用。请检查网络连接，或改用 Chrome / Edge 并允许语音识别服务'
                    : (state.micError || '浏览器拒绝了语音识别，请允许麦克风和语音识别权限');
            } else if (code === 'audio-capture') {
                state.dictationStopRequested = true;
                state.dictationError = state.micGranted
                    ? '麦克风已经打开，但语音识别没有拿到音频输入，请检查系统麦克风设置'
                    : (state.micError || '没有检测到麦克风，请检查设备');
            } else if (code === 'network') {
                state.dictationStopRequested = true;
                state.dictationError = '语音识别服务需要联网，请检查网络连接后重试';
            } else if (code === 'language-not-supported') {
                state.dictationStopRequested = true;
                state.dictationError = '当前浏览器的语音识别不支持中文，请使用 Chrome 或 Edge';
            }
        };
        rec.onend = function () {
            state.dictationRec = null;
            if (!state.listening) return;
            if (manual && !state.dictationStopRequested) {
                setFoot('还在听，请继续说话；说完再点麦克风停止', 4000);
                state.dictationRestartTimer = setTimeout(function () {
                    state.dictationRestartTimer = null;
                    beginDictationRecognition();
                }, 600);
                return;
            }
            finishDictation();
        };
        try {
            rec.start();
        } catch (error) {
            const hadMic = state.micGranted;
            resetDictationState();
            setFoot(hadMic
                ? '麦克风已经打开，但语音识别启动失败。请检查网络，或改用 Chrome / Edge 后重试'
                : '语音识别启动失败，请检查麦克风权限', 7000);
        }
    }

    function showMicFailure(message) {
        setFoot(message, 15000);
        if (typeof window.showToast === 'function') {
            window.showToast(message, 'danger');
        }
    }

    async function ensureMicrophonePermission() {
        const origin = location.origin;
        const userAgent = navigator.userAgent || '';
        const browserLabel = /Edg\//.test(userAgent)
            ? 'Edge'
            : (/Chrome\//.test(userAgent) ? 'Chrome 或内置浏览器' : '其他浏览器');
        if (!window.isSecureContext) {
            return {
                ok: false,
                message: '当前页面不是安全上下文（' + origin + '）。请使用 http://127.0.0.1:5000 或 http://localhost:5000 打开，或使用 HTTPS'
            };
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            return { ok: false, message: '当前浏览器不支持麦克风访问，请使用 Chrome 或 Edge' };
        }
        let permissionState = 'unknown';
        if (navigator.permissions && navigator.permissions.query) {
            try {
                const status = await navigator.permissions.query({ name: 'microphone' });
                permissionState = status.state;
            } catch (error) {}
        }
        let audioInputs = 0;
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            audioInputs = devices.filter(function (device) {
                return device.kind === 'audioinput';
            }).length;
        } catch (error) {}
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            stream.getTracks().forEach(function (track) { track.stop(); });
            state.micGranted = true;
            return { ok: true, message: '麦克风已打开' };
        } catch (error) {
            const name = error && error.name ? error.name : 'UnknownError';
            const detail = error && error.message ? error.message : '';
            console.warn('[麦克风诊断]', {
                origin: origin,
                secureContext: window.isSecureContext,
                permissionState: permissionState,
                audioInputs: audioInputs,
                errorName: name,
                errorMessage: detail,
                userAgent: navigator.userAgent
            });
            if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
                if (permissionState === 'granted') {
                    return {
                        ok: false,
                        message: '浏览器站点权限已经允许，但系统仍然拒绝麦克风（' + name +
                            '）。请检查 Windows 设置 → 隐私和安全性 → 麦克风 → 允许桌面应用访问麦克风，并确认麦克风没有静音。当前页面：' + origin
                    };
                }
                if (permissionState === 'denied') {
                    return {
                        ok: false,
                        message: '浏览器当前返回的麦克风状态是“已拒绝”（页面：' + origin +
                            '，浏览器：' + browserLabel + '，检测到 ' + audioInputs + ' 个音频输入设备）。如果你已经在站点设置里允许，请确认：' +
                            '1）当前页面是否在同一个 Edge 窗口/配置文件；2）Edge 的麦克风总开关和阻止列表；' +
                            '3）Windows 隐私设置里的桌面应用麦克风权限。如果当前页面在 Codex 内置浏览器中，' +
                            'Edge 的站点设置不会生效，请改用 Edge 或 Chrome 打开'
                    };
                }
                return {
                    ok: false,
                    message: '麦克风请求被拒绝（' + name + '，权限状态：' + permissionState +
                        '，检测到 ' + audioInputs + ' 个音频输入设备）。当前页面：' + origin +
                        '。请检查站点权限和 Windows 麦克风权限'
                };
            }
            if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
                return {
                    ok: false,
                    message: '没有检测到可用的麦克风（检测到 ' + audioInputs + ' 个音频输入设备），请检查设备连接'
                };
            }
            if (name === 'NotReadableError' || name === 'TrackStartError') {
                return {
                    ok: false,
                    message: '系统检测到麦克风，但无法读取（' + name + '）。请关闭占用麦克风的程序，或检查 Windows 麦克风隐私设置'
                };
            }
            return {
                ok: false,
                message: '麦克风打开失败（' + name + '）：' + (detail || '未知错误') + '。当前页面：' + origin
            };
        }
    }

    async function startDictation(mode) {
        if (!speechSupported()) {
            showMicFailure('当前浏览器不支持语音识别，请使用 Chrome 或 Edge');
            return;
        }
        if (state.listening || state.micChecking) return;
        const manual = mode === 'manual';
        if (manual) stopWakeWord(false);
        state.micChecking = true;
        if (el.mic) el.mic.classList.add('is-checking');
        setFoot('正在请求麦克风权限…', 0);
        const permission = await ensureMicrophonePermission();
        state.micChecking = false;
        if (el.mic) el.mic.classList.remove('is-checking');
        state.listening = true;
        state.dictationMode = manual ? 'manual' : 'auto';
        state.dictationStopRequested = false;
        state.dictationBaseText = el.input ? el.input.value.trim() : '';
        state.dictationFinalText = '';
        state.dictationError = '';
        state.micGranted = permission.ok;
        state.micError = permission.ok ? '' : permission.message;
        state.micResultSeen = false;
        setListening(true);
        if (el.mic) el.mic.classList.add('is-active');
        if (el.foot) {
            el.foot.textContent = permission.ok
                ? (manual
                    ? '麦克风已打开，请说话；说完再点一次麦克风停止'
                    : '麦克风已打开，正在听…说完会自动转成文字')
                : '浏览器没有确认麦克风权限，正在尝试语音识别…';
        }
        beginDictationRecognition();
    }
    function updateRunCard(detail, fallbackStatus) {
        if (!detail || !detail.run_id) return;
        const status = detail.status || fallbackStatus;
        document.querySelectorAll('.ai-run-card').forEach(function (card) {
            if (card.dataset.runId !== detail.run_id) return;
            card.className = 'ai-run-card is-' + status;
            const title = card.querySelector('strong');
            if (title) title.textContent = status === 'failed' ? '运行失败' : '运行完成';
        });
    }

    function init() {
        cache();
        if (!el.root) return;

        if (el.autoRun) {
            state.autoRun = localStorage.getItem('aiAssistantAutoRun') === '1';
            el.autoRun.checked = state.autoRun;
            el.autoRun.addEventListener('change', function () {
                state.autoRun = el.autoRun.checked;
                localStorage.setItem('aiAssistantAutoRun', state.autoRun ? '1' : '0');
                setFoot(state.autoRun
                    ? '自动试算已开启：配置类问题会先运行仿真再回答'
                    : '自动试算已关闭：配置类问题会先给建议，再让你点按钮试算', 5000);
            });
        }

        window.addEventListener('dcs:run-completed', function (event) {
            const detail = event.detail || {};
            updateRunCard(detail, 'success');
            const pending = state.pendingTrial;
            if (!pending || detail.origin === 'ai') return;
            state.pendingTrial = null;
            const question = pending.question ? '我原来的问题是：' + pending.question + '。' : '';
            setTimeout(function () {
                send('试算已完成（运行编号：' + (detail.run_id || '') + '）。' + question +
                     '请基于这次运行结果做数据分析，给出关键指标、最佳算法，并和上一轮做对比。');
            }, 900);
        });
        window.addEventListener('dcs:run-failed', function (event) {
            const detail = event.detail || {};
            updateRunCard(detail, 'failed');
            if (!state.pendingTrial) return;
            state.pendingTrial = null;
            addMessage('assistant', '试算失败：' + (detail.error || '未知错误') + '。可以调整参数后重试。', '运行失败');
        });

        el.orb.addEventListener('pointerdown', function (event) { startDrag(event, el.orb); });
        el.orb.addEventListener('pointermove', moveDrag);
        el.orb.addEventListener('pointerup', endDrag);
        el.orb.addEventListener('pointercancel', endDrag);

        const panelHead = document.querySelector('.ai-panel-head');
        if (panelHead) {
            panelHead.addEventListener('pointerdown', function (event) { startDrag(event, panelHead); });
            panelHead.addEventListener('pointermove', moveDrag);
            panelHead.addEventListener('pointerup', endDrag);
            panelHead.addEventListener('pointercancel', endDrag);
        }

        window.addEventListener('dcs:simulation-updated', function (event) {
            const detail = event.detail || {};
            if (detail.origin !== 'ai') {
                state.history = [];
            }
            setFoot('运行数据已更新，小助已切换到最新结果', 5000);
            if (typeof window.showToast === 'function') {
                window.showToast('运行数据已更新，可以继续向小助提问', 'success');
            }
        });

        window.addEventListener('resize', function () {
            if (el.root.style.left) {
                const rect = el.root.getBoundingClientRect();
                applyPosition(rect.left, rect.top);
            }
            positionPanel();
        });

        el.orb.addEventListener('click', function (event) {
            if (Date.now() < state.suppressClickUntil) {
                event.preventDefault();
                return;
            }
            togglePanel();
        });
        if (el.close) el.close.addEventListener('click', closePanel);
        if (el.form) {
            el.form.addEventListener('submit', function (event) {
                event.preventDefault();
                send(el.input.value);
            });
        }
        if (el.mic) {
            el.mic.addEventListener('click', function () {
                if (state.micChecking) return;
                if (state.conversation) {
                    endConversation('已退出语音对话，请再点一次麦克风开始手动录音。');
                    return;
                }
                if (state.listening) stopDictation();
                else startDictation('manual');
            });
        }
        if (el.quick) {
            el.quick.addEventListener('click', function (event) {
                const button = event.target.closest('button[data-prompt]');
                if (button) send(button.dataset.prompt);
            });
        }

        restorePosition();
        refreshStatus();
        if (window.speechSynthesis) {
            const refreshVoice = function () {
                preferredVoice = pickChineseFemaleVoice();
            };
            refreshVoice();
            if (window.speechSynthesis.addEventListener) {
                window.speechSynthesis.addEventListener('voiceschanged', refreshVoice);
            } else {
                window.speechSynthesis.onvoiceschanged = refreshVoice;
            }
        }
        setInterval(refreshStatus, 30000);
        setTimeout(function () {
            if (!state.conversation && !state.listening && !state.micChecking) startWakeWord();
        }, 1200);
        document.addEventListener('click', function () {
            if (state.conversation || state.listening || state.micChecking) return;
            if (state.wake && state.wakeActive) return;
            if (Date.now() - state.wakeRetryAt < 2500) return;
            state.wakeRetryAt = Date.now();
            startWakeWord();
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();









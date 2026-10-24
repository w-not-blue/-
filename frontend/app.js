const API_BASE = '/api/v1';

const THEME = {
    text: '#4a6076',
    grid: '#c2d9eb',
    series: {
        greedy:      { line: '#5794f2', fill: 'rgba(87,148,242,0.10)' },
        genetic:     { line: '#b877d9', fill: 'rgba(184,119,217,0.10)' },
        round_robin: { line: '#73bf69', fill: 'rgba(115,191,105,0.10)' },
        damo_ets:    { line: '#ff9830', fill: 'rgba(255,152,48,0.10)' },
        nsga2:       { line: '#e24d42', fill: 'rgba(226,77,66,0.10)' },
        moead:       { line: '#eab839', fill: 'rgba(234,184,57,0.10)' }
    },
    fallback: { line: '#8e8e99', fill: 'rgba(142,142,153,0.10)' }
};

function seriesColor(algo) {
    return THEME.series[algo] || THEME.fallback;
}

function axisOptions(extra) {
    const base = {
        grid: { color: THEME.grid, drawTicks: false },
        border: { display: false },
        ticks: { color: THEME.text, padding: 8, maxRotation: 0, font: { size: 11 } }
    };
    return Object.assign(base, extra || {});
}

function baseLineOptions(yExtra) {
    return {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { position: 'top', align: 'end' } },
        elements: { line: { borderWidth: 2, tension: 0.35 } },
        scales: { x: axisOptions(), y: axisOptions(yExtra) }
    };
}

function applyChartDefaults() {
    if (!window.Chart) return;
    Chart.defaults.color = THEME.text;
    Chart.defaults.font.family = '-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif';
    Chart.defaults.font.size = 11;
    Chart.defaults.borderColor = THEME.grid;
    Chart.defaults.animation.duration = 300;
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.boxWidth = 8;
    Chart.defaults.plugins.legend.labels.boxHeight = 8;
    Chart.defaults.plugins.legend.labels.padding = 14;
    Chart.defaults.plugins.legend.labels.color = THEME.text;
    Chart.defaults.plugins.tooltip.backgroundColor = '#ffffff';
    Chart.defaults.plugins.tooltip.borderColor = '#a5c3db';
    Chart.defaults.plugins.tooltip.borderWidth = 1;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.cornerRadius = 4;
    Chart.defaults.plugins.tooltip.titleColor = '#1b2a3a';
    Chart.defaults.plugins.tooltip.bodyColor = '#4a6076';
    Chart.defaults.plugins.tooltip.boxPadding = 4;
}

let energyChart, tempChart, loadChart, pueChart, radarChart;
let radarData = [];
let radarSelectedAlgo = null;
let currentPluginConfig = {};

document.addEventListener('DOMContentLoaded', function() {
    initCharts();
    loadMetrics();
    loadPlugins();
    
    document.getElementById('simulationForm').addEventListener('submit', runSimulation);
    document.getElementById('saveConfigBtn').addEventListener('click', savePluginConfig);
    const radarTabs = document.getElementById('algorithmScoreTabs');
    if (radarTabs) {
        radarTabs.addEventListener('click', function (event) {
            const button = event.target.closest('[data-algorithm]');
            if (!button) return;
            radarSelectedAlgo = button.getAttribute('data-algorithm');
            renderSelectedRadar();
        });
    }

    document.getElementById('aiAnalyzeBtn').addEventListener('click', runAiAnalyze);
    document.getElementById('aiParseBtn').addEventListener('click', parseAiIntent);
    document.getElementById('aiPrompt').addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            parseAiIntent();
        }
    });
    loadAiStatus();
    document.getElementById('tuneBtn').addEventListener('click', runTune);
    document.getElementById('applyTuneBtn').addEventListener('click', applyTunedWeights);
    document.querySelectorAll('.opt-tab').forEach(function(tab) {
        tab.addEventListener('click', function() { setOptTab(tab.dataset.optTab); });
    });
    const optStartBtn = document.getElementById('optStartBtn');
    if (optStartBtn) optStartBtn.addEventListener('click', startGlobalOptimize);
    
    document.querySelectorAll('.sidebar-nav .nav-item').forEach(item => {
        item.addEventListener('click', function() {
            document.querySelectorAll('.sidebar-nav .nav-item').forEach(i => i.classList.remove('active'));
            this.classList.add('active');
            document.body.classList.remove('sidebar-open');
        });
    });

    document.getElementById('sidebarToggle').addEventListener('click', function() {
        document.body.classList.toggle('sidebar-open');
    });

    const cityInput = document.getElementById('citySearch');
    if (cityInput) {
        let cityTimer = null;
        cityInput.addEventListener('input', function() {
            clearTimeout(cityTimer);
            cityTimer = setTimeout(function() { renderCityPanel(cityInput.value); }, 80);
        });
        cityInput.addEventListener('focus', function() { renderCityPanel(cityInput.value); });
        cityInput.addEventListener('keydown', onCityKeydown);
    }

    const schoolInput = document.getElementById('schoolSearch');
    if (schoolInput) {
        let schoolTimer = null;
        schoolInput.addEventListener('input', function() {
            clearTimeout(schoolTimer);
            selectedSchool = null;
            const value = schoolInput.value.trim();
            const city = selectedCity ? selectedCity.city : '';
            if (!value) { searchSchools('', city, true); return; }
            schoolTimer = setTimeout(function() { searchSchools(value, city, true); }, 180);
        });
        schoolInput.addEventListener('keydown', onSchoolKeydown);
        schoolInput.addEventListener('focus', function() {
            if (schoolItems.length) {
                renderSchoolPanel(schoolTotal, selectedCity ? selectedCity.city : '');
            }
        });
        document.addEventListener('click', function(event) {
            if (!event.target.closest('.combo-wrap')) {
                hideSchoolPanel();
                hideCityPanel();
            }
        });
    }
    loadCities();

    const csvButton = document.getElementById('exportCsvBtn');
    if (csvButton) csvButton.addEventListener('click', exportResultsCsv);

    const pngButton = document.getElementById('exportPngBtn');
    if (pngButton) pngButton.addEventListener('click', exportChartImages);

    setInterval(checkHealth, 5000);
    setInterval(loadPlugins, 30000);
    setInterval(loadMetrics, 15000);
    connectRunEvents();
});

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(function() { URL.revokeObjectURL(url); }, 1500);
}

async function exportResultsCsv() {
    try {
        const response = await fetch(`${API_BASE}/data`);
        const result = await response.json();
        if (!result.success || !result.data || !result.data.length) {
            showToast('暂无可导出的数据', 'warning');
            return;
        }
        const rows = result.data;
        const headers = Object.keys(rows[0]);
        const cell = function(value) {
            const text = value === null || value === undefined ? '' : String(value);
            return /[",\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
        };
        const lines = [headers.join(',')];
        rows.forEach(function(row) {
            lines.push(headers.map(function(h) { return cell(row[h]); }).join(','));
        });
        downloadBlob(new Blob(['\ufeff' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' }),
            'simulation_results.csv');
        showToast('已导出 ' + rows.length + ' 行 CSV', 'success');
    } catch (error) {
        showToast('导出失败：' + error.message, 'danger');
    }
}

function exportChartImages() {
    const canvases = Array.from(document.querySelectorAll('#charts canvas'));
    if (!canvases.length) {
        showToast('暂无可导出的图表', 'warning');
        return;
    }
    canvases.forEach(function(canvas, index) {
        const name = (canvas.id || ('chart' + index)) + '.png';
        const link = document.createElement('a');
        link.href = canvas.toDataURL('image/png');
        link.download = name;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    });
    showToast('已导出 ' + canvases.length + ' 张图表', 'success');
}

function initCharts() {
    if (!window.Chart) {
        console.error('Chart.js 未加载, 图表不可用');
        return;
    }
    applyChartDefaults();

    energyChart = new Chart(document.getElementById('energyChart').getContext('2d'), {
        type: 'line', data: { labels: [], datasets: [] }, options: baseLineOptions({ beginAtZero: true })
    });
    tempChart = new Chart(document.getElementById('tempChart').getContext('2d'), {
        type: 'line', data: { labels: [], datasets: [] }, options: baseLineOptions({ suggestedMin: 18, suggestedMax: 60 })
    });
    loadChart = new Chart(document.getElementById('loadChart').getContext('2d'), {
        type: 'line', data: { labels: [], datasets: [] }, options: baseLineOptions({ beginAtZero: true })
    });
    pueChart = new Chart(document.getElementById('pueChart').getContext('2d'), {
        type: 'line', data: { labels: [], datasets: [] }, options: baseLineOptions({ suggestedMin: 1.0, suggestedMax: 2.2 })
    });
    radarChart = new Chart(document.getElementById('radarChart').getContext('2d'), {
        type: 'radar',
        data: { labels: [], datasets: [] },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    align: 'end',
                    labels: { usePointStyle: true, boxWidth: 8, padding: 14, font: { size: 11 } }
                },
                tooltip: {
                    callbacks: {
                        label: (context) => {
                            const raw = context.dataset.rawMetrics || {};
                            const metric = String(context.label).split('\n')[0];
                            let detail = '';
                            if (metric === '能耗效率') detail = `单位负载能耗 ${Number(raw.energy || 0).toFixed(2)}`;
                            if (metric === '温度控制') detail = `峰值温度 ${Number(raw.temperature || 0).toFixed(1)} ℃`;
                            if (metric === 'PUE 指标') detail = `平均 PUE ${Number(raw.pue || 0).toFixed(3)}`;
                            if (metric === '任务服务率') detail = `任务服务率 ${(Number(raw.serviceRate || 0) * 100).toFixed(1)}%`;
                            return [`${context.dataset.label}：${Number(context.parsed.r).toFixed(1)} 分`, detail];
                        }
                    }
                }
            },
            scales: {
                r: {
                    beginAtZero: true, min: 0, max: 100,
                    angleLines: { color: THEME.grid },
                    grid: { color: THEME.grid },
                    pointLabels: { color: THEME.text, font: { size: 11.5 }, padding: 8 },
                    ticks: { display: true, stepSize: 25, color: THEME.text, backdropColor: 'transparent', font: { size: 10 } }
                }
            }
        }
    });
}


async function loadMetrics() {
    try {
        const response = await fetch(`${API_BASE}/metrics`);
        const result = await response.json();

        if (result.success && result.data) {
            updateDashboard(result.data);
            renderMetricsTable(result.data, result.ranking);
            renderRanking(result.ranking);
            renderRuntimeNote(result.data_source, result.controlled, result.ranking);
            loadDataAndCharts();
        }
    } catch (error) {
        console.error('Failed to load metrics:', error);
    }
}

function numberOrDash(value, digits, suffix) {
    if (value === null || value === undefined || !isFinite(value)) return '--';
    return Number(value).toFixed(digits) + (suffix || '');
}

let schoolIndex = {};
let schoolItems = [];
let schoolTotal = 0;
let schoolActive = -1;
let selectedSchool = null;

let cityList = [];
let cityEntries = [];
let cityActive = -1;
let selectedCity = null;

function cityMatches(item, query) {
    return item.city.indexOf(query) >= 0
        || (item.province && item.province.indexOf(query) >= 0);
}

async function loadCities() {
    try {
        const response = await fetch(`${API_BASE}/cities`);
        const result = await response.json();
        if (!result.success) return;
        cityList = result.data || [];
        const beijing = cityList.find(function(item) { return item.city === '北京市'; });
        if (beijing) {
            selectedCity = beijing;
            const input = document.getElementById('citySearch');
            if (input) input.value = beijing.city;
            const hint = document.getElementById('cityHint');
            if (hint) hint.textContent = beijing.province + ' · ' + beijing.school_count + ' 所学校';
        }
        onCityChanged(false);
    } catch (error) {
        console.error('City list failed:', error);
    }
}

function renderCityPanel(query) {
    const panel = document.getElementById('cityCombo');
    const input = document.getElementById('citySearch');
    if (!panel) return;
    const q = (query || '').trim();
    const matched = q
        ? cityList.filter(function(item) { return cityMatches(item, q); })
        : cityList.slice();
    const shown = matched.slice(0, 60);
    cityEntries = [null].concat(shown);

    panel.innerHTML = '';
    cityActive = -1;

    const allItem = document.createElement('div');
    allItem.className = 'combo-item';
    allItem.setAttribute('role', 'option');
    const allName = document.createElement('span');
    allName.className = 'name';
    allName.textContent = '全部城市（不限）';
    allItem.appendChild(allName);
    allItem.addEventListener('mousedown', function(event) {
        event.preventDefault();
        selectCity(null);
    });
    panel.appendChild(allItem);

    shown.forEach(function(item) {
        const el = document.createElement('div');
        el.className = 'combo-item';
        el.setAttribute('role', 'option');
        const name = document.createElement('span');
        name.className = 'name';
        name.textContent = item.city;
        const meta = document.createElement('span');
        meta.className = 'meta';
        meta.textContent = item.province + ' · ' + item.school_count + ' 所';
        el.appendChild(name);
        el.appendChild(meta);
        el.addEventListener('mousedown', function(event) {
            event.preventDefault();
            selectCity(item);
        });
        panel.appendChild(el);
    });

    if (matched.length > shown.length) {
        const more = document.createElement('div');
        more.className = 'combo-more';
        more.textContent = '共 ' + matched.length + ' 个城市，已显示前 ' + shown.length + ' 个，继续输入可缩小范围';
        panel.appendChild(more);
    }

    panel.hidden = false;
    if (input) input.setAttribute('aria-expanded', 'true');
}

function hideCityPanel() {
    const panel = document.getElementById('cityCombo');
    const input = document.getElementById('citySearch');
    if (panel) panel.hidden = true;
    if (input) input.setAttribute('aria-expanded', 'false');
    cityActive = -1;
}

function highlightCity() {
    const panel = document.getElementById('cityCombo');
    if (!panel) return;
    const items = panel.querySelectorAll('.combo-item');
    items.forEach(function(item, index) {
        item.classList.toggle('active', index === cityActive);
        if (index === cityActive) item.scrollIntoView({ block: 'nearest' });
    });
}

function selectCity(item, openSchools) {
    selectedCity = item;
    const input = document.getElementById('citySearch');
    if (input) input.value = item ? item.city : '';
    const hint = document.getElementById('cityHint');
    if (hint) {
        hint.textContent = item
            ? item.province + ' · ' + item.school_count + ' 所学校'
            : '全部城市，不限定地点';
    }
    hideCityPanel();
    onCityChanged(openSchools !== false);
}

window.applySimulationSchool = async function (schoolName, city) {
    if (!schoolName) return;
    const input = document.getElementById('schoolSearch');
    if (input) input.value = schoolName;
    try {
        const params = new URLSearchParams();
        params.set('q', schoolName);
        if (city) params.set('city', city);
        params.set('limit', '10');
        const response = await fetch(`${API_BASE}/schools?${params.toString()}`);
        const result = await response.json();
        const found = (result.data || []).find(function (item) {
            return item.school_name === schoolName;
        });
        if (found) selectedSchool = found;
    } catch (error) {}
};

window.applySimulationSite = async function (siteId) {
    if (!siteId) return;
    if (!Array.isArray(cityList) || !cityList.length) {
        try {
            const response = await fetch(`${API_BASE}/cities`);
            const result = await response.json();
            cityList = result.data || [];
        } catch (error) {
            return;
        }
    }
    const city = cityList.find(function (item) { return item.city === siteId; });
    if (city) selectCity(city, false);
};

function onCityKeydown(event) {
    const panel = document.getElementById('cityCombo');
    const input = event.currentTarget;
    if (!panel || panel.hidden) {
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            renderCityPanel(input.value);
        }
        return;
    }
    const items = panel.querySelectorAll('.combo-item');
    if (!items.length) return;
    if (event.key === 'ArrowDown') {
        event.preventDefault();
        cityActive = Math.min(cityActive + 1, items.length - 1);
        highlightCity();
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        cityActive = Math.max(cityActive - 1, 0);
        highlightCity();
    } else if (event.key === 'Enter') {
        if (cityActive >= 0 && cityEntries[cityActive] !== undefined) {
            event.preventDefault();
            selectCity(cityEntries[cityActive]);
        }
    } else if (event.key === 'Escape') {
        hideCityPanel();
    }
}

function onCityChanged(showPanel) {
    selectedSchool = null;
    const schoolInput = document.getElementById('schoolSearch');
    if (schoolInput) schoolInput.value = '';
    const cityName = selectedCity ? selectedCity.city : '';
    const hint = document.getElementById('schoolHint');
    if (hint) {
        hint.textContent = cityName
            ? '正在列出 ' + cityName + ' 的学校…'
            : '全部城市，请输入学校名';
    }
    searchSchools('', cityName, showPanel === true);
}

async function searchSchools(query, city, open) {
    const panel = document.getElementById('schoolCombo');
    if (!panel) return;
    if (!query && !city) { hideSchoolPanel(); return; }
    try {
        const params = new URLSearchParams();
        if (query) params.set('q', query);
        if (city) params.set('city', city);
        params.set('limit', '50');
        const response = await fetch(`${API_BASE}/schools?${params.toString()}`);
        const result = await response.json();
        if (!result.success) return;
        schoolItems = result.data || [];
        schoolTotal = result.total || schoolItems.length;
        schoolIndex = {};
        schoolItems.forEach(function(school) { schoolIndex[school.school_name] = school; });
        if (open) renderSchoolPanel(schoolTotal, city);
    } catch (error) {
        console.error('School search failed:', error);
    }
}

function renderSchoolPanel(total, city) {
    const panel = document.getElementById('schoolCombo');
    const input = document.getElementById('schoolSearch');
    if (!panel) return;
    panel.innerHTML = '';
    schoolActive = -1;
    if (!schoolItems.length) {
        const empty = document.createElement('div');
        empty.className = 'combo-empty';
        empty.textContent = '没有匹配的学校，换个关键字试试';
        panel.appendChild(empty);
    } else {
        schoolItems.forEach(function(school, index) {
            const item = document.createElement('div');
            item.className = 'combo-item';
            item.setAttribute('role', 'option');
            item.dataset.index = String(index);
            const name = document.createElement('span');
            name.className = 'name';
            name.textContent = school.school_name;
            const meta = document.createElement('span');
            meta.className = 'meta';
            meta.textContent = [school.city, school.level].filter(Boolean).join(' · ');
            item.appendChild(name);
            item.appendChild(meta);
            item.addEventListener('mousedown', function(event) {
                event.preventDefault();
                selectSchool(index);
            });
            panel.appendChild(item);
        });
        if (total && total > schoolItems.length) {
            const more = document.createElement('div');
            more.className = 'combo-more';
            more.textContent = (city ? city + ' ' : '') + '共 ' + total + ' 所，已显示前 ' + schoolItems.length + ' 条，继续输入可缩小范围';
            panel.appendChild(more);
        }
    }
    panel.hidden = false;
    if (input) input.setAttribute('aria-expanded', 'true');
}

function hideSchoolPanel() {
    const panel = document.getElementById('schoolCombo');
    const input = document.getElementById('schoolSearch');
    if (panel) panel.hidden = true;
    if (input) input.setAttribute('aria-expanded', 'false');
    schoolActive = -1;
}

function highlightSchool() {
    const panel = document.getElementById('schoolCombo');
    if (!panel) return;
    const items = panel.querySelectorAll('.combo-item');
    items.forEach(function(item, index) {
        item.classList.toggle('active', index === schoolActive);
        if (index === schoolActive) item.scrollIntoView({ block: 'nearest' });
    });
}

function selectSchool(index) {
    const school = schoolItems[index];
    if (!school) return;
    selectedSchool = school;
    const input = document.getElementById('schoolSearch');
    input.value = school.school_name;
    const hint = document.getElementById('schoolHint');
    if (hint) hint.textContent = [school.province, school.city, school.level].filter(Boolean).join(' · ');
    hideSchoolPanel();
}

function onSchoolKeydown(event) {
    const panel = document.getElementById('schoolCombo');
    const input = event.currentTarget;
    if (!panel || panel.hidden) {
        if (event.key === 'ArrowDown' && input.value.trim()) {
            event.preventDefault();
            searchSchools(input.value.trim());
        }
        return;
    }
    const items = panel.querySelectorAll('.combo-item');
    if (!items.length) return;
    if (event.key === 'ArrowDown') {
        event.preventDefault();
        schoolActive = Math.min(schoolActive + 1, items.length - 1);
        highlightSchool();
    } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        schoolActive = Math.max(schoolActive - 1, 0);
        highlightSchool();
    } else if (event.key === 'Enter') {
        if (schoolActive >= 0) {
            event.preventDefault();
            selectSchool(schoolActive);
        }
    } else if (event.key === 'Escape') {
        hideSchoolPanel();
    }
}

function renderRuntimeNote(source, controlled, ranking) {
    const box = document.getElementById('runtimeNote');
    if (!box) return;

    const info = source || {};
    const parts = [];
    const fromCatalog = info.ambient_source === 'catalog';
    const fromTrace = info.scenario_source === 'catalog';
    let modeLabel = '合成基线';
    if (fromCatalog && fromTrace) modeLabel = '真实数据目录';
    else if (fromCatalog || fromTrace) modeLabel = '混合来源';
    parts.push('<span class="rn-item">数据来源 <span class="tag">' +
        escapeHtml(modeLabel) + '</span></span>');
    parts.push('<span class="rn-item">环境温度 <span class="tag' +
        (fromCatalog ? ' ok' : '') + '">' +
        escapeHtml(fromCatalog ? '数据目录' : '合成曲线') + '</span></span>');
    parts.push('<span class="rn-item">任务流 <span class="tag' +
        (fromTrace ? ' ok' : '') + '">' +
        escapeHtml(fromTrace ? '真实轨迹' : '合成生成') + '</span></span>');
    if (info.site_id) {
        parts.push('<span class="rn-item">站点 <strong>' + escapeHtml(info.site_id) + '</strong></span>');
    }
    if (info.emission_source === 'catalog') {
        parts.push('<span class="rn-item">碳因子 <span class="tag ok">国家公开数据</span></span>');
    }

    if (info.scenario_digest) {
        parts.push('<span class="rn-item">任务流快照 <span class="tag">' +
            escapeHtml(info.scenario_digest) + '</span></span>');
    }
    if (controlled === true) {
        parts.push('<span class="rn-item"><span class="tag ok">受控对比：四算法同一任务流</span></span>');
    } else if (controlled === false) {
        parts.push('<span class="rn-item"><span class="tag warn">任务流不一致</span></span>');
    }
    if (info.sla_slack !== null && info.sla_slack !== undefined) {
        parts.push('<span class="rn-item">SLA 松弛系数 <strong>' + escapeHtml(info.sla_slack) + '</strong></span>');
    }
    if (ranking && ranking.rule) {
        parts.push('<span class="rn-item">排名规则 <strong>' + escapeHtml(ranking.rule) + '</strong></span>');
    }
    box.innerHTML = parts.join('');
}

function renderRanking(ranking) {
    const list = document.getElementById('rankingList');
    if (!list) return;
    if (!ranking) {
        list.innerHTML = '<li class="excluded">暂无排名数据</li>';
        return;
    }
    const rows = [];
    (ranking.eligible || []).forEach(function(item) {
        rows.push('<li><span class="algo"><span class="rk">#' + item.rank + '</span>' +
            escapeHtml(getAlgorithmName(item.algorithm)) + '</span>' +
            '<span>' + numberOrDash(item.energy_per_served_load, 4, ' W/负载') +
            ' · 服务率 ' + numberOrDash(item.service_rate * 100, 1, '%') + '</span></li>');
    });
    (ranking.excluded || []).forEach(function(item) {
        rows.push('<li class="excluded"><span class="algo"><span class="rk">--</span>' +
            escapeHtml(getAlgorithmName(item.algorithm)) + '</span>' +
            '<span>' + escapeHtml(item.reason || '未达标') + '</span></li>');
    });
    rows.push('<li class="excluded"><span>' + escapeHtml(ranking.note || '') + '</span></li>');
    list.innerHTML = rows.join('');
}

async function loadDataAndCharts() {
    try {
        const response = await fetch(`${API_BASE}/data`);
        const result = await response.json();
        
        if (result.success && result.data) {
            updateCharts(result.data);
        }
    } catch (error) {
        console.error('Failed to load data:', error);
    }
}

function updateDashboard(data) {
    const set = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    };

    if (!data || Object.keys(data).length === 0) {
        ['avg-energy', 'max-temp', 'avg-pue', 'total-load'].forEach(id => set(id, '--'));
        return;
    }

    let bestEnergy = Infinity;
    let bestTemp = Infinity;
    let bestPUE = Infinity;
    let totalLoad = 0;

    Object.values(data).forEach(algo => {
        const peak = (algo.max_temp === null || algo.max_temp === undefined) ? algo.p95_temp : algo.max_temp;
        if (isFinite(algo.avg_energy) && algo.avg_energy < bestEnergy) bestEnergy = algo.avg_energy;
        if (isFinite(peak) && peak < bestTemp) bestTemp = peak;
        if (isFinite(algo.avg_pue) && algo.avg_pue < bestPUE) bestPUE = algo.avg_pue;
        if (isFinite(algo.total_load)) totalLoad += algo.total_load;
    });

    set('avg-energy', Number.isFinite(bestEnergy) ? bestEnergy.toFixed(1) : '--');
    set('max-temp', Number.isFinite(bestTemp) ? bestTemp.toFixed(1) : '--');
    set('avg-pue', Number.isFinite(bestPUE) ? bestPUE.toFixed(3) : '--');
    set('total-load', Math.round(totalLoad));
}

function buildLineDataset(algo, points, withFill) {
    const sc = seriesColor(algo);
    return {
        label: getAlgorithmName(algo),
        data: points,
        borderColor: sc.line,
        backgroundColor: sc.fill,
        fill: withFill,
        borderWidth: 2,
        tension: 0.3,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: sc.line,
        pointHoverBorderColor: '#ffffff',
        pointHoverBorderWidth: 2
    };
}

function updateCharts(data) {
    const algorithms = [...new Set(data.map(d => d.algorithm))];
    const hours = [...new Set(data.map(d => d.time))].sort((a, b) => a - b);
    const labels = hours.map(h => `${h}:00`);
    const pick = (algo, key, h) => {
        const row = data.find(x => x.time === h && x.algorithm === algo);
        return row ? row[key] : null;
    };

    const panels = [
        { chart: energyChart, key: 'total_energy',    fill: true  },
        { chart: tempChart,   key: 'avg_temperature', fill: false },
        { chart: loadChart,   key: 'total_load',      fill: true  },
        { chart: pueChart,    key: 'pue',             fill: false }
    ];

    panels.forEach(p => {
        if (!p.chart) return;
        p.chart.data.labels = labels;
        p.chart.data.datasets = algorithms.map(algo =>
            buildLineDataset(algo, hours.map(h => pick(algo, p.key, h)), p.fill)
        );
        p.chart.update();
    });

    updateAlgorithmRadar(data);
}

function updateAlgorithmRadar(data) {
    const algorithms = [...new Set(data.map(d => d.algorithm))];
    const clamp = (v) => Math.max(0, Math.min(1, v));
    const stats = algorithms.map(algo => {
        const rows = data.filter(d => d.algorithm === algo);
        const sum = (key) => rows.reduce((acc, row) => {
            const value = Number(row[key]);
            return acc + (Number.isFinite(value) ? value : 0);
        }, 0);
        const average = (key) => {
            const values = rows.map(row => Number(row[key])).filter(Number.isFinite);
            return values.length ? values.reduce((acc, value) => acc + value, 0) / values.length : 0;
        };
        const servedLoad = sum('served_load');
        const assigned = sum('assigned');
        const dropped = sum('dropped');
        const peaks = rows.map(row => Number(row.peak_temperature)).filter(Number.isFinite);
        return {
            algo,
            energy: sum('total_energy') / Math.max(servedLoad, 1),
            temperature: peaks.length ? Math.max(...peaks) : average('avg_temperature'),
            pue: average('pue'),
            serviceRate: assigned / Math.max(assigned + dropped, 1)
        };
    });
    const extent = (key) => {
        const values = stats.map(item => item[key]).filter(Number.isFinite);
        return values.length ? [Math.min(...values), Math.max(...values)] : [0, 0];
    };
    const energyRange = extent('energy');
    const temperatureRange = extent('temperature');
    const pueRange = extent('pue');
    const serviceRange = extent('serviceRate');
    const lowerBetter = (value, [min, max]) => {
        if (max - min < 1e-9) return 50;
        return clamp((max - value) / (max - min)) * 100;
    };
    const higherBetter = (value, [min, max]) => {
        if (max - min < 1e-9) return 50;
        return clamp((value - min) / (max - min)) * 100;
    };
    radarData = stats.map(item => {
        const scores = [
            lowerBetter(item.energy, energyRange),
            lowerBetter(item.temperature, temperatureRange),
            lowerBetter(item.pue, pueRange),
            higherBetter(item.serviceRate, serviceRange)
        ];
        return {
            ...item,
            scores,
            composite: scores.reduce((acc, value) => acc + value, 0) / scores.length
        };
    }).sort((a, b) => b.composite - a.composite);
    if (!radarData.some(item => item.algo === radarSelectedAlgo)) {
        radarSelectedAlgo = radarData[0] ? radarData[0].algo : null;
    }
    const tabs = document.getElementById('algorithmScoreTabs');
    if (tabs) {
        tabs.innerHTML = radarData.map(item => `
            <button type="button" class="algorithm-score-tab ${item.algo === radarSelectedAlgo ? 'is-active' : ''}" data-algorithm="${item.algo}">
                <span>${getAlgorithmName(item.algo)}</span>
                <strong>${item.composite.toFixed(1)}</strong>
            </button>`).join('');
    }
    renderSelectedRadar();
}

function renderSelectedRadar() {
    if (!radarChart || !radarData.length) return;
    const selected = radarData.find(item => item.algo === radarSelectedAlgo) || radarData[0];
    const averageScores = [0, 1, 2, 3].map(index => radarData.reduce((acc, item) => acc + item.scores[index], 0) / radarData.length);
    const labels = [
        `能耗效率\n单位负载能耗 ${selected.energy.toFixed(2)}`,
        `温度控制\n峰值温度 ${selected.temperature.toFixed(1)} ℃`,
        `PUE 指标\n平均 PUE ${selected.pue.toFixed(3)}`,
        `任务服务率\n${(selected.serviceRate * 100).toFixed(1)}%`
    ];
    radarChart.data.labels = labels;
    radarChart.data.datasets = [
        {
            label: getAlgorithmName(selected.algo),
            data: selected.scores,
            rawMetrics: { energy: selected.energy, temperature: selected.temperature, pue: selected.pue, serviceRate: selected.serviceRate },
            borderColor: seriesColor(selected.algo).line,
            backgroundColor: seriesColor(selected.algo).fill.replace('0.10', '0.12'),
            borderWidth: 2.5,
            pointBackgroundColor: seriesColor(selected.algo).line,
            pointBorderColor: '#ffffff',
            pointRadius: 4,
            pointHoverRadius: 6
        },
        {
            label: '本轮平均',
            data: averageScores,
            borderColor: '#8aa0b5',
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            borderDash: [5, 5],
            pointRadius: 0,
            pointHoverRadius: 0
        }
    ];
    radarChart.update();
    document.querySelectorAll('.algorithm-score-tab').forEach(button => {
        button.classList.toggle('is-active', button.getAttribute('data-algorithm') === selected.algo);
    });
    const summary = document.getElementById('algorithmRadarSummary');
    if (summary) {
        const rank = radarData.findIndex(item => item.algo === selected.algo) + 1;
        summary.innerHTML = `<span>当前算法：<strong>${getAlgorithmName(selected.algo)}</strong></span><span>综合分：<strong>${selected.composite.toFixed(1)} / 100</strong></span><span>排名：<strong>${rank} / ${radarData.length}</strong></span>`;
    }
}

function getAlgorithmName(algo) {
    const names = {
        greedy: '贪心算法',
        genetic: '遗传算法',
        round_robin: '轮询算法',
        damo_ets: 'DAMO-ETS (多目标能效)',
        nsga2: 'NSGA-II (多目标进化)',
        moead: 'MOEA-D (分解多目标)'
    };
    return names[algo] || algo;
}

async function runSimulation(e) {
    e.preventDefault();

    const numServers = parseInt(document.getElementById('numServers').value);
    const simHours = parseInt(document.getElementById('simHours').value);
    const parallelWorkers = parseInt(document.getElementById('parallelWorkers').value);
    const runMode = document.getElementById('runMode').value;
    const serverType = document.getElementById('serverType').value;
    const algorithms = Array.from(document.querySelectorAll('input[name="algorithms"]:checked'))
                           .map(cb => cb.value);
    const advancedAlgorithms = algorithms.includes('nsga2') || algorithms.includes('moead');

    const button = document.getElementById('runBtn');
    const progressBar = document.getElementById('progressBar');
    const progressText = document.getElementById('progressText');

    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin"></i> ' + (advancedAlgorithms ? '高级算法运行中（约 20 秒）...' : '运行中...');
    progressBar.style.display = 'block';

    try {
        progressText.textContent = advancedAlgorithms ? '正在运行高级算法，预计约 20 秒...' : '正在发送请求...';
        updateProgress(10);

        const payload = {
            num_servers: numServers,
            simulation_hours: simHours,
            parallel_workers: parallelWorkers,
            run_mode: runMode,
            server_type: serverType,
            algorithms: algorithms
        };

        if (selectedSchool) {
            payload.site_id = selectedSchool.city;
            payload.school_name = selectedSchool.school_name;
        } else if (selectedCity) {
            payload.site_id = selectedCity.city;
        }

        const override = window.pendingSimulationOverride;
        if (override && override.site_id) payload.site_id = override.site_id;
        if (override && override.school_name) payload.school_name = override.school_name;
        window.pendingSimulationOverride = null;

        const response = await fetch(`${API_BASE}/simulation`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        progressText.textContent = '正在处理数据...';
        updateProgress(50);

        const result = await response.json();

        if (result.success) {
            progressText.textContent = '正在更新图表...';
            updateProgress(80);

            if (result.warnings && result.warnings.length) {
                showToast(result.warnings.join('；'), 'warning');
            }

            await loadMetrics();
            window.dispatchEvent(new CustomEvent('dcs:run-completed', {
                detail: { run_id: result.run_id, status: 'success', origin: 'web' }
            }));
            window.dispatchEvent(new CustomEvent('dcs:simulation-updated', {
                detail: Object.assign({}, result.data || {}, { run_id: result.run_id })
            }));
            updateProgress(100);

            setTimeout(() => {
                progressBar.style.display = 'none';
                button.disabled = false;
                button.innerHTML = '<i class="fas fa-play"></i> 开始运行';
                
                document.getElementById('status').className = 'badge bg-success';
                document.getElementById('status').textContent = '仿真完成';
                
                setTimeout(() => {
                    document.getElementById('status').textContent = '系统在线';
                }, 3000);
            }, 500);
        } else {
            throw new Error(result.message || result.error || ('HTTP ' + response.status));
        }
    } catch (error) {
        console.error('Simulation failed:', error);
        window.pendingSimulationOverride = null;
        window.dispatchEvent(new CustomEvent('dcs:run-failed', {
            detail: { status: 'failed', origin: 'web', error: error.message }
        }));
        const message = error.message === 'Failed to fetch' ? '请求没有连上服务，请确认服务已启动后再试。' : error.message;
        if (typeof showToast === 'function') showToast('仿真失败：' + message, 'danger');
        else alert('仿真失败：' + message);
        button.disabled = false;
        button.innerHTML = '<i class="fas fa-play"></i> 开始运行';
        progressBar.style.display = 'none';
    }
}

function updateRunStatus(data) {
    const status = document.getElementById('status');
    if (!status) return;
    if (data.status === 'running' || data.status === 'queued') {
        status.className = 'badge bg-warning';
        status.textContent = '仿真运行中';
    } else if (data.status === 'success') {
        status.className = 'badge bg-success';
        status.textContent = '仿真完成';
        setTimeout(function () {
            status.textContent = '系统在线';
        }, 3000);
    } else if (data.status === 'failed') {
        status.className = 'badge bg-danger';
        status.textContent = '仿真失败';
    }
}

function connectRunEvents() {
    if (!window.EventSource) return;
    const source = new EventSource(`${API_BASE}/events`);
    source.onmessage = function (event) {
        let data;
        try { data = JSON.parse(event.data); } catch (error) { return; }
        if (data.type === 'run_created' || data.type === 'run_progress') {
            updateRunStatus(data);
            return;
        }
        if (data.type === 'run_completed') {
            updateRunStatus(data);
            loadMetrics();
            window.dispatchEvent(new CustomEvent('dcs:run-completed', { detail: data }));
            window.dispatchEvent(new CustomEvent('dcs:simulation-updated', { detail: data }));
            if (typeof showToast === 'function') showToast('仿真完成，页面数据已同步', 'success');
            return;
        }
        if (data.type === 'run_failed') {
            updateRunStatus(data);
            window.dispatchEvent(new CustomEvent('dcs:run-failed', { detail: data }));
            if (typeof showToast === 'function') showToast('仿真失败：' + (data.error || '未知错误'), 'danger');
        }
    };
    source.onerror = function () {};
}

function updateProgress(percent) {
    const bar = document.querySelector('.progress-bar');
    bar.style.width = `${percent}%`;
}

async function checkHealth() {
    try {
        const response = await fetch('/health');
        if (response.ok) {
            document.getElementById('status').className = 'badge bg-success';
            document.getElementById('status').textContent = '系统在线';
        } else {
            document.getElementById('status').className = 'badge bg-danger';
            document.getElementById('status').textContent = '系统离线';
        }
    } catch {
        document.getElementById('status').className = 'badge bg-danger';
        document.getElementById('status').textContent = '系统离线';
    }
}

async function loadPlugins() {
    try {
        const response = await fetch(`${API_BASE}/plugins`);
        const result = await response.json();
        
        if (result.success) {
            renderPlugins(result.plugins);
        } else {
            renderPlugins([]);
        }
    } catch (error) {
        console.error('Failed to load plugins:', error);
        renderPlugins([]);
    }
}

function renderPlugins(plugins) {
    const container = document.getElementById('pluginsContainer');

    if (!plugins || plugins.length === 0) {
        container.innerHTML = `
            <div class="col-12">
                <div class="card"><div class="card-body">
                    <div class="empty-state">
                        <i class="fas fa-plug"></i>
                        <p style="margin:0;">暂无可用插件</p>
                    </div>
                </div></div>
            </div>`;
        return;
    }

    container.innerHTML = plugins.map(plugin => {
        return `
        <div class="col-lg-6">
            <div class="card plugin-card ${plugin.loaded ? 'loaded' : 'not-loaded'}">
                <div class="card-header">
                    <h5>${plugin.name}</h5>
                    <span class="status-badge badge ${plugin.loaded ? 'bg-success' : 'bg-warning'}">
                        ${plugin.loaded ? '已加载' : '未加载'}
                    </span>
                    <span class="status-badge badge bg-secondary">${pluginCategoryLabel(plugin)}</span>
                    <span class="plugin-ver">v${plugin.version}</span>
                </div>
                <div class="card-body">
                    <div class="d-flex gap-2">
                        ${!plugin.loaded ? `
                            <button class="btn btn-sm btn-success" onclick="loadPlugin('${plugin.name}')">
                                <i class="fas fa-power-off"></i> 加载插件
                            </button>` : ''}
                        ${plugin.loaded ? `
                            <button class="btn btn-sm btn-secondary" onclick="configurePlugin('${plugin.name}')">
                                <i class="fas fa-gear"></i> 配置
                            </button>
                            <button class="btn btn-sm btn-danger" onclick="unloadPlugin('${plugin.name}')">
                                <i class="fas fa-power-off"></i> 卸载
                            </button>` : ''}
                    </div>
                </div>
            </div>
        </div>`;
    }).join('');
}

async function loadPlugin(pluginName) {
    try {
        const defaultConfig = getDefaultPluginConfig(pluginName);
        const response = await fetch(`${API_BASE}/plugins/${pluginName}/load`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(defaultConfig)
        });
        
        const result = await response.json();
        
        if (result.success) {
            await loadPlugins();
            showToast('插件加载成功', 'success');
        } else {
            showToast('插件加载失败: ' + result.message, 'danger');
        }
    } catch (error) {
        showToast('插件加载失败: ' + error.message, 'danger');
    }
}

async function unloadPlugin(pluginName) {
    try {
        const response = await fetch(`${API_BASE}/plugins/${pluginName}/unload`, {
            method: 'POST'
        });
        
        const result = await response.json();
        
        if (result.success) {
            await loadPlugins();
            showToast('插件卸载成功', 'success');
        } else {
            showToast('插件卸载失败: ' + result.message, 'danger');
        }
    } catch (error) {
        showToast('插件卸载失败: ' + error.message, 'danger');
    }
}

function configurePlugin(pluginName) {
    currentPluginConfig.pluginName = pluginName;
    
    const configFields = getPluginConfigFields(pluginName);
    document.getElementById('pluginConfigTitle').textContent = `${pluginName} 配置`;
    document.getElementById('configFields').innerHTML = configFields.map(field => `
        <div class="mb-3">
            <label class="form-label">${field.label}</label>
            <input type="${field.type}" class="form-control" id="config_${field.name}" 
                   value="${field.default}" ${field.min ? `min="${field.min}"` : ''} ${field.max ? `max="${field.max}"` : ''}>
            <small class="text-muted">${field.description}</small>
        </div>
    `).join('');
    
    const modal = new bootstrap.Modal(document.getElementById('pluginConfigModal'));
    modal.show();
}

async function savePluginConfig() {
    const pluginName = currentPluginConfig.pluginName;
    const config = {};
    
    document.querySelectorAll('[id^="config_"]').forEach(input => {
        const name = input.id.replace('config_', '');
        const value = input.type === 'number' ? parseFloat(input.value) : input.value;
        config[name] = value;
    });

    try {
        await unloadPlugin(pluginName);
        await loadPluginWithConfig(pluginName, config);
        
        const modal = bootstrap.Modal.getInstance(document.getElementById('pluginConfigModal'));
        modal.hide();
        
        showToast('配置保存成功', 'success');
    } catch (error) {
        showToast('配置保存失败: ' + error.message, 'danger');
    }
}

async function loadPluginWithConfig(pluginName, config) {
    const response = await fetch(`${API_BASE}/plugins/${pluginName}/load`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config)
    });
    
    const result = await response.json();
    if (!result.success) {
        throw new Error(result.message);
    }
    
    await loadPlugins();
}

function getDefaultPluginConfig(pluginName) {
    const configs = {
        'SimulationPlugin': {
            num_servers: 10,
            simulation_hours: 24,
            results_dir: 'data',
            reports_dir: 'reports'
        }
    };
    return configs[pluginName] || {};
}

function getPluginConfigFields(pluginName) {
    const fields = {
        'SimulationPlugin': [
            { name: 'num_servers', label: '服务器数量', type: 'number', default: 10, min: 1, max: 100, description: '仿真中使用的服务器数量' },
            { name: 'simulation_hours', label: '仿真时长(小时)', type: 'number', default: 24, min: 1, max: 168, description: '仿真运行的小时数' },
            { name: 'results_dir', label: '结果目录', type: 'text', default: 'data', description: '数据文件保存目录' },
            { name: 'reports_dir', label: '报告目录', type: 'text', default: 'reports', description: '图表报告保存目录' }
        ]
    };
    return fields[pluginName] || [];
}

function showToast(message, type) {
    const kind = type === 'success' ? 'is-success' : (type === 'warning' ? 'is-warning' : 'is-danger');
    const icon = type === 'success' ? 'circle-check' : (type === 'warning' ? 'triangle-exclamation' : 'circle-exclamation');

    const el = document.createElement('div');
    el.className = `app-toast ${kind} position-fixed bottom-0 end-0 m-4`;
    el.style.zIndex = '1080';
    const body = document.createElement('div');
    body.className = 'toast-body d-flex align-items-center gap-2';
    const glyph = document.createElement('i');
    glyph.className = `fas fa-${icon}`;
    const text = document.createElement('span');
    text.textContent = message;
    body.appendChild(glyph);
    body.appendChild(text);
    el.appendChild(body);

    document.body.appendChild(el);
    setTimeout(() => {
        el.style.transition = 'opacity 0.3s ease';
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 320);
    }, 3000);
}


var PLUGIN_CATEGORIES = { executor: '执行器', algorithm: '调度算法', adapter: '适配器' };

function pluginCategoryLabel(plugin) {
    var key = plugin.category ? plugin.category : 'algorithm';
    return PLUGIN_CATEGORIES[key] ? PLUGIN_CATEGORIES[key] : key;
}

function escapeHtml(value) {
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function renderMetricsTable(data, ranking) {
    const tbody = document.querySelector('#metricsTable tbody');
    if (!tbody) return;
    const algorithms = Object.keys(data);
    if (!algorithms.length) {
        tbody.innerHTML = '';
        return;
    }
    const rows = algorithms.map(function(algo) {
        return Object.assign({ algo: algo }, data[algo]);
    });
    const finite = function(value) { return value !== null && value !== undefined && isFinite(value); };
    const numeric = function(key) { return rows.map(function(r) { return r[key]; }).filter(finite); };

    const energies = numeric('avg_energy');
    const temps = numeric('max_temp');
    const bestEnergy = energies.length ? Math.min.apply(null, energies) : null;
    const bestTemp = temps.length ? Math.min.apply(null, temps) : null;
    const ranks = {};
    if (ranking && ranking.eligible) {
        ranking.eligible.forEach(function(item) { ranks[item.algorithm] = item.rank; });
    }

    tbody.innerHTML = rows.map(function(r) {
        const hasRate = typeof r.service_rate === 'number';
        const rateText = hasRate ? (r.service_rate * 100).toFixed(1) + '%' : '--';
        const rateClass = hasRate && r.service_rate < 0.98 ? ' class="warn"' : '';
        const energyClass = finite(r.avg_energy) && r.avg_energy === bestEnergy ? ' class="best"' : '';
        const tempClass = finite(r.max_temp) && r.max_temp === bestTemp ? ' class="best"' : '';
        const rank = ranks[r.algo];
        const rankCell = rank ? '<td class="best">#' + rank + '</td>' : '<td class="dim">未达标</td>';
        return '<tr>' +
            '<td>' + escapeHtml(getAlgorithmName(r.algo)) + '</td>' +
            '<td class="num"' + energyClass + '>' + numberOrDash(r.avg_energy, 1) + '</td>' +
            '<td class="num"' + tempClass + '>' + numberOrDash(r.max_temp, 2) + '</td>' +
            '<td class="num">' + numberOrDash(r.avg_pue, 3) + '</td>' +
            '<td class="num">' + (finite(r.total_load) ? Math.round(r.total_load) : '--') + '</td>' +
            '<td class="num"' + rateClass + '>' + rateText + '</td>' +
            '<td class="num">' + numberOrDash(r.energy_per_served_load, 4) + '</td>' +
            rankCell +
            '</tr>';
    }).join('');
}

function aiNote(text) {
    const box = document.getElementById('aiNotes');
    if (box) box.textContent = text ? text : '';
}

function loadAiStatus() {
    const badge = document.getElementById('aiEngine');
    fetch(API_BASE + '/ai/status')
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (!badge) return;
            if (res.success && res.status.available && !res.status.last_error) {
                badge.textContent = '大模型已启用 · ' + res.status.model;
            } else if (res.success && res.status.available) {
                badge.textContent = '大模型不可用 · 已回退本地规则';
                badge.title = res.status.reason ? res.status.reason : '';
            } else {
                badge.textContent = '本地专家规则 · 未配置 API Key';
            }
        })
        .catch(function() {
            if (badge) badge.textContent = 'AI 服务不可用';
        });
}

function runAiAnalyze() {
    const btn = document.getElementById('aiAnalyzeBtn');
    const box = document.getElementById('aiResult');
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '分析中...';
    aiNote('正在读取最新仿真指标');
    fetch(API_BASE + '/ai/analyze', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (!res.success) {
                const msg = res.message ? res.message : '诊断失败';
                box.innerHTML = '<p class="ai-empty">' + escapeHtml(msg) + '</p>';
                aiNote('');
                return;
            }
            renderAiResult(res.result);
        })
        .catch(function(err) {
            box.innerHTML = '<p class="ai-empty">诊断请求失败：' + escapeHtml(err.message) + '</p>';
        })
        .finally(function() {
            btn.disabled = false;
            btn.textContent = original;
        });
}

function renderAiResult(result) {
    const box = document.getElementById('aiResult');
    const tuning = result.tuning ? result.tuning : {};
    const risks = result.risks ? result.risks : [];
    const actions = result.actions ? result.actions : [];
    const parts = ['<p class="ai-summary">' + escapeHtml(result.summary ? result.summary : '') + '</p>'];

    if (risks.length) {
        parts.push('<div class="ai-block"><div class="ai-block-title">风险提示</div><ul class="ai-list">' +
            risks.map(function(t) { return '<li>' + escapeHtml(t) + '</li>'; }).join('') + '</ul></div>');
    }
    const tuningKeys = Object.keys(tuning);
    if (tuningKeys.length) {
        parts.push('<div class="ai-block"><div class="ai-block-title">建议权重</div><div class="ai-kv">' +
            tuningKeys.map(function(k) { return '<span>' + escapeHtml(WEIGHT_LABELS[k] ? WEIGHT_LABELS[k] : k) + ' = ' + escapeHtml(tuning[k]) + '</span>'; }).join('') +
            '</div></div>');
    }
    if (actions.length) {
        parts.push('<div class="ai-block"><div class="ai-block-title">可执行动作</div><ul class="ai-list">' +
            actions.map(function(t) { return '<li>' + escapeHtml(t) + '</li>'; }).join('') + '</ul></div>');
    }
    box.innerHTML = parts.join('');
    if (result.source === 'llm') {
        aiNote('由 ' + (result.model ? result.model : 'DeepSeek') + ' 生成');
    } else {
        const notice = result.notice ? result.notice : '本地专家规则生成（配置 DEEPSEEK_API_KEY 后自动切换为大模型）';
        aiNote(notice);
    }
}

function parseAiIntent() {
    const input = document.getElementById('aiPrompt');
    const btn = document.getElementById('aiParseBtn');
    const original = btn.textContent;
    const text = String(input.value).trim();
    if (!text) {
        aiNote('请先输入一句需求描述');
        return;
    }
    btn.disabled = true;
    btn.textContent = '解析中...';
    fetch(API_BASE + '/ai/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: text })
    })
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (!res.success) {
                aiNote(res.message ? res.message : '解析失败');
                return;
            }
            applyAiParams(res.params);
            const notes = res.notes ? res.notes.slice() : [];
            if (res.notice) notes.push(res.notice);
            const prefix = res.source === 'llm' ? '大模型解析：' : '本地规则解析：';
            aiNote(prefix + (notes.length ? notes.join(' ') : '参数已填入控制中心，可直接运行'));
        })
        .catch(function(err) {
            aiNote('解析请求失败：' + err.message);
        })
        .finally(function() {
            btn.disabled = false;
            btn.textContent = original;
        });
}

function applyAiParams(params) {
    if (params.num_servers) document.getElementById('numServers').value = params.num_servers;
    if (params.simulation_hours) document.getElementById('simHours').value = params.simulation_hours;
    if (params.parallel_workers) document.getElementById('parallelWorkers').value = params.parallel_workers;
    if (params.run_mode) document.getElementById('runMode').value = params.run_mode;
    if (params.server_type) document.getElementById('serverType').value = params.server_type;
    const algorithms = params.algorithms ? params.algorithms : [];
    if (algorithms.length) {
        document.querySelectorAll('input[name="algorithms"]').forEach(function(box) {
            box.checked = algorithms.indexOf(box.value) >= 0;
        });
    }
}


var lastTuning = null;

var WEIGHT_LABELS = {
    alpha_deadline: 'α 截止',
    beta_energy: 'β 能耗',
    gamma_thermal: 'γ 温度',
    delta_heterogeneity: 'δ 异构',
    epsilon_migration: 'ε 迁移'
};

function runTune() {
    const btn = document.getElementById('tuneBtn');
    const box = document.getElementById('tuneResult');
    const original = btn.textContent;
    const servers = parseInt(document.getElementById('tuneServers').value, 10);
    const hours = parseInt(document.getElementById('tuneHours').value, 10);
    btn.disabled = true;
    btn.textContent = '寻优中...';
    aiNote('');
    document.getElementById('tuneNotes').textContent = '正在枚举权重方案并逐组仿真…';
    fetch(API_BASE + '/ai/tune', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ num_servers: servers, simulation_hours: hours })
    })
        .then(function(r) { return r.json(); })
        .then(function(res) {
            if (!res.success) {
                const msg = res.message ? res.message : '寻优失败';
                box.innerHTML = '<p class="ai-empty">' + escapeHtml(msg) + '</p>';
                document.getElementById('tuneNotes').textContent = '';
                return;
            }
            lastTuning = res.result;
            renderTune(res.result);
        })
        .catch(function(err) {
            box.innerHTML = '<p class="ai-empty">寻优请求失败：' + escapeHtml(err.message) + '</p>';
        })
        .finally(function() {
            btn.disabled = false;
            btn.textContent = original;
        });
}

function renderTune(result) {
    const keys = Object.keys(WEIGHT_LABELS);
    const best = result.best ? result.best.name : '';
    let html = '<div class="table-wrap"><table class="data-table"><thead><tr><th>方案</th>';
    keys.forEach(function(k) { html += '<th>' + WEIGHT_LABELS[k] + '</th>'; });
    html += '<th>平均能耗 (W)</th><th>最高温度 (°C)</th><th>服务率</th></tr></thead><tbody>';
    (result.rows ? result.rows : []).forEach(function(row) {
        const isBest = row.name === best;
        html += '<tr' + (isBest ? ' class="row-best"' : '') + '><td>' + escapeHtml(row.name) + (isBest ? ' ★' : '') + '</td>';
        keys.forEach(function(k) {
            const value = row.weights[k];
            html += '<td>' + (typeof value === 'number' ? value.toFixed(2) : '--') + '</td>';
        });
        html += '<td>' + row.metrics.avg_energy.toFixed(1) + '</td>';
        html += '<td>' + row.metrics.max_temp.toFixed(2) + '</td>';
        html += '<td>' + (row.metrics.service_rate * 100).toFixed(1) + '%</td></tr>';
    });
    html += '</tbody></table></div>';

    const ex = result.explanation ? result.explanation : {};
    if (ex.summary) {
        html += '<div class="ai-block"><div class="ai-block-title">AI 解释</div>';
        html += '<p class="ai-summary">' + escapeHtml(ex.summary) + '</p>';
        const reasons = ex.reasons ? ex.reasons : [];
        if (reasons.length) {
            html += '<ul class="ai-list">' + reasons.map(function(t) {
                return '<li>' + escapeHtml(t) + '</li>';
            }).join('') + '</ul>';
        }
        if (ex.caution) html += '<p class="ai-empty">' + escapeHtml(ex.caution) + '</p>';
        html += '</div>';
    }
    document.getElementById('tuneResult').innerHTML = html;

    const engine = ex.source === 'llm' ? '大模型解释' : '本地规则解释';
    document.getElementById('tuneNotes').textContent =
        '择优规则：' + (result.rule ? result.rule : '') +
        '　候选 ' + (result.candidates ? result.candidates : 0) + ' 组，耗时 ' + (result.elapsed_sec ? result.elapsed_sec : 0) + ' 秒　（' + engine + '）';
    document.getElementById('applyTuneBtn').disabled = !result.best;
}

function applyTunedWeights() {
    if (!lastTuning || !lastTuning.best) {
        aiNote('请先执行一次寻优');
        return;
    }
    const btn = document.getElementById('applyTuneBtn');
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '写入中...';
    fetch(API_BASE + '/ai/apply-tuning', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ weights: lastTuning.best.weights })
    })
        .then(function(r) { return r.json(); })
        .then(function(res) {
            const notes = document.getElementById('tuneNotes');
            if (!res.success) {
                notes.textContent = res.message ? res.message : '写入失败';
                return;
            }
            const applied = res.result.applied ? res.result.applied : {};
            const before = res.result.previous ? res.result.previous : {};
            const parts = Object.keys(applied).map(function(k) {
                const oldValue = before[k];
                return k + ' ' + (typeof oldValue === 'number' ? oldValue.toFixed(2) : '--') + ' → ' + applied[k].toFixed(2);
            });
            notes.textContent = '已写入 ' + res.result.path + '：' + parts.join('，') + '（新权重已生效，下次仿真即采用）';
            btn.textContent = '已应用';
        })
        .catch(function(err) {
            document.getElementById('tuneNotes').textContent = '写入失败：' + err.message;
            btn.disabled = false;
            btn.textContent = original;
        });
}

var lastOptimization = null;
var optimizationTimer = null;

function setOptTab(name) {
    document.querySelectorAll('.opt-tab').forEach(function (tab) {
        tab.classList.toggle('is-active', tab.dataset.optTab === name);
    });
    document.getElementById('optGlobalPanel').hidden = name !== 'global';
    document.getElementById('optQuickPanel').hidden = name !== 'quick';
    document.getElementById('optHeaderHint').textContent = name === 'global'
        ? '目标驱动，自动搜索配置组合'
        : '固定种子，逐组仿真对比';
}

function readOptimizeRequest() {
    const min = parseInt(document.getElementById('optServerMin').value, 10);
    const max = parseInt(document.getElementById('optServerMax').value, 10);
    const hours = parseInt(document.getElementById('optHours').value, 10);
    const floor = parseFloat(document.getElementById('optServiceFloor').value) / 100;
    const maxTempValue = document.getElementById('optMaxTemp').value;
    const algorithms = Array.from(document.querySelectorAll('input[name="optAlgorithms"]:checked')).map(function (box) {
        return box.value;
    });
    if (!algorithms.length) throw new Error('请至少选择一个算法');
    if (!Number.isFinite(min) || !Number.isFinite(max) || min > max) throw new Error('服务器范围填写不正确');
    if (!Number.isFinite(hours) || hours < 1 || hours > 72) throw new Error('仿真时长应为 1～72 小时');
    if (!Number.isFinite(floor) || floor < 0.8 || floor > 1) throw new Error('服务率门槛应为 80%～100%');
    const selection = typeof window.dcsGetSelection === 'function' ? window.dcsGetSelection() : {};
    return {
        server_min: min,
        server_max: max,
        simulation_hours: hours,
        algorithms: algorithms,
        target: document.getElementById('optTarget').value,
        service_rate_floor: floor,
        max_temp: maxTempValue === '' ? null : parseFloat(maxTempValue),
        include_weights: document.getElementById('optWeights').checked,
        site_id: selection.site_id || '',
        school_name: selection.school_name || ''
    };
}

async function startGlobalOptimize() {
    const button = document.getElementById('optStartBtn');
    const box = document.getElementById('optResult');
    const notes = document.getElementById('optNotes');
    let payload;
    try {
        payload = readOptimizeRequest();
    } catch (error) {
        notes.textContent = error.message;
        return;
    }
    button.disabled = true;
    button.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>寻优中';
    notes.textContent = '正在提交任务…';
    box.innerHTML = '<p class="ai-empty">正在准备候选方案…</p>';
    document.getElementById('optProgress').hidden = false;
    try {
        const response = await fetch(API_BASE + '/ai/optimize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!result.success) throw new Error(result.message || '启动寻优失败');
        lastOptimization = result.job;
        renderOptimize(result.job);
        pollOptimize(result.job.job_id);
    } catch (error) {
        notes.textContent = '启动失败：' + error.message;
        box.innerHTML = '<p class="ai-empty">' + escapeHtml(error.message) + '</p>';
        button.disabled = false;
        button.innerHTML = '<i class="fas fa-play me-1"></i>开始全局寻优';
    }
}

function pollOptimize(jobId) {
    if (optimizationTimer) clearInterval(optimizationTimer);
    optimizationTimer = setInterval(async function () {
        try {
            const response = await fetch(API_BASE + '/ai/optimize/' + jobId);
            const result = await response.json();
            if (!result.success) return;
            lastOptimization = result.job;
            renderOptimize(result.job);
            if (result.job.status === 'success' || result.job.status === 'failed') {
                clearInterval(optimizationTimer);
                optimizationTimer = null;
            }
        } catch (error) {
             
        }
    }, 1500);
}

function renderOptimize(job) {
    const progress = document.getElementById('optProgress');
    const bar = document.getElementById('optProgressBar');
    const pct = Math.max(0, Math.min(100, Number(job.progress) || 0));
    progress.hidden = job.status === 'success' || job.status === 'failed' ? true : false;
    bar.style.width = pct + '%';
    document.getElementById('optProgressPct').textContent = pct + '%';
    document.getElementById('optProgressText').textContent = job.message || '处理中';
    document.getElementById('optNotes').textContent = job.message || '';
    const button = document.getElementById('optStartBtn');
    if (job.status === 'success' || job.status === 'failed') {
        button.disabled = false;
        button.innerHTML = '<i class="fas fa-play me-1"></i>开始全局寻优';
    }
    const box = document.getElementById('optResult');
    if (job.status === 'failed') {
        box.innerHTML = '<p class="ai-empty">寻优失败：' + escapeHtml(job.error || job.message || '未知错误') + '</p>';
        return;
    }
    if (!job.result) {
        box.innerHTML = '<p class="ai-empty">正在评估候选方案，完成后会显示推荐结果…</p>';
        return;
    }
    const result = job.result;
    const explanation = result.explanation || {};
    let html = '<div class="ai-block"><div class="ai-block-title">推荐结果</div>';
    html += '<p class="ai-summary">' + escapeHtml(explanation.summary || '') + '</p></div>';
    html += '<div class="plan-grid">';
    (result.top || []).forEach(function (plan) {
        const metrics = plan.metrics || {};
        html += '<div class="plan-card' + (plan.rank === 1 ? ' is-best' : '') + '">';
        html += '<h6><span>' + plan.num_servers + ' 台 · ' + escapeHtml(plan.algorithm_label || plan.algorithm) + '</span>';
        html += '<span class="plan-rank">' + (plan.rank === 1 ? '推荐' : '备选 ' + plan.rank) + (plan.pareto ? ' · Pareto' : '') + '</span></h6>';
        if (plan.weight_label) html += '<p>' + escapeHtml(plan.weight_label) + '</p>';
        html += '<div class="plan-metrics">';
        html += '<span>PUE ' + (metrics.avg_pue != null ? metrics.avg_pue.toFixed(3) : '--') + '</span>';
        html += '<span>单位能耗 ' + (metrics.energy_per_served_load != null ? metrics.energy_per_served_load.toFixed(2) : '--') + '</span>';
        html += '<span>峰值温度 ' + (metrics.max_temp != null ? metrics.max_temp.toFixed(1) + '℃' : '--') + '</span>';
        html += '<span>服务率 ' + (metrics.service_rate != null ? (metrics.service_rate * 100).toFixed(1) + '%' : '--') + '</span>';
        html += '</div></div>';
    });
    html += '</div>';
    if (explanation.reasons && explanation.reasons.length) {
        html += '<div class="ai-block"><div class="ai-block-title">取舍说明</div><ul class="ai-list">';
        explanation.reasons.forEach(function (line) {
            html += '<li>' + escapeHtml(line) + '</li>';
        });
        html += '</ul></div>';
    }
    if (explanation.caution) html += '<p class="ai-empty">' + escapeHtml(explanation.caution) + '</p>';
    html += '<div class="plan-actions"><button class="btn btn-primary" type="button" id="optApplyBtn"><i class="fas fa-check me-1"></i>应用推荐方案并运行</button>';
    html += '<span class="ai-empty">候选 ' + result.evaluated + ' 组，耗时 ' + (result.elapsed_sec || 0) + ' 秒，Pareto ' + result.pareto_count + ' 个</span></div>';
    box.innerHTML = html;
    const applyButton = document.getElementById('optApplyBtn');
    if (applyButton) applyButton.addEventListener('click', function () { applyOptimize(job.job_id); });
}

async function applyOptimize(jobId) {
    const button = document.getElementById('optApplyBtn');
    const notes = document.getElementById('optNotes');
    if (button) {
        button.disabled = true;
        button.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>启动中';
    }
    try {
        const response = await fetch(API_BASE + '/ai/optimize/' + jobId + '/apply', { method: 'POST' });
        const result = await response.json();
        if (!result.success) throw new Error(result.message || '应用失败');
        notes.textContent = '已按推荐方案启动运行（运行编号：' + result.result.run_id + '），页面数据会在完成后自动刷新。';
        if (button) button.innerHTML = '<i class="fas fa-check me-1"></i>已启动运行';
    } catch (error) {
        notes.textContent = '应用失败：' + error.message;
        if (button) {
            button.disabled = false;
            button.innerHTML = '<i class="fas fa-check me-1"></i>应用推荐方案并运行';
        }
    }
}

window.dcsGetSelection = function () {
    return {
        site_id: selectedSchool ? selectedSchool.city : (selectedCity ? selectedCity.city : ''),
        school_name: selectedSchool ? selectedSchool.school_name : '',
        city_name: selectedCity ? selectedCity.city : ''
    };
};

document.addEventListener('DOMContentLoaded', async function () {
    const params = new URLSearchParams(window.location.search);
    const city = params.get('city');
    const school = params.get('school');
    if (city && typeof window.applySimulationSite === 'function') {
        await window.applySimulationSite(city);
    }
    if (school && typeof window.applySimulationSchool === 'function') {
        await window.applySimulationSchool(school, city);
    }
    if (params.get('autorun') === '1') {
        const servers = document.getElementById('numServers');
        const hours = document.getElementById('simHours');
        const serversParam = params.get('servers');
        const hoursParam = params.get('hours');
        const algorithmsParam = params.get('algorithms');
        if (servers) servers.value = serversParam || '48';
        if (hours) hours.value = hoursParam || '24';
        if (algorithmsParam) {
            const allowed = algorithmsParam.split(',').map(function (item) { return item.trim(); }).filter(Boolean);
            if (allowed.length) {
                document.querySelectorAll('input[name="algorithms"]').forEach(function (box) {
                    box.checked = allowed.indexOf(box.value) >= 0;
                });
            }
        }
        setTimeout(function () {
            const form = document.getElementById('simulationForm');
            if (!form) return;
            if (form.requestSubmit) form.requestSubmit();
            else form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
        }, 650);
    }
});


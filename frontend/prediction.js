(function () {
    'use strict';

    var API_URL = '/api/v1/ai/predict';
    var chart = null;
    var chartMetric = 'pue';
    var lastPredictionData = null;
    var ALGORITHM_LABELS = {
        greedy: '贪心算法',
        genetic: '遗传算法',
        round_robin: '轮询算法',
        damo_ets: 'DAMO-ETS 能效调度',
        nsga2: 'NSGA-II',
        moead: 'MOEA-D'
    };
    var ALGORITHM_COLORS = {
        greedy: '#5794f2',
        genetic: '#b877d9',
        round_robin: '#73bf69',
        damo_ets: '#ff9830',
        nsga2: '#e24d42',
        moead: '#eab839'
    };

    function byId(id) {
        return document.getElementById(id);
    }

    function getSelection() {
        if (typeof window.dcsGetSelection === 'function') {
            return window.dcsGetSelection();
        }
        var city = byId('citySearch');
        var school = byId('schoolSearch');
        return {
            site_id: city && city.value ? city.value : '北京市',
            school_name: school ? school.value : ''
        };
    }

    function updateContext(extra) {
        var context = byId('predictContext');
        if (!context) return;
        var selection = getSelection();
        var parts = ['当前站点：' + (selection.site_id || '北京市')];
        parts.push(selection.school_name ? '学校：' + selection.school_name : '学校：未选择（使用城市）');
        if (extra) parts.push(extra);
        context.textContent = parts.join(' · ');
    }

    function showError(message) {
        var error = byId('predictError');
        if (!error) return;
        error.textContent = message;
        error.hidden = false;
    }

    function hideError() {
        var error = byId('predictError');
        if (!error) return;
        error.hidden = true;
        error.textContent = '';
    }

    function number(value, digits) {
        var parsed = Number(value);
        return Number.isFinite(parsed) ? parsed.toFixed(digits) : '--';
    }

    function selectedAlgorithms() {
        var select = byId('predictAlgorithms');
        var value = select ? select.value : 'core';
        if (value === 'core') {
            return ['greedy', 'genetic', 'round_robin', 'damo_ets', 'nsga2', 'moead'];
        }
        if (value === 'custom') {
            return Array.prototype.slice.call(document.querySelectorAll('input[name="predictAlgorithm"]:checked')).map(function (input) {
                return input.value;
            });
        }
        return [value];
    }

    function updateCustomPanel() {
        var select = byId('predictAlgorithms');
        var panel = byId('predictCustomAlgorithms');
        if (!panel) return;
        panel.hidden = !select || select.value !== 'custom';
    }

    function updateCustomCount() {
        var count = document.querySelectorAll('input[name="predictAlgorithm"]:checked').length;
        var label = byId('predictCustomCount');
        if (label) label.textContent = '已选 ' + count + ' 个';
    }

    function algorithmLabel(key) {
        return ALGORITHM_LABELS[key] || key;
    }

    function translatePredictionText(text) {
        var result = String(text || '');
        Object.keys(ALGORITHM_LABELS).forEach(function (key) {
            result = result.split(key).join(ALGORITHM_LABELS[key]);
        });
        result = result.replace(/server_type\s+mixed/g, '服务器类型 混合类型');
        result = result.replace(/\bmixed\b/g, '混合类型');
        return result;
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function renderKpis(data) {
        var container = byId('predictKpis');
        if (!container) return;
        var best = data.best_algorithm || Object.keys(data.summary || {})[0];
        var stats = (data.summary || {})[best] || {};
        var cards = [
            { label: '平均 PUE', value: number(stats.avg_pue, 2), unit: '' },
            { label: '峰值温度', value: number(stats.max_temp, 1), unit: '°C' },
            { label: '平均功率', value: number(stats.avg_energy, 0), unit: 'W' },
            { label: '服务率', value: number((Number(stats.service_rate) || 0) * 100, 1), unit: '%' }
        ];
        container.innerHTML = cards.map(function (card) {
            return '<div class="col-6 col-xl-3">' +
                '<div class="prediction-kpi">' +
                '<div class="label">' + card.label + '</div>' +
                '<div class="value">' + card.value +
                (card.unit ? '<span class="unit">' + card.unit + '</span>' : '') +
                '</div></div></div>';
        }).join('');
    }

    function renderAlgorithmTable(data) {
        var container = byId('predictTable');
        if (!container) return;
        var summary = data.summary || {};
        var entries = Object.keys(summary).map(function (key) {
            return { key: key, metrics: summary[key] || {} };
        });
        if (!entries.length) {
            container.innerHTML = '';
            return;
        }
        var bestKey = data.best_algorithm;
        if (!bestKey) {
            var eligible = entries.filter(function (entry) {
                return Number(entry.metrics.service_rate) >= 0.98;
            });
            var pool = eligible.length ? eligible : entries.slice();
            pool.sort(function (a, b) {
                return (Number(a.metrics.energy_per_served_load) || Infinity) -
                    (Number(b.metrics.energy_per_served_load) || Infinity);
            });
            bestKey = pool[0].key;
        }
        entries.sort(function (a, b) {
            var aEligible = Number(a.metrics.service_rate) >= 0.98;
            var bEligible = Number(b.metrics.service_rate) >= 0.98;
            if (aEligible !== bEligible) return aEligible ? -1 : 1;
            return (Number(a.metrics.energy_per_served_load) || Infinity) -
                (Number(b.metrics.energy_per_served_load) || Infinity);
        });
        var rows = entries.map(function (entry) {
            var metrics = entry.metrics;
            var rate = Number(metrics.service_rate) || 0;
            var status = entry.key === bestKey
                ? '<span class="prediction-badge best">最优</span>'
                : (rate < 0.98
                    ? '<span class="prediction-badge warn">服务率不足</span>'
                    : '<span class="prediction-badge">已纳入</span>');
            return '<tr>' +
                '<td>' + escapeHtml(algorithmLabel(entry.key)) + '</td>' +
                '<td>' + number(metrics.avg_pue, 2) + '</td>' +
                '<td>' + number(metrics.max_temp, 1) + '°C</td>' +
                '<td>' + number(rate * 100, 1) + '%</td>' +
                '<td>' + number(metrics.energy_per_served_load, 2) + '</td>' +
                '<td>' + status + '</td>' +
                '</tr>';
        }).join('');
        container.innerHTML = '<table class="prediction-table">' +
            '<thead><tr><th>算法</th><th>平均 PUE</th><th>峰值温度</th><th>服务率</th><th>能耗/负载</th><th>状态</th></tr></thead>' +
            '<tbody>' + rows + '</tbody></table>';
    }

    function renderChart(data) {
        var canvas = byId('predictChart');
        if (!canvas || typeof window.Chart === 'undefined') return;
        var seriesMap = data.series || {};
        var algorithms = Object.keys(seriesMap);
        if (!algorithms.length) return;
        var first = seriesMap[algorithms[0]] || [];
        var labels = first.map(function (item) { return '第 ' + item.time + ' 小时'; });
        var datasets = algorithms.map(function (algorithm) {
            var series = seriesMap[algorithm] || [];
            return {
                label: algorithmLabel(algorithm),
                data: series.map(function (item) { return item[chartMetric]; }),
                borderColor: ALGORITHM_COLORS[algorithm] || '#8e8e99',
                backgroundColor: 'transparent',
                tension: 0.25,
                pointRadius: 0,
                borderWidth: 2,
                fill: false
            };
        });
        if (chart) {
            chart.destroy();
            chart = null;
        }
        chart = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: {
                        labels: { color: '#4a6076', boxWidth: 12, boxHeight: 3 }
                    }
                },
                scales: {
                    x: {
                        grid: { color: '#c2d9eb', drawTicks: false },
                        ticks: { color: '#7a8fa3', maxRotation: 0, autoSkip: true, maxTicksLimit: 8, font: { size: 10 } }
                    },
                    y: {
                        title: {
                            display: true,
                            text: chartMetric === 'pue' ? 'PUE' : '峰值温度 (°C)',
                            color: '#4a6076',
                            font: { size: 11 }
                        },
                        grid: { color: '#c2d9eb', drawTicks: false },
                        ticks: { color: '#7a8fa3', font: { size: 10 } }
                    }
                }
            }
        });
    }

    function renderAssumptions(data) {
        var list = byId('predictAssumptions');
        if (!list) return;
        list.innerHTML = '';
        var items = (data.assumptions || []).slice();
        if (data.disclaimer) items.push(data.disclaimer);
        items.forEach(function (text) {
            var item = document.createElement('li');
            item.textContent = translatePredictionText(text);
            list.appendChild(item);
        });
    }

    function renderResult(data) {
        updateContext('最优算法：' + algorithmLabel(data.best_algorithm || '--'));
        var empty = byId('predictEmpty');
        var result = byId('predictResult');
        if (empty) empty.hidden = true;
        if (result) result.hidden = false;
        lastPredictionData = data;
        renderKpis(data);
        renderAlgorithmTable(data);
        renderChart(data);
        var explanation = byId('predictExplanation');
        if (explanation) explanation.textContent = translatePredictionText(data.explanation || '预测已完成。');
        renderAssumptions(data);
        if (result && result.scrollIntoView) {
            setTimeout(function () {
                result.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 80);
        }
    }

    function runPrediction() {
        var button = byId('predictBtn');
        var servers = parseInt(byId('predictServers').value, 10);
        var hours = parseInt(byId('predictHours').value, 10);
        if (!Number.isFinite(servers) || servers < 1 || servers > 1000) {
            showError('服务器数量需在 1-1000 之间');
            return;
        }
        var algorithms = selectedAlgorithms();
        if (!algorithms.length) {
            showError('自定义算法至少选择一个算法');
            return;
        }
        var selection = getSelection();
        hideError();
        updateContext();
        var original = button ? button.innerHTML : '';
        if (button) {
            button.disabled = true;
            button.innerHTML = '<i class="fas fa-spinner fa-spin me-1"></i>预测计算中...';
        }
        fetch(API_URL, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                num_servers: servers,
                horizon_hours: hours,
                parallel_workers: 1,
                server_type: 'mixed',
                algorithms: algorithms,
                site_id: selection.site_id || '北京市',
                school_name: selection.school_name || ''
            })
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok || data.success === false) {
                    throw new Error(data.message || '预测失败，请稍后重试');
                }
                return data;
            });
        }).then(function (data) {
            renderResult(data);
        }).catch(function (error) {
            showError(error.message || '预测失败，请稍后重试');
        }).finally(function () {
            if (button) {
                button.disabled = false;
                button.innerHTML = original;
            }
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        var button = byId('predictBtn');
        if (!button) return;
        updateContext();
        var metricSelect = byId('predictChartMetric');
        if (metricSelect) {
            metricSelect.addEventListener('change', function () {
                chartMetric = metricSelect.value;
                if (lastPredictionData) renderChart(lastPredictionData);
            });
        }
        var algorithmSelect = byId('predictAlgorithms');
        if (algorithmSelect) {
            algorithmSelect.addEventListener('change', updateCustomPanel);
            updateCustomPanel();
        }
        var customChecks = document.querySelectorAll('input[name="predictAlgorithm"]');
        Array.prototype.forEach.call(customChecks, function (checkbox) {
            checkbox.addEventListener('change', updateCustomCount);
        });
        updateCustomCount();
        button.addEventListener('click', runPrediction);
    });
})();
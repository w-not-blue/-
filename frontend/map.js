(function () {
    'use strict';

    var DATA_URL = '/api/v1/map/data';
    var GEO_URL = '/static/maps/china.json';
    var chart = null;
    var climateChart = null;
    var mapData = null;
    var metric = 'school_count';
    var currentSchools = [];
    var selectedCityName = '';
    var selectedSchoolName = '';
    var runsCache = null;
    var runsPromise = null;
    var cityDetailCache = {};
    var compareItems = [];
    var detailMode = 'overview';
    var mapView = { center: [104, 36], zoom: 1.16 };
    var searchTimer = null;

    var ALGO_LABELS = {
        greedy: '贪心算法',
        genetic: '遗传算法',
        round_robin: '轮询算法',
        damo_ets: 'DAMO-ETS',
        nsga2: 'NSGA-II',
        moead: 'MOEA-D'
    };

    var METRICS = {
        school_count: {
            label: '学校密度',
            colors: ['#d7ebfa', '#a9d0ea', '#6aa9d6', '#f0a35e'],
            format: function (value) { return Math.round(Number(value) || 0) + ' 所'; }
        },
        avg_temp: {
            label: '年均气温',
            colors: ['#d7ebfa', '#9fcbe5', '#4e9f9b', '#f0a35e'],
            format: function (value) { return (Number(value) || 0).toFixed(1) + ' °C'; }
        },
        carbon_factor: {
            label: '区域碳因子',
            colors: ['#d7ebfa', '#a8cde2', '#5b9fbd', '#e18a45'],
            format: function (value) { return (Number(value) || 0).toFixed(3) + ' kg CO2/kWh'; }
        },
        heat_risk: {
            label: '高温风险',
            colors: ['#d7ebfa', '#9fc4d4', '#d6a35e', '#d9534f'],
            format: function (value) { return (Number(value) || 0).toFixed(1) + ' °C'; }
        }
    };

    function compareKey(item) {
        return item.type + ':' + item.name;
    }

    function isInCompare(item) {
        return compareItems.some(function (entry) {
            return compareKey(entry) === compareKey(item);
        });
    }

    function byId(id) {
        return document.getElementById(id);
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function numberText(value, digits, fallback) {
        var number = Number(value);
        return Number.isFinite(number) ? number.toFixed(digits) : (fallback || '--');
    }

    function metricKey() {
        return metric === 'heat_risk' ? 'avg_temp' : metric;
    }

    function metricValue(item) {
        if (!item) return null;
        var value = item[metricKey()];
        var number = Number(value);
        return Number.isFinite(number) ? number : null;
    }

    function rangeFor(values) {
        var clean = values.filter(function (value) { return Number.isFinite(value); });
        if (!clean.length) return { min: 0, max: 1 };
        var min = Math.min.apply(null, clean);
        var max = Math.max.apply(null, clean);
        if (metric === 'school_count') min = 0;
        if (min === max) {
            if (metric === 'school_count') max = min + 1;
            else { min = min - 1; max = max + 1; }
        }
        return { min: min, max: max };
    }

    function tempColor(value) {
        var number = Number(value);
        if (!Number.isFinite(number)) return '#5b6573';
        if (number >= 28) return '#d9534f';
        if (number >= 22) return '#e18a45';
        if (number >= 15) return '#f0a35e';
        if (number >= 8) return '#4e9f9b';
        return '#3f8fa4';
    }

    function carbonColor(value) {
        var number = Number(value);
        if (!Number.isFinite(number)) return '#5b6573';
        if (number >= 0.65) return '#e18a45';
        if (number >= 0.45) return '#f0a35e';
        return '#3f8fa4';
    }

    function cityColor(city) {
        if (metric === 'school_count') return '#ffb15e';
        if (metric === 'carbon_factor') return carbonColor(city.carbon_factor);
        return tempColor(city.avg_temp);
    }

    function cityPoint(city, showLabel) {
        var point = {
            name: city.city,
            value: [city.longitude, city.latitude, city.school_count],
            city: city
        };
        if (showLabel) {
            point.label = {
                show: true,
                formatter: city.city,
                position: 'right',
                color: '#243b53',
                fontSize: 9,
                distance: 4,
                textBorderColor: '#ddebfa',
                textBorderWidth: 2
            };
        }
        return point;
    }

    function visibleCities() {
        var cities = (mapData.cities || []).slice().sort(function (a, b) {
            return (Number(b.school_count) || 0) - (Number(a.school_count) || 0);
        });
        var top = cities.slice(0, 28);
        if (selectedCityName) {
            var selected = cities.find(function (item) { return item.city === selectedCityName; });
            if (selected && top.indexOf(selected) < 0) top.push(selected);
        }
        return top;
    }

    function successfulRuns() {
        return (runsCache || []).filter(function (run) {
            return run.status === 'success' && run.summary && Object.keys(run.summary).length > 0;
        });
    }

    function runCounts() {
        var cities = {};
        var schools = {};
        successfulRuns().forEach(function (run) {
            var params = run.params || {};
            if (params.site_id) cities[params.site_id] = true;
            if (params.school_name) schools[params.school_name] = true;
        });
        return {
            cities: Object.keys(cities).length,
            schools: Object.keys(schools).length
        };
    }

    function overviewRow(city, rank, valueText) {
        return '<button type="button" class="map-overview-row" data-city="' + escapeHtml(city.city) + '">' +
            '<span class="map-overview-rank">' + (rank < 10 ? '0' + rank : rank) + '</span>' +
            '<span class="map-overview-city">' + escapeHtml(city.city) + '</span>' +
            '<span class="map-overview-value">' + escapeHtml(valueText) + '</span>' +
            '</button>';
    }

    function renderNationalOverview() {
        if (!mapData) return;
        var detail = byId('mapDetail');
        var hint = byId('mapDetailHint');
        if (!detail) return;
        detailMode = 'overview';
        if (hint) hint.textContent = '全国概览';

        var cities = (mapData.cities || []).slice();
        var topSchools = cities.slice().sort(function (a, b) {
            return (Number(b.school_count) || 0) - (Number(a.school_count) || 0);
        }).slice(0, 5);
        var topTemp = cities.filter(function (city) {
            return Number.isFinite(Number(city.avg_temp));
        }).sort(function (a, b) {
            return Number(b.avg_temp) - Number(a.avg_temp);
        }).slice(0, 5);
        var carbonCities = cities.filter(function (city) {
            return Number.isFinite(Number(city.carbon_factor));
        }).sort(function (a, b) {
            return Number(a.carbon_factor) - Number(b.carbon_factor);
        });
        var lowestCarbon = carbonCities[0];
        var highestCarbon = carbonCities[carbonCities.length - 1];
        var totals = mapData.totals || {};
        var runs = runCounts();

        detail.innerHTML = '<div class="map-overview">' +
            '<div class="map-overview-head">' +
            '<div class="map-overview-title">全国概览</div>' +
            '<div class="map-overview-sub">点击任意条目，地图会定位到对应城市</div>' +
            '</div>' +
            '<div class="map-overview-stats">' +
            '<div><span>覆盖城市</span><strong>' + Number(totals.cities || cities.length) + '</strong></div>' +
            '<div><span>已试算城市</span><strong>' + runs.cities + '</strong></div>' +
            '<div><span>已试算学校</span><strong>' + runs.schools + '</strong></div>' +
            '</div>' +
            '<div class="map-overview-section">' +
            '<div class="map-overview-section-title"><span>学校数量 Top 5</span><em>所</em></div>' +
            '<div class="map-overview-list">' + topSchools.map(function (city, index) {
                return overviewRow(city, index + 1, Math.round(Number(city.school_count) || 0) + ' 所');
            }).join('') + '</div></div>' +
            '<div class="map-overview-section">' +
            '<div class="map-overview-section-title"><span>高温风险 Top 5</span><em>年均气温</em></div>' +
            '<div class="map-overview-list">' + topTemp.map(function (city, index) {
                return overviewRow(city, index + 1, Number(city.avg_temp).toFixed(1) + '°C');
            }).join('') + '</div></div>' +
            '<div class="map-overview-section">' +
            '<div class="map-overview-section-title"><span>区域碳因子</span><em>kg CO2/kWh</em></div>' +
            '<div class="map-overview-pair">' +
            (lowestCarbon ? '<button type="button" data-city="' + escapeHtml(lowestCarbon.city) + '"><span>最低</span><strong>' + escapeHtml(lowestCarbon.city) + '</strong><em>' + Number(lowestCarbon.carbon_factor).toFixed(3) + '</em></button>' : '') +
            (highestCarbon ? '<button type="button" data-city="' + escapeHtml(highestCarbon.city) + '"><span>最高</span><strong>' + escapeHtml(highestCarbon.city) + '</strong><em>' + Number(highestCarbon.carbon_factor).toFixed(3) + '</em></button>' : '') +
            '</div></div>' +
            '<div class="map-overview-note">学校名单 2025 · 城市气象 2025 · 碳因子 2021 年度</div>' +
            '</div>';
    }

    function rankingValueText(value) {
        if (metric === 'school_count') return Math.round(Number(value) || 0) + ' 所';
        if (metric === 'carbon_factor') return Number(value).toFixed(3);
        return Number(value).toFixed(1) + ' °C';
    }

    function renderCityRanking() {
        if (!mapData) return;
        var container = byId('mapCityRanking');
        var hint = byId('mapRankingHint');
        if (!container) return;
        var config = METRICS[metric];
        if (hint) hint.textContent = '按' + config.label;
        var rows = (mapData.cities || []).map(function (city) {
            return { city: city, value: metricValue(city) };
        }).filter(function (item) {
            return Number.isFinite(item.value);
        }).sort(function (a, b) {
            return b.value - a.value;
        }).slice(0, 8);
        if (!rows.length) {
            container.innerHTML = '<div class="map-empty">暂无排行数据。</div>';
            return;
        }
        var max = rows[0].value || 1;
        var color = config.colors[2] || '#4e9f9b';
        container.innerHTML = rows.map(function (item, index) {
            var width = Math.max(6, Math.round((item.value / max) * 100));
            var selected = item.city.city === selectedCityName ? ' is-selected' : '';
            return '<button type="button" class="map-rank-row' + selected + '" data-city="' + escapeHtml(item.city.city) + '">' +
                '<span class="map-rank-index">' + (index + 1) + '</span>' +
                '<span class="map-rank-city">' + escapeHtml(item.city.city) + '</span>' +
                '<span class="map-rank-track"><i style="width:' + width + '%;background:' + color + '"></i></span>' +
                '<span class="map-rank-value">' + escapeHtml(rankingValueText(item.value)) + '</span>' +
                '</button>';
        }).join('');
    }

    function renderSelectedCard(city) {
        var card = byId('mapSelectedCard');
        if (!card) return;
        if (!city) {
            card.hidden = true;
            card.innerHTML = '';
            return;
        }
        var run = latestRunForCity(city.city);
        var best = run ? pickBestRun(run.summary) : null;
        var status = best
            ? '最近试算 · ' + (ALGO_LABELS[best.algorithm] || best.algorithm) + ' · PUE ' + numberText(best.metrics.avg_pue, 3)
            : '尚未试算';
        card.innerHTML = '<div class="map-selected-head"><span>当前城市</span><button type="button" data-selected-close aria-label="关闭">×</button></div>' +
            '<div class="map-selected-name">' + escapeHtml(city.city) + '</div>' +
            '<div class="map-selected-meta">' + city.school_count + ' 所学校 · ' +
            (city.avg_temp == null ? '--' : Number(city.avg_temp).toFixed(1) + '°C') + ' · 碳因子 ' +
            (city.carbon_factor == null ? '--' : Number(city.carbon_factor).toFixed(3)) + '</div>' +
            '<div class="map-selected-status' + (best ? ' is-ready' : '') + '">' + escapeHtml(status) + '</div>';
        card.hidden = false;
    }

    function provinceTooltip(province) {
        if (!province) return '';
        return '<div style="font-weight:600;margin-bottom:4px;">' + escapeHtml(province.name) + '</div>' +
            '<div style="color:#4a6076;font-size:11px;">' + escapeHtml(province.region || '') + '</div>' +
            '<div style="margin-top:6px;">学校：<strong>' + province.school_count + '</strong> 所</div>' +
            '<div>年均气温：<strong>' + (province.avg_temp == null ? '--' : Number(province.avg_temp).toFixed(1) + '°C') + '</strong></div>' +
            '<div>碳因子：<strong>' + (province.carbon_factor == null ? '--' : Number(province.carbon_factor).toFixed(3)) + '</strong></div>';
    }

    function cityTooltip(city) {
        if (!city) return '';
        var run = latestRunForCity(city.city);
        var best = run ? pickBestRun(run.summary) : null;
        var runLine = best
            ? '最近试算：<strong>' + escapeHtml(ALGO_LABELS[best.algorithm] || best.algorithm) + ' · PUE ' + numberText(best.metrics.avg_pue, 3) + '</strong>'
            : '最近试算：<strong>尚未试算</strong>';
        return '<div style="font-weight:600;margin-bottom:4px;">' + escapeHtml(city.city) + '</div>' +
            '<div style="color:#4a6076;font-size:11px;">' + escapeHtml(city.province || '') + '</div>' +
            '<div style="margin-top:6px;">学校：<strong>' + city.school_count + '</strong> 所</div>' +
            '<div>年均气温：<strong>' + (city.avg_temp == null ? '--' : Number(city.avg_temp).toFixed(1) + '°C') + '</strong></div>' +
            '<div>碳因子：<strong>' + (city.carbon_factor == null ? '--' : Number(city.carbon_factor).toFixed(3)) + '</strong></div>' +
            '<div style="margin-top:6px;padding-top:6px;border-top:1px solid #d2e2f0;color:#4a6076;font-size:11px;">' + runLine + '</div>';
    }

    function renderChart() {
        if (!chart || !mapData) return;
        var config = METRICS[metric];
        var provinceValues = mapData.provinces.map(metricValue);
        var range = rangeFor(provinceValues);
        var provinceData = mapData.provinces.map(function (province) {
            return {
                name: province.name,
                value: metricValue(province),
                label: { show: Number(province.school_count) >= 25 }
            };
        });
        var visible = visibleCities();
        var labelKeys = {};
        visible.slice().sort(function (a, b) {
            return (metricValue(b) || 0) - (metricValue(a) || 0);
        }).slice(0, 6).forEach(function (city) {
            labelKeys[city.city] = true;
        });
        var cityData = visible.map(function (city) {
            return cityPoint(city, !!labelKeys[city.city]);
        });
        var allCityData = (mapData.cities || []).map(function (city) {
            return cityPoint(city, false);
        });
        var runCityData = visible.filter(function (city) {
            return !!latestRunForCity(city.city);
        }).map(function (city) {
            return cityPoint(city, false);
        });
        var selectedCity = (mapData.cities || []).find(function (city) {
            return city.city === selectedCityName;
        });
        var selectedData = selectedCity ? [cityPoint(selectedCity, false)] : [];

        chart.setOption({
            backgroundColor: 'transparent',
            tooltip: {
                trigger: 'item',
                backgroundColor: '#ffffff',
                borderColor: '#a5c3db',
                borderWidth: 1,
                textStyle: { color: '#1b2a3a', fontSize: 12 },
                formatter: function (params) {
                    if (params.seriesType === 'scatter') {
                        return cityTooltip(params.data && params.data.city);
                    }
                    var province = (mapData.provinces || []).find(function (item) {
                        return item.name === params.name;
                    });
                    return provinceTooltip(province);
                }
            },
            visualMap: {
                min: range.min,
                max: range.max,
                left: 18,
                bottom: 22,
                calculable: true,
                seriesIndex: 0,
                text: [config.label + ' 高', '低'],
                textStyle: { color: '#4a6076', fontSize: 10 },
                itemWidth: 10,
                itemHeight: 96,
                inRange: { color: config.colors },
                formatter: function (value) { return config.format(value); }
            },
            geo: {
                map: 'china',
                roam: true,
                zoom: mapView.zoom,
                center: mapView.center,
                layoutCenter: ['50%', '52%'],
                layoutSize: '108%',
                scaleLimit: { min: 0.8, max: 5 },
                itemStyle: {
                    areaColor: '#eef7ff',
                    borderColor: '#a5c3db',
                    borderWidth: 1
                },
                emphasis: {
                    itemStyle: { areaColor: '#d3e9fb' },
                    label: { show: true, color: '#1b2a3a', fontSize: 11 }
                },
                label: { show: false }
            },
            series: [
                {
                    name: config.label,
                    type: 'map',
                    geoIndex: 0,
                    data: provinceData,
                    selectedMode: false,
                    label: {
                        show: false,
                        color: 'rgba(58,78,98,0.55)',
                        fontSize: 8
                    },
                    labelLayout: { hideOverlap: true },
                    emphasis: { label: { show: true, color: '#1b2a3a', fontSize: 11 } }
                },
                {
                    name: '全部城市',
                    type: 'scatter',
                    coordinateSystem: 'geo',
                    data: allCityData,
                    symbolSize: 2.6,
                    itemStyle: { color: 'rgba(90,110,130,0.25)' },
                    silent: true,
                    zlevel: 2
                },
                {
                    name: '重点城市',
                    type: 'scatter',
                    coordinateSystem: 'geo',
                    data: cityData,
                    symbolSize: function (value) {
                        return Math.max(5, Math.min(18, 5 + Math.sqrt(Number(value[2]) || 0) * 1.9));
                    },
                    itemStyle: {
                        color: function (params) {
                            return params.data && params.data.city ? cityColor(params.data.city) : '#ffb15e';
                        },
                        borderColor: 'rgba(27,42,58,0.18)',
                        borderWidth: 1,
                        opacity: 0.86
                    },
                    label: { show: false },
                    labelLayout: { hideOverlap: true },
                    emphasis: {
                        scale: 1.6,
                        itemStyle: { borderColor: '#1b2a3a', borderWidth: 1, opacity: 1 }
                    },
                    zlevel: 3
                },
                {
                    name: '已试算城市',
                    type: 'scatter',
                    coordinateSystem: 'geo',
                    data: runCityData,
                    symbolSize: function (value) {
                        return Math.max(11, Math.min(26, 11 + Math.sqrt(Number(value[2]) || 0) * 1.9));
                    },
                    itemStyle: {
                        color: 'transparent',
                        borderColor: 'rgba(22,104,220,0.55)',
                        borderWidth: 1.2
                    },
                    silent: true,
                    zlevel: 4
                },
                {
                    name: '选中城市',
                    type: 'scatter',
                    coordinateSystem: 'geo',
                    data: selectedData,
                    symbolSize: function (value) {
                        return Math.max(18, Math.min(34, 17 + Math.sqrt(Number(value[2]) || 0) * 1.9));
                    },
                    itemStyle: {
                        color: 'transparent',
                        borderColor: '#1668dc',
                        borderWidth: 2,
                        opacity: 0.95
                    },
                    silent: true,
                    zlevel: 5
                }
            ]
        }, true);
        updateLegend();
        updateLayerLabel();
        renderCityRanking();
        renderSelectedCard(selectedCity);
    }

    function updateLegend() {
        var legend = byId('mapLegend');
        if (!legend || !mapData) return;
        var config = METRICS[metric];
        var range = rangeFor((mapData.provinces || []).map(metricValue));
        legend.innerHTML = '<div class="map-legend-head"><span>' + escapeHtml(config.label) + '</span><span>颜色深浅</span></div>' +
            '<div class="map-legend-scale"><span>' + escapeHtml(config.format(range.min)) + '</span>' +
            '<i style="background:linear-gradient(90deg,' + config.colors.join(',') + ')"></i>' +
            '<span>' + escapeHtml(config.format(range.max)) + '</span></div>' +
            '<div class="map-legend-keys"><span><i class="map-legend-dot is-small"></i>少</span><span><i class="map-legend-dot is-large"></i>多</span><span><i class="map-legend-ring"></i>已试算</span></div>';
    }

    function updateLayerLabel() {
        var label = byId('mapLayerLabel');
        if (label) label.textContent = METRICS[metric].label;
    }

    function renderDetailHeader(title, subtitle, metrics) {
        return '<div class="map-detail-head">' +
            '<div class="map-detail-title">' + escapeHtml(title) + '</div>' +
            '<div class="map-detail-sub">' + escapeHtml(subtitle) + '</div>' +
            '</div>' +
            '<div class="map-metric-grid">' +
            metrics.map(function (item) {
                return '<div class="map-metric"><span>' + escapeHtml(item.label) + '</span><strong>' + escapeHtml(item.value) + '</strong></div>';
            }).join('') +
            '</div>';
    }

    function loadRuns() {
        if (!runsPromise) {
            runsPromise = fetch('/api/v1/runs?limit=100')
                .then(function (response) { return response.json(); })
                .then(function (data) {
                    runsCache = data.success ? (data.runs || []) : [];
                    return runsCache;
                })
                .catch(function () {
                    runsCache = [];
                    return runsCache;
                });
        }
        return runsPromise;
    }

    function pickBestRun(summary) {
        var items = Object.keys(summary || {}).map(function (algorithm) {
            return { algorithm: algorithm, metrics: summary[algorithm] || {} };
        }).filter(function (item) {
            return Number.isFinite(Number(item.metrics.service_rate));
        });
        if (!items.length) return null;
        var eligible = items.filter(function (item) {
            return Number(item.metrics.service_rate) >= 0.98;
        });
        var pool = eligible.length ? eligible : items;
        pool.sort(function (a, b) {
            return (Number(a.metrics.energy_per_served_load) || Infinity) -
                (Number(b.metrics.energy_per_served_load) || Infinity);
        });
        return pool[0];
    }

    function latestRunForSchool(schoolName) {
        var runs = runsCache || [];
        for (var i = 0; i < runs.length; i++) {
            var run = runs[i];
            if (run.status !== 'success' || !run.summary || !Object.keys(run.summary).length) continue;
            if (run.params && run.params.school_name === schoolName) return run;
        }
        return null;
    }

    function latestRunForCity(cityName) {
        var runs = runsCache || [];
        for (var i = 0; i < runs.length; i++) {
            var run = runs[i];
            if (run.status !== 'success' || !run.summary || !Object.keys(run.summary).length) continue;
            if (run.params && run.params.site_id === cityName) return run;
        }
        return null;
    }

    function renderRunSummary(run) {
        if (!run) {
            return '<div class="map-run-empty">这个学校还没有运行记录，先做一次试算。</div>';
        }
        var best = pickBestRun(run.summary);
        if (!best) {
            return '<div class="map-run-empty">最近一次运行没有可用的汇总结果。</div>';
        }
        var metrics = best.metrics;
        return '<div class="map-run-summary">' +
            '<div><span>平均 PUE</span><strong>' + numberText(metrics.avg_pue, 2) + '</strong></div>' +
            '<div><span>峰值温度</span><strong>' + numberText(metrics.max_temp, 1) + '°C</strong></div>' +
            '<div><span>服务率</span><strong>' + (metrics.service_rate == null ? '--' : numberText(Number(metrics.service_rate) * 100, 1) + '%') + '</strong></div>' +
            '<div><span>最优算法</span><strong>' + escapeHtml(ALGO_LABELS[best.algorithm] || best.algorithm) + '</strong></div>' +
            '</div>' +
            '<div class="map-run-meta">最近运行：' + escapeHtml(run.created_at || '') + ' · ' + escapeHtml(run.run_id || '') + '</div>';
    }

    function loadCityDetail(city) {
        var key = city.city;
        if (cityDetailCache[key]) return Promise.resolve(cityDetailCache[key]);
        return fetch('/api/v1/map/city/' + encodeURIComponent(key))
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (data && data.success) cityDetailCache[key] = data;
                return data;
            })
            .catch(function () {
                return {
                    success: false,
                    city: key,
                    months: [],
                    recommendation: {
                        servers: 48,
                        hours: 24,
                        algorithm: 'damo_ets',
                        pue: null,
                        max_temp: null,
                        service_rate: null,
                        source: 'baseline'
                    }
                };
            });
    }

    function renderRecommendation(city, data) {
        var box = byId('mapRecommendation');
        if (!box) return;
        var rec = (data && data.recommendation) || {};
        var schoolRun = selectedSchoolName ? latestRunForSchool(selectedSchoolName) : null;
        var source = rec.source || 'baseline';
        if (schoolRun) {
            var best = pickBestRun(schoolRun.summary);
            if (best) {
                rec = {
                    servers: (schoolRun.params || {}).num_servers || 48,
                    hours: (schoolRun.params || {}).simulation_hours || 24,
                    algorithm: best.algorithm,
                    pue: best.metrics.avg_pue,
                    max_temp: best.metrics.max_temp,
                    service_rate: best.metrics.service_rate,
                    source: 'school_run'
                };
                source = 'school_run';
            }
        }
        if (selectedSchoolName && !schoolRun) {
            rec = {
                servers: rec.servers || 48,
                hours: rec.hours || 24,
                algorithm: rec.algorithm || 'damo_ets',
                pue: null,
                max_temp: null,
                service_rate: null
            };
            source = 'school_none';
        }
        var algorithm = rec.algorithm || 'damo_ets';
        var servers = rec.servers || 48;
        var hours = rec.hours || 24;
        var pue = rec.pue == null ? '待试算' : numberText(rec.pue, 3);
        var maxTemp = rec.max_temp == null ? '--' : numberText(rec.max_temp, 1) + '°C';
        var service = rec.service_rate == null ? '--' : numberText(Number(rec.service_rate) * 100, 1) + '%';
        var sourceText = source === 'school_run' ? '来自该校最近一次运行' :
            (source === 'school_none' ? '该校暂无运行结果，以下为默认配置' :
                (source === 'latest_run' ? '来自该城市最近一次运行' : '暂无运行结果，可自定义'));
        var item = selectedSchoolName
            ? { type: 'school', name: selectedSchoolName, city: city.city }
            : { type: 'city', name: city.city, city: city.city };
        var inCompare = isInCompare(item);
        var options = Object.keys(ALGO_LABELS).map(function (key) {
            return '<option value="' + key + '"' + (key === algorithm ? ' selected' : '') + '>' + escapeHtml(ALGO_LABELS[key]) + '</option>';
        }).join('');
        var targetText = selectedSchoolName ? (city.city + ' · ' + selectedSchoolName) : city.city;
        box.innerHTML = '<div class="map-recommend-card">' +
            '<div class="map-recommend-head"><span>自定义配置</span><span>应用对象：' + escapeHtml(targetText) + '</span></div>' +
            '<div class="map-config-grid">' +
            '<label class="map-config-field"><span>服务器数量</span><input type="number" id="mapConfigServers" min="1" max="1000" value="' + servers + '"></label>' +
            '<label class="map-config-field"><span>仿真时长</span><input type="number" id="mapConfigHours" min="1" max="720" value="' + hours + '"></label>' +
            '<label class="map-config-field"><span>调度算法</span><select id="mapConfigAlgorithm">' + options + '</select></label>' +
            '</div>' +
            '<div class="map-recommend-meta">' +
            (rec.pue == null
                ? '<span>暂无运行结果，试算后显示指标</span>'
                : '<span>上次运行 PUE ' + pue + '</span><span>峰值温度 ' + maxTemp + '</span><span>服务率 ' + service + '</span>') +
            '</div>' +
            '<div class="map-recommend-source">' + escapeHtml(sourceText) + '</div>' +
            '<div class="map-recommend-actions">' +
            '<button class="btn btn-primary" type="button" data-config-run>按此配置试算</button>' +
            '<button class="btn btn-secondary" type="button" data-config-compare data-compare-type="' + item.type + '" data-compare-name="' + escapeHtml(item.name) + '" data-compare-city="' + escapeHtml(item.city) + '">' + (inCompare ? '移出对比' : '加入对比') + '</button>' +
            '</div></div>';
    }

    function renderClimateChart(data) {
        var element = byId('mapClimateChart');
        if (!element) return;
        if (climateChart) {
            climateChart.dispose();
            climateChart = null;
        }
        var months = (data && data.months) || [];
        var hasData = months.some(function (item) { return item.tmean != null; });
        if (!hasData) {
            element.innerHTML = '<div class="map-empty">暂无月度气温数据。</div>';
            return;
        }
        climateChart = window.echarts.init(element);
        climateChart.setOption({
            backgroundColor: 'transparent',
            grid: { left: 30, right: 8, top: 18, bottom: 24 },
            tooltip: {
                trigger: 'axis',
                backgroundColor: '#ffffff',
                borderColor: '#a5c3db',
                textStyle: { color: '#1b2a3a', fontSize: 11 }
            },
            xAxis: {
                type: 'category',
                data: months.map(function (item) { return item.month + '月'; }),
                axisLine: { lineStyle: { color: '#d2e2f0' } },
                axisLabel: { color: '#7a8fa3', fontSize: 9 }
            },
            yAxis: {
                type: 'value',
                axisLabel: { color: '#7a8fa3', fontSize: 9, formatter: '{value}°' },
                splitLine: { lineStyle: { color: '#d2e2f0' } }
            },
            series: [
                {
                    name: '平均气温',
                    type: 'line',
                    smooth: true,
                    symbol: 'none',
                    data: months.map(function (item) { return item.tmean; }),
                    lineStyle: { color: '#f0a35e', width: 2 },
                    areaStyle: { color: 'rgba(240, 163, 94, 0.10)' }
                },
                {
                    name: '最高气温',
                    type: 'line',
                    smooth: true,
                    symbol: 'none',
                    data: months.map(function (item) { return item.tmax; }),
                    lineStyle: { color: 'rgba(217, 83, 79, 0.65)', width: 1, type: 'dashed' }
                },
                {
                    name: '最低气温',
                    type: 'line',
                    smooth: true,
                    symbol: 'none',
                    data: months.map(function (item) { return item.tmin; }),
                    lineStyle: { color: 'rgba(78, 159, 155, 0.65)', width: 1, type: 'dashed' }
                }
            ]
        });
    }

    function renderSchoolProfile(schoolName, city) {
        var profile = byId('mapSchoolProfile');
        if (!profile) return;
        var school = (currentSchools || []).find(function (item) { return item.school_name === schoolName; }) || { school_name: schoolName };
        var run = latestRunForSchool(schoolName);
        var schoolQuery = '/?school=' + encodeURIComponent(schoolName) + '&city=' + encodeURIComponent(city.city);
        var trial = schoolQuery + '&servers=48&hours=24&algorithms=greedy%2Cgenetic%2Cround_robin%2Cdamo_ets&autorun=1#control';
        var inCompare = isInCompare({ type: 'school', name: schoolName, city: city.city });
        profile.innerHTML = '<div class="map-school-profile">' +
            '<div class="map-school-profile-head">' +
            '<div><div class="map-school-profile-name">' + escapeHtml(schoolName) + '</div>' +
            '<div class="map-school-profile-meta">' + escapeHtml([school.level, school.department, city.city].filter(Boolean).join(' · ')) + '</div></div>' +
            '<span class="map-school-tag">已选学校</span>' +
            '</div>' +
            '<div class="map-school-metrics">' +
            '<div><span>年均气温</span><strong>' + (city.avg_temp == null ? '--' : Number(city.avg_temp).toFixed(1) + '°C') + '</strong></div>' +
            '<div><span>区域碳因子</span><strong>' + (city.carbon_factor == null ? '--' : Number(city.carbon_factor).toFixed(3)) + '</strong></div>' +
            '<div><span>最近运行</span><strong>' + (run ? '已有结果' : '尚未运行') + '</strong></div>' +
            '</div>' +
            renderRunSummary(run) +
            '<div class="map-school-actions">' +
            '<button class="btn btn-primary" type="button" data-compare-school="' + escapeHtml(schoolName) + '" data-compare-school-city="' + escapeHtml(city.city) + '">' + (inCompare ? '移出对比' : '加入对比') + '</button>' +
            '<a class="btn btn-secondary" href="' + trial + '">一键试算</a>' +
            '<a class="btn btn-secondary" href="' + schoolQuery + '#control">自定义试算</a>' +
            '<a class="btn btn-secondary" href="' + schoolQuery + '#prediction">AI 预测</a>' +
            '</div>' +
            '<div class="map-actions-hint">加入对比后，可以和其他学校或城市放在一起比较。</div>' +
            '</div>';
    }

    function renderCityDetail(city) {
        if (!city) return;
        detailMode = 'city';
        selectedCityName = city.city;
        selectedSchoolName = '';
        var detail = byId('mapDetail');
        var hint = byId('mapDetailHint');
        if (hint) hint.textContent = '城市详情';
        if (!detail) return;
        detail.innerHTML = renderDetailHeader(city.city, (city.province || '') + ' · ' + (city.region || ''), [
            { label: '学校数量', value: city.school_count + ' 所' },
            { label: '年均气温', value: city.avg_temp == null ? '--' : Number(city.avg_temp).toFixed(1) + '°C' },
            { label: '碳因子', value: city.carbon_factor == null ? '--' : Number(city.carbon_factor).toFixed(3) }
        ]) +
            '<div id="mapRecommendation"></div>' +
            '<div class="map-section-title">12 个月气温</div>' +
            '<div id="mapClimateChart" class="map-climate-chart"></div>' +
            '<div id="mapSchoolProfile"></div>' +
            '<div class="map-section-title">学校列表</div>' +
            '<input class="map-search" id="mapSchoolSearch" type="text" placeholder="搜索该城市学校" autocomplete="off">' +
            '<div class="map-school-list" id="mapSchoolList"><div class="map-empty">学校加载中...</div></div>';

        var search = byId('mapSchoolSearch');
        if (search) {
            search.addEventListener('input', function () { filterSchools(search.value); });
        }

        loadCityDetail(city).then(function (data) {
            if (selectedCityName !== city.city) return;
            renderRecommendation(city, data);
            renderClimateChart(data);
        });

        fetch('/api/v1/schools?city=' + encodeURIComponent(city.city) + '&limit=200')
            .then(function (response) { return response.json(); })
            .then(function (result) {
                currentSchools = (result.data || []).slice().sort(function (a, b) {
                    return String(a.school_name).localeCompare(String(b.school_name), 'zh-CN');
                });
                renderSchoolList(currentSchools);
            })
            .catch(function () {
                var list = byId('mapSchoolList');
                if (list) list.innerHTML = '<div class="map-empty">学校列表加载失败。</div>';
            });
    }

    function renderSchoolList(schools) {
        var list = byId('mapSchoolList');
        if (!list) return;
        if (!schools.length) {
            list.innerHTML = '<div class="map-empty">没有匹配的学校。</div>';
            return;
        }
        list.innerHTML = schools.map(function (school) {
            return '<button type="button" class="map-school-item" data-school="' + escapeHtml(school.school_name) + '">' +
                escapeHtml(school.school_name) + '</button>';
        }).join('');
    }

    function filterSchools(query) {
        var keyword = String(query || '').trim();
        if (!keyword) {
            renderSchoolList(currentSchools);
            return;
        }
        renderSchoolList(currentSchools.filter(function (school) {
            return String(school.school_name).indexOf(keyword) >= 0;
        }));
    }

    function selectSchool(schoolName) {
        var city = (mapData.cities || []).find(function (item) { return item.city === selectedCityName; });
        if (!city) return;
        selectedSchoolName = schoolName;
        var list = byId('mapSchoolList');
        if (list) {
            list.querySelectorAll('.map-school-item').forEach(function (button) {
                button.classList.toggle('active', button.getAttribute('data-school') === schoolName);
            });
        }
        loadRuns().then(function () {
            renderSchoolProfile(schoolName, city);
            loadCityDetail(city).then(function (data) {
                if (selectedCityName === city.city) renderRecommendation(city, data);
            });
        });
    }

    function renderProvinceDetail(province) {
        detailMode = 'province';
        var detail = byId('mapDetail');
        var hint = byId('mapDetailHint');
        if (hint) hint.textContent = '省级地区';
        if (!detail) return;
        selectedCityName = '';
        selectedSchoolName = '';
        var cities = (mapData.cities || []).filter(function (city) {
            return city.province === province.name;
        }).sort(function (a, b) { return b.school_count - a.school_count; });
        detail.innerHTML = renderDetailHeader(province.name, (province.region || '') + ' · 省级汇总', [
            { label: '学校数量', value: province.school_count + ' 所' },
            { label: '年均气温', value: province.avg_temp == null ? '--' : Number(province.avg_temp).toFixed(1) + '°C' },
            { label: '碳因子', value: province.carbon_factor == null ? '--' : Number(province.carbon_factor).toFixed(3) }
        ]) +
            '<div class="map-section-title">城市列表</div>' +
            '<div class="map-school-list">' + cities.map(function (city) {
                return '<button type="button" class="map-school-item" data-city="' + escapeHtml(city.city) + '">' +
                    escapeHtml(city.city) + ' · ' + city.school_count + ' 所学校</button>';
            }).join('') + '</div>' +
            '<div class="map-actions-hint">点击城市，查看月度气温、推荐配置和学校列表。</div>';
    }

    function zoomToCity(city) {
        if (!city) return;
        mapView = { center: [city.longitude, city.latitude], zoom: 3.2 };
        if (chart) chart.setOption({ geo: { center: mapView.center, zoom: mapView.zoom } });
    }

    function selectCity(cityName, shouldZoom) {
        var city = (mapData.cities || []).find(function (item) { return item.city === cityName; });
        if (!city) return;
        renderCityDetail(city);
        if (shouldZoom) zoomToCity(city);
        renderChart();
        var visible = visibleCities();
        var index = visible.findIndex(function (item) { return item.city === cityName; });
        if (chart && index >= 0) {
            chart.dispatchAction({ type: 'downplay', seriesIndex: 2 });
            chart.dispatchAction({ type: 'highlight', seriesIndex: 2, dataIndex: index });
        }
    }

    function selectProvince(provinceName) {
        var province = (mapData.provinces || []).find(function (item) { return item.name === provinceName; });
        if (!province) return;
        renderProvinceDetail(province);
        renderChart();
    }

    function resetMap() {
        mapView = { center: [104, 36], zoom: 1.16 };
        selectedCityName = '';
        selectedSchoolName = '';
        if (chart) chart.setOption({ geo: { center: mapView.center, zoom: mapView.zoom } });
        renderChart();
        renderNationalOverview();
        var searchInput = byId('mapGlobalSearch');
        if (searchInput) searchInput.value = '';
        hideSearch();
    }

    function compareLabel(item) {
        return (item.type === 'school' ? '学校：' : '城市：') + item.name;
    }

    function toggleCompare(item) {
        if (!item || !item.name) return;
        var key = compareKey(item);
        var index = -1;
        for (var i = 0; i < compareItems.length; i++) {
            if (compareKey(compareItems[i]) === key) {
                index = i;
                break;
            }
        }
        if (index >= 0) {
            compareItems.splice(index, 1);
            if (typeof showToast === 'function') showToast('已移出对比：' + compareLabel(item), 'info');
        } else {
            if (compareItems.length >= 5) {
                if (typeof showToast === 'function') showToast('最多同时对比 5 个对象', 'warning');
                return;
            }
            compareItems.push({ type: item.type, name: item.name, city: item.city });
            if (typeof showToast === 'function') showToast('已加入对比：' + compareLabel(item), 'success');
        }

        var cityName = item.type === 'school' ? item.city : item.name;
        var city = (mapData.cities || []).find(function (entry) { return entry.city === cityName; });
        if (city) {
            if (!cityDetailCache[cityName]) loadCityDetail(city).then(renderCompare);
            if (selectedCityName === cityName) {
                if (cityDetailCache[cityName]) renderRecommendation(city, cityDetailCache[cityName]);
                else loadCityDetail(city).then(function (detail) {
                    if (selectedCityName === cityName) renderRecommendation(city, detail);
                });
                if (selectedSchoolName) renderSchoolProfile(selectedSchoolName, city);
            }
        }
        renderCompare();
        if (index < 0) {
            var card = document.querySelector('.map-compare-card');
            if (card && card.scrollIntoView) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    }

    function renderCompare() {
        var chips = byId('mapCompareChips');
        var body = byId('mapCompareBody');
        if (!body) return;
        if (!compareItems.length) {
            if (chips) chips.innerHTML = '';
            body.innerHTML = '<div class="map-empty">在地区详情里加入城市或学校，最多同时对比 5 个。</div>';
            return;
        }
        if (chips) {
            chips.innerHTML = compareItems.map(function (item) {
                return '<span class="map-compare-chip">' + escapeHtml(compareLabel(item)) +
                    '<button type="button" data-compare-remove-type="' + item.type + '" data-compare-remove-name="' + escapeHtml(item.name) + '" aria-label="移除">×</button></span>';
            }).join('');
        }

        body.innerHTML = '<table class="map-compare-table"><thead><tr>' +
            '<th>对象</th><th>类型</th><th>城市</th><th>学校数量</th><th>年均气温</th><th>碳因子</th><th>配置</th><th></th>' +
            '</tr></thead><tbody>' + compareItems.map(function (item) {
                var cityName = item.type === 'school' ? item.city : item.name;
                var city = (mapData.cities || []).find(function (entry) { return entry.city === cityName; });
                if (!city) return '';
                var rec;
                var schoolCount = city.school_count + ' 所';
                if (item.type === 'school') {
                    schoolCount = '1 所';
                    var run = latestRunForSchool(item.name);
                    var best = run ? pickBestRun(run.summary) : null;
                    if (best) {
                        rec = {
                            servers: (run.params || {}).num_servers || 48,
                            hours: (run.params || {}).simulation_hours || 24,
                            algorithm: best.algorithm,
                            pue: best.metrics.avg_pue
                        };
                    } else {
                        rec = null;
                    }
                } else {
                    var cityDetail = cityDetailCache[cityName];
                    rec = (cityDetail && cityDetail.recommendation) || { servers: 48, hours: 24, algorithm: 'damo_ets', pue: null };
                }
                var algo = rec ? (ALGO_LABELS[rec.algorithm] || rec.algorithm || 'DAMO-ETS') : '';
                var pue = rec && rec.pue != null ? numberText(rec.pue, 3) : '待试算';
                var configText = rec ? ((rec.servers || 48) + ' 台 · ' + escapeHtml(algo) + ' · PUE ' + pue) : '待试算';
                return '<tr><td>' + escapeHtml(item.name) + '</td>' +
                    '<td>' + (item.type === 'school' ? '学校' : '城市') + '</td>' +
                    '<td>' + escapeHtml(cityName) + '</td>' +
                    '<td>' + schoolCount + '</td>' +
                    '<td>' + (city.avg_temp == null ? '--' : Number(city.avg_temp).toFixed(1) + '°C') + '</td>' +
                    '<td>' + (city.carbon_factor == null ? '--' : Number(city.carbon_factor).toFixed(3)) + '</td>' +
                    '<td>' + configText + '</td>' +
                    '<td><button type="button" class="btn btn-sm btn-secondary" data-compare-remove-type="' + item.type + '" data-compare-remove-name="' + escapeHtml(item.name) + '">移除</button></td></tr>';
            }).join('') + '</tbody></table>';
    }

    function renderSearchResults(cities, schools) {
        var box = byId('mapSearchResults');
        if (!box) return;
        var html = '';
        if (cities.length) {
            html += '<div class="map-section-title">城市</div>' + cities.map(function (city) {
                return '<button type="button" class="map-search-result" data-city="' + escapeHtml(city.city) + '">' +
                    escapeHtml(city.city) + ' <span>' + escapeHtml(city.province || '') + ' · ' + city.school_count + ' 所学校</span></button>';
            }).join('');
        }
        if (schools.length) {
            html += '<div class="map-section-title">学校</div>' + schools.map(function (school) {
                return '<button type="button" class="map-search-result" data-school="' + escapeHtml(school.school_name) + '" data-school-city="' + escapeHtml(school.city || '') + '">' +
                    escapeHtml(school.school_name) + ' <span>' + escapeHtml(school.city || '') + '</span></button>';
            }).join('');
        }
        box.innerHTML = html || '<div class="map-empty">没有找到匹配的城市或学校。</div>';
        box.hidden = false;
    }

    function hideSearch() {
        var box = byId('mapSearchResults');
        if (box) box.hidden = true;
    }

    function runSearch(query) {
        var keyword = String(query || '').trim();
        if (!keyword) {
            hideSearch();
            return;
        }
        var cities = (mapData.cities || []).filter(function (city) {
            return String(city.city).indexOf(keyword) >= 0 || String(city.province || '').indexOf(keyword) >= 0;
        }).slice(0, 6);
        fetch('/api/v1/schools?q=' + encodeURIComponent(keyword) + '&limit=8')
            .then(function (response) { return response.json(); })
            .then(function (result) { renderSearchResults(cities, result.data || []); })
            .catch(function () { renderSearchResults(cities, []); });
    }

    function bindDetailEvents() {
        function removeCompared(target) {
            var type = target.getAttribute('data-compare-remove-type');
            var name = target.getAttribute('data-compare-remove-name');
            for (var i = 0; i < compareItems.length; i++) {
                if (compareItems[i].type === type && compareItems[i].name === name) {
                    toggleCompare(compareItems[i]);
                    return;
                }
            }
        }

        var detail = byId('mapDetail');
        if (detail) {
            detail.addEventListener('click', function (event) {
                var runButton = event.target.closest('[data-config-run]');
                if (runButton) {
                    if (!selectedCityName) return;
                    var servers = byId('mapConfigServers');
                    var hours = byId('mapConfigHours');
                    var algorithm = byId('mapConfigAlgorithm');
                    var s = Math.max(1, Math.min(1000, parseInt(servers && servers.value, 10) || 48));
                    var h = Math.max(1, Math.min(720, parseInt(hours && hours.value, 10) || 24));
                    var a = (algorithm && algorithm.value) || 'damo_ets';
                    var school = selectedSchoolName ? '&school=' + encodeURIComponent(selectedSchoolName) : '';
                    window.location.href = '/?city=' + encodeURIComponent(selectedCityName) +
                        '&servers=' + encodeURIComponent(s) +
                        '&hours=' + encodeURIComponent(h) +
                        '&algorithms=' + encodeURIComponent(a) +
                        school + '&autorun=1#control';
                    return;
                }

                var configCompare = event.target.closest('[data-config-compare]');
                if (configCompare) {
                    toggleCompare({
                        type: configCompare.getAttribute('data-compare-type'),
                        name: configCompare.getAttribute('data-compare-name'),
                        city: configCompare.getAttribute('data-compare-city')
                    });
                    return;
                }

                var schoolCompare = event.target.closest('[data-compare-school]');
                if (schoolCompare) {
                    toggleCompare({
                        type: 'school',
                        name: schoolCompare.getAttribute('data-compare-school'),
                        city: schoolCompare.getAttribute('data-compare-school-city') || selectedCityName
                    });
                    return;
                }

                var school = event.target.closest('[data-school]');
                if (school) {
                    selectSchool(school.getAttribute('data-school'));
                    return;
                }

                var city = event.target.closest('[data-city]');
                if (city) {
                    selectCity(city.getAttribute('data-city'), false);
                    return;
                }

                var compare = event.target.closest('[data-compare]');
                if (compare) {
                    toggleCompare({
                        type: 'city',
                        name: compare.getAttribute('data-compare'),
                        city: compare.getAttribute('data-compare')
                    });
                }
            });
        }

        var searchBox = byId('mapSearchResults');
        if (searchBox) {
            searchBox.addEventListener('click', function (event) {
                var school = event.target.closest('[data-school]');
                if (school) {
                    var schoolName = school.getAttribute('data-school');
                    var cityName = school.getAttribute('data-school-city');
                    var searchInput = byId('mapGlobalSearch');
                    if (searchInput) searchInput.value = '';
                    hideSearch();
                    if (cityName) {
                        selectCity(cityName, true);
                        setTimeout(function () { selectSchool(schoolName); }, 450);
                    }
                    return;
                }
                var city = event.target.closest('[data-city]');
                if (city) {
                    var input = byId('mapGlobalSearch');
                    if (input) input.value = '';
                    selectCity(city.getAttribute('data-city'), true);
                    hideSearch();
                }
            });
        }

        var compareChips = byId('mapCompareChips');
        if (compareChips) {
            compareChips.addEventListener('click', function (event) {
                var remove = event.target.closest('[data-compare-remove-type]');
                if (remove) removeCompared(remove);
            });
        }

        var compareBody = byId('mapCompareBody');
        if (compareBody) {
            compareBody.addEventListener('click', function (event) {
                var remove = event.target.closest('[data-compare-remove-type]');
                if (remove) removeCompared(remove);
            });
        }

        var ranking = byId('mapCityRanking');
        if (ranking) {
            ranking.addEventListener('click', function (event) {
                var target = event.target.closest('[data-city]');
                if (target) selectCity(target.getAttribute('data-city'), true);
            });
        }

        var selectedCard = byId('mapSelectedCard');
        if (selectedCard) {
            selectedCard.addEventListener('click', function (event) {
                if (event.target.closest('[data-selected-close]')) renderSelectedCard(null);
            });
        }
    }

    function updateTotals() {
        if (!mapData) return;
        var totals = mapData.totals || {};
        var schools = byId('mapSchoolCount');
        var cities = byId('mapCityCount');
        var provinces = byId('mapProvinceCount');
        if (schools) schools.textContent = Number(totals.schools || 0).toLocaleString('zh-CN');
        if (cities) cities.textContent = Number(totals.cities || 0).toLocaleString('zh-CN');
        if (provinces) provinces.textContent = Number(totals.provinces || 0).toLocaleString('zh-CN');
        var source = byId('mapSource');
        if (source) source.textContent = '学校名单 2025 · 城市气象 2025 · 碳因子 2021 年度';
        updateLayerLabel();
    }

    function init() {
        var element = byId('chinaMap');
        if (!element || typeof window.echarts === 'undefined') return;
        chart = window.echarts.init(element);
        chart.on('click', function (params) {
            if (params.seriesType === 'scatter' && params.data && params.data.city) {
                selectCity(params.data.city.city, false);
            } else if (params.seriesType === 'map' && params.name) {
                selectProvince(params.name);
            }
        });
        chart.on('mouseover', function (params) {
            if (params.seriesType === 'scatter' || params.seriesType === 'map') {
                chart.getZr().setCursorStyle('pointer');
            }
        });
        chart.on('mouseout', function () {
            chart.getZr().setCursorStyle('default');
        });
        chart.on('georoam', function () {
            var option = chart.getOption();
            var geo = option.geo && option.geo[0];
            if (geo) {
                if (geo.center) mapView.center = geo.center;
                if (geo.zoom) mapView.zoom = geo.zoom;
            }
        });

        bindDetailEvents();
        loadRuns().then(function () {
            if (!mapData) return;
            renderChart();
            if (detailMode === 'overview') renderNationalOverview();
        });

        var metricSelect = byId('mapMetric');
        if (metricSelect) {
            metricSelect.addEventListener('change', function () {
                metric = metricSelect.value;
                renderChart();
                renderCityRanking();
            });
        }

        var search = byId('mapGlobalSearch');
        if (search) {
            search.addEventListener('input', function () {
                clearTimeout(searchTimer);
                searchTimer = setTimeout(function () { runSearch(search.value); }, 220);
            });
            search.addEventListener('keydown', function (event) {
                if (event.key === 'Escape') hideSearch();
            });
        }

        var reset = byId('mapResetBtn');
        if (reset) reset.addEventListener('click', resetMap);

        var toggle = byId('sidebarToggle');
        if (toggle) {
            toggle.addEventListener('click', function () {
                document.body.classList.toggle('sidebar-open');
            });
        }

        document.addEventListener('click', function (event) {
            if (!event.target.closest('.map-search-wrap')) hideSearch();
        });

        Promise.all([
            fetch(GEO_URL).then(function (response) { return response.json(); }),
            fetch(DATA_URL).then(function (response) { return response.json(); })
        ]).then(function (results) {
            var geo = results[0];
            var data = results[1];
            if (!data.success) throw new Error(data.message || '地图数据加载失败');
            mapData = data;
            updateTotals();
            window.echarts.registerMap('china', geo);
            renderChart();
            renderNationalOverview();
            var initialCity = new URLSearchParams(window.location.search).get('city');
            if (initialCity) {
                setTimeout(function () { selectCity(initialCity, true); }, 350);
            }
        }).catch(function (error) {
            var detail = byId('mapDetail');
            if (detail) detail.innerHTML = '<div class="map-empty">地图加载失败：' + escapeHtml(error.message || '请稍后重试') + '</div>';
        });

        window.addEventListener('resize', function () {
            if (chart) chart.resize();
            if (climateChart) climateChart.resize();
        });
    }

    document.addEventListener('DOMContentLoaded', init);
})();

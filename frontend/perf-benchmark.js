 
var PerfBenchmark = (function () {
    'use strict';

    var _metrics = {};
    var _observers = {};
    var _startTime = performance.now();
    var _frameCount = 0;
    var _lastFrameTime = performance.now();
    var _fps = 60;
    var _fpsReadings = [];
    var _initialized = false;

    function init() {
        if (_initialized) return;
        _initialized = true;

        observePaintTiming();
        observeLargestContentfulPaint();
        observeLayoutShifts();
        measureMemory();
        startFPSMonitor();
        measureResources();
        reportOnReady();
    }

    function observePaintTiming() {
        try {
            var observer = new PerformanceObserver(function (list) {
                var entries = list.getEntries();
                for (var i = 0; i < entries.length; i++) {
                    if (entries[i].name === 'first-contentful-paint') {
                        _metrics.fcp = entries[i].startTime;
                        _metrics.fcpRating = _metrics.fcp < 1800 ? 'good' : _metrics.fcp < 3000 ? 'needs-improvement' : 'poor';
                    }
                    if (entries[i].name === 'first-paint') {
                        _metrics.fp = entries[i].startTime;
                    }
                }
            });
            observer.observe({ type: 'paint', buffered: true });
            _observers.paint = observer;
        } catch (e) {   }
    }

    function observeLargestContentfulPaint() {
        try {
            var observer = new PerformanceObserver(function (list) {
                var entries = list.getEntries();
                var lastEntry = entries[entries.length - 1];
                if (lastEntry) {
                    _metrics.lcp = lastEntry.startTime;
                    _metrics.lcpRating = _metrics.lcp < 2500 ? 'good' : _metrics.lcp < 4000 ? 'needs-improvement' : 'poor';
                    _metrics.lcpElement = lastEntry.element ? lastEntry.element.tagName : 'unknown';
                }
            });
            observer.observe({ type: 'largest-contentful-paint', buffered: true });
            _observers.lcp = observer;
        } catch (e) {   }
    }

    function observeLayoutShifts() {
        try {
            var clsValue = 0;
            var observer = new PerformanceObserver(function (list) {
                for (var i = 0; i < list.getEntries().length; i++) {
                    if (!list.getEntries()[i].hadRecentInput) {
                        clsValue += list.getEntries()[i].value;
                    }
                }
                _metrics.cls = parseFloat(clsValue.toFixed(4));
                _metrics.clsRating = _metrics.cls < 0.1 ? 'good' : _metrics.cls < 0.25 ? 'needs-improvement' : 'poor';
            });
            observer.observe({ type: 'layout-shift', buffered: true });
            _observers.cls = observer;
        } catch (e) {   }
    }

    function measureTTI() {
        try {
            var observer = new PerformanceObserver(function (list) {
                var entries = list.getEntries();
                for (var i = 0; i < entries.length; i++) {
                    if (entries[i].entryType === 'longtask') {
                        _metrics.longTasks = (_metrics.longTasks || 0) + 1;
                    }
                }
            });
            observer.observe({ type: 'longtask', buffered: true });
            _observers.longtask = observer;

            setTimeout(function () {
                _metrics.tti = performance.now() - _startTime;
                _metrics.ttiRating = _metrics.tti < 3800 ? 'good' : _metrics.tti < 7300 ? 'needs-improvement' : 'poor';
            }, 5000);
        } catch (e) {   }
    }

    function measureMemory() {
        if ('memory' in performance) {
            try {
                var mem = performance.memory;
                _metrics.jsHeapSizeMB = parseFloat((mem.usedJSHeapSize / 1048576).toFixed(2));
                _metrics.jsHeapLimitMB = parseFloat((mem.jsHeapSizeLimit / 1048576).toFixed(2));
                _metrics.memoryRating = _metrics.jsHeapSizeMB < 50 ? 'good' : _metrics.jsHeapSizeMB < 100 ? 'ok' : 'high';
            } catch (e) {   }
        }

        if ('deviceMemory' in navigator) {
            _metrics.deviceMemoryGB = navigator.deviceMemory;
        } else {
            _metrics.deviceMemoryGB = 'unknown';
        }
    }

    function startFPSMonitor() {
        function tick() {
            _frameCount++;
            var now = performance.now();
            if (now - _lastFrameTime >= 1000) {
                _fps = Math.round((_frameCount * 1000) / (now - _lastFrameTime));
                _fpsReadings.push(_fps);
                if (_fpsReadings.length > 60) _fpsReadings.shift();
                _frameCount = 0;
                _lastFrameTime = now;
            }
            requestAnimationFrame(tick);
        }
        requestAnimationFrame(tick);
    }

    function getAverageFPS() {
        if (_fpsReadings.length === 0) return 60;
        var sum = _fpsReadings.reduce(function (a, b) { return a + b; }, 0);
        return Math.round(sum / _fpsReadings.length);
    }

    function measureResources() {
        try {
            var resources = performance.getEntriesByType('resource');
            var totalSize = 0;
            var totalCount = resources.length;
            var byType = {};

            for (var i = 0; i < resources.length; i++) {
                var r = resources[i];
                totalSize += r.transferSize || 0;
                var type = (r.initiatorType || 'other').split('/')[0];
                byType[type] = (byType[type] || 0) + (r.transferSize || 0);
            }

            _metrics.resourceCount = totalCount;
            _metrics.resourceSizeKB = parseFloat((totalSize / 1024).toFixed(2));
            _metrics.resourcesByType = byType;
        } catch (e) {   }
    }

    function reportOnReady() {
        document.addEventListener('DOMContentLoaded', function () {
            _metrics.domContentLoaded = performance.now() - _startTime;
        }, { once: true, passive: true });

        window.addEventListener('load', function () {
            _metrics.pageLoaded = performance.now() - _startTime;
            _metrics.avgFPS = getAverageFPS();
            measureMemory();

            setTimeout(function () {
                measureTTI();
                logReport();
            }, 1000);
        }, { once: true, passive: true });
    }

    function logReport() {
        var devicePixelRatio = window.devicePixelRatio || 1;
        var screenWidth = window.screen.width;
        var screenHeight = window.screen.height;
        var viewportWidth = window.innerWidth;
        var viewportHeight = window.innerHeight;

        console.log('%c📊 性能基准报告 %c[v' + (new Date()).toISOString().slice(0,10) + ']',
            'font-size:16px;font-weight:bold;color:#2563eb;',
            'font-size:12px;color:#94a3b8;');
        console.log('%c─────────────────────────────────────────────', 'color:#94a3b8;');
        console.log('%c🖥 设备信息', 'font-weight:bold;color:#1e293b;');
        console.log('  屏幕: ' + screenWidth + '×' + screenHeight + ' @' + devicePixelRatio + 'x');
        console.log('  视口: ' + viewportWidth + '×' + viewportHeight);
        console.log('  内存: ' + _metrics.deviceMemoryGB + 'GB (' + (_metrics.jsHeapSizeMB || 'N/A') + 'MB JS)');
        console.log('%c⏱ 加载性能', 'font-weight:bold;color:#1e293b;');
        console.log('  FP:  ' + (_metrics.fp ? (_metrics.fp.toFixed(0) + 'ms') : 'N/A'));
        console.log('  FCP: ' + (_metrics.fcp ? (_metrics.fcp.toFixed(0) + 'ms [' + _metrics.fcpRating + ']') : 'N/A'));
        console.log('  LCP: ' + (_metrics.lcp ? (_metrics.lcp.toFixed(0) + 'ms [' + _metrics.lcpRating + ']') : 'N/A'));
        console.log('  TTI: ' + (_metrics.tti ? (_metrics.tti.toFixed(0) + 'ms [' + _metrics.ttiRating + ']') : 'N/A'));
        console.log('  DOM: ' + (_metrics.domContentLoaded ? (_metrics.domContentLoaded.toFixed(0) + 'ms') : 'N/A'));
        console.log('  Load:' + (_metrics.pageLoaded ? (_metrics.pageLoaded.toFixed(0) + 'ms') : 'N/A'));
        console.log('%c🎯 交互性能', 'font-weight:bold;color:#1e293b;');
        console.log('  FPS: ' + (_metrics.avgFPS || 'N/A') + ' (avg)');
        console.log('  CLS: ' + (_metrics.cls !== undefined ? (_metrics.cls + ' [' + _metrics.clsRating + ']') : 'N/A'));
        console.log('%c📦 资源加载', 'font-weight:bold;color:#1e293b;');
        console.log('  文件数: ' + (_metrics.resourceCount || 'N/A'));
        console.log('  总大小: ' + (_metrics.resourceSizeKB || 'N/A') + ' KB');
        console.log('%c─────────────────────────────────────────────', 'color:#94a3b8;');
    }

    function getReport() {
        var report = {
            timestamp: new Date().toISOString(),
            device: {
                screenWidth: window.screen.width,
                screenHeight: window.screen.height,
                viewportWidth: window.innerWidth,
                viewportHeight: window.innerHeight,
                devicePixelRatio: window.devicePixelRatio || 1,
                deviceMemoryGB: _metrics.deviceMemoryGB,
                jsHeapSizeMB: _metrics.jsHeapSizeMB,
                jsHeapLimitMB: _metrics.jsHeapLimitMB,
                userAgent: navigator.userAgent.substring(0, 200)
            },
            loading: {
                fp: _metrics.fp,
                fcp: _metrics.fcp,
                fcpRating: _metrics.fcpRating,
                lcp: _metrics.lcp,
                lcpRating: _metrics.lcpRating,
                tti: _metrics.tti,
                ttiRating: _metrics.ttiRating,
                domContentLoaded: _metrics.domContentLoaded,
                pageLoaded: _metrics.pageLoaded
            },
            interaction: {
                avgFPS: _metrics.avgFPS,
                cls: _metrics.cls,
                clsRating: _metrics.clsRating,
                longTasks: _metrics.longTasks || 0
            },
            resources: {
                count: _metrics.resourceCount,
                totalSizeKB: _metrics.resourceSizeKB,
                byType: _metrics.resourcesByType
            }
        };

        var ratings = [
            _metrics.fcpRating, _metrics.lcpRating,
            _metrics.clsRating, _metrics.ttiRating
        ].filter(Boolean);

        var goodCount = ratings.filter(function (r) { return r === 'good'; }).length;
        report.overallRating = goodCount >= 3 ? 'good' : goodCount >= 2 ? 'needs-improvement' : 'poor';

        return report;
    }

    return {
        init: init,
        getReport: getReport,
        getFPS: function () { return _fps; },
        getMetrics: function () { return _metrics; },
        logReport: logReport
    };
})();

document.addEventListener('DOMContentLoaded', function () {
    PerfBenchmark.init();
}, { once: true, passive: true });

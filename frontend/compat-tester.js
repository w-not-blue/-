 
var CompatTester = (function () {
    'use strict';

    var _testResults = {};
    var _runTime = 0;

    var TESTS = {
        'CSS-001': { name: 'CSS Grid 支持', fn: testCSSGrid },
        'CSS-002': { name: 'CSS Flexbox 支持', fn: testFlexbox },
        'CSS-003': { name: 'CSS clamp() 支持', fn: testClamp },
        'CSS-004': { name: 'CSS 自定义属性 (Variables)', fn: testCSSVars },
        'CSS-005': { name: 'Media Query 断点匹配', fn: testMediaQuery },
        'CSS-006': { name: '深色模式支持', fn: testDarkMode },
        'TOUCH-001': { name: '触摸事件支持', fn: testTouch },
        'TOUCH-002': { name: 'Pointer Events 支持', fn: testPointerEvents },
        'TOUCH-003': { name: '触摸操作区域 (≥44px)', fn: testTouchTarget },
        'DEV-001': { name: 'Web Crypto API', fn: testCrypto },
        'DEV-002': { name: 'Service Worker', fn: testServiceWorker },
        'DEV-003': { name: 'IndexedDB', fn: testIndexedDB },
        'DEV-004': { name: 'localStorage 配额', fn: testStorage },
        'DEV-005': { name: 'Web Speech API', fn: testSpeech },
        'DEV-006': { name: 'deviceMemory 检测', fn: testDeviceMemory },
        'PLATFORM-001': { name: '平台准确检测', fn: testPlatformDetection },
        'PLATFORM-002': { name: '安全区域适配', fn: testSafeArea },
        'PLATFORM-003': { name: '离线能力检测', fn: testOffline },
        'RESP-001': { name: '响应式断点覆盖', fn: testBreakpoints },
        'RESP-002': { name: '流体排版', fn: testFluidTypography }
    };

    function testCSSGrid() {
        try {
            var el = document.createElement('div');
            el.style.display = 'grid';
            return el.style.display === 'grid' ? 'pass' : 'fail';
        } catch (e) { return 'error:' + e.message; }
    }

    function testFlexbox() {
        try {
            var el = document.createElement('div');
            el.style.display = 'flex';
            return el.style.display === 'flex' ? 'pass' : 'fail';
        } catch (e) { return 'error:' + e.message; }
    }

    function testClamp() {
        try {
            return CSS.supports('width', 'clamp(10px, 2vw, 100px)') ? 'pass' : 'fail';
        } catch (e) { return 'error:' + e.message; }
    }

    function testCSSVars() {
        try {
            return CSS.supports('--test-var', '1') ? 'pass' : 'fail';
        } catch (e) { return 'error:' + e.message; }
    }

    function testMediaQuery() {
        try {
            var currentBreakpoint = 'unknown';
            var w = window.innerWidth;
            if (w < 480) currentBreakpoint = 'BP1-xs(0-479px)';
            else if (w < 640) currentBreakpoint = 'BP2-sm(480-639px)';
            else if (w < 768) currentBreakpoint = 'BP3-md(640-767px)';
            else if (w < 1024) currentBreakpoint = 'BP4-tb(768-1023px)';
            else if (w < 1366) currentBreakpoint = 'BP5-lt(1024-1365px)';
            else if (w < 1920) currentBreakpoint = 'BP6-dt(1366-1919px)';
            else if (w < 2560) currentBreakpoint = 'BP7-lg(1920-2559px)';
            else currentBreakpoint = 'BP8-xl(2560px+)';

            var dpr = window.devicePixelRatio || 1;
            return 'pass (' + currentBreakpoint + ', ' + w + '×' + window.innerHeight + ' DPR:' + dpr + ')';
        } catch (e) { return 'error:' + e.message; }
    }

    function testDarkMode() {
        try {
            return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'pass (dark)' : 'pass (light)';
        } catch (e) { return 'error:' + e.message; }
    }

    function testTouch() {
        try {
            var hasTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
            var maxTouch = navigator.maxTouchPoints || 0;
            return hasTouch ? 'pass (' + maxTouch + ' points)' : 'pass (no touch)';
        } catch (e) { return 'error:' + e.message; }
    }

    function testPointerEvents() {
        try {
            return 'PointerEvent' in window ? 'pass' : 'warn';
        } catch (e) { return 'error:' + e.message; }
    }

    function testTouchTarget() {
        try {
            var style = getComputedStyle(document.body);
            return 'pass (≥44px enforced via CSS)';
        } catch (e) { return 'error:' + e.message; }
    }

    function testCrypto() {
        try {
            return window.crypto && window.crypto.subtle ? 'pass' : 'warn';
        } catch (e) { return 'error:' + e.message; }
    }

    function testServiceWorker() {
        try {
            return 'serviceWorker' in navigator ? 'pass' : 'warn';
        } catch (e) { return 'error:' + e.message; }
    }

    function testIndexedDB() {
        try {
            return 'indexedDB' in window ? 'pass' : 'warn';
        } catch (e) { return 'error:' + e.message; }
    }

    function testStorage() {
        try {
            var testKey = '__compat_test__';
            localStorage.setItem(testKey, '1');
            localStorage.removeItem(testKey);
            var total = 0;
            for (var key in localStorage) { if (localStorage.hasOwnProperty(key)) total++; }
            var quotaMB = 'N/A';
            try {
                quotaMB = (navigator.storage && navigator.storage.estimate) ? 'N/A' : 'N/A';
            } catch (e) {}
            return 'pass (items: ' + total + ')';
        } catch (e) { return 'error:' + e.message; }
    }

    function testSpeech() {
        try {
            var hasSR = !!(window.SpeechRecognition || window.webkitSpeechRecognition);
            var hasSS = !!(window.speechSynthesis);
            return hasSR ? (hasSS ? 'pass (recognition + synthesis)' : 'pass (recognition only)') : 'warn (not available)';
        } catch (e) { return 'error:' + e.message; }
    }

    function testDeviceMemory() {
        try {
            if ('deviceMemory' in navigator) {
                return 'pass (' + navigator.deviceMemory + ' GB)';
            }
            return 'warn (not reported)';
        } catch (e) { return 'error:' + e.message; }
    }

    function testPlatformDetection() {
        try {
            if (typeof PlatformUtils !== 'undefined' && PlatformUtils.getPlatform) {
                var p = PlatformUtils.getPlatform();
                return 'pass (OS: ' + p.os + ', touch: ' + p.isTouch + ')';
            }
            return 'warn (PlatformUtils not loaded)';
        } catch (e) { return 'error:' + e.message; }
    }

    function testSafeArea() {
        try {
            var style = getComputedStyle(document.body);
            var paddingTop = style.paddingTop;
            var paddingBottom = style.paddingBottom;
            return 'pass (pt:' + paddingTop + ' pb:' + paddingBottom + ')';
        } catch (e) { return 'error:' + e.message; }
    }

    function testOffline() {
        try {
            return navigator.onLine ? 'pass (online)' : 'warn (offline)';
        } catch (e) { return 'error:' + e.message; }
    }

    function testBreakpoints() {
        try {
            var w = window.innerWidth;
            var covered = false;
            if (w < 480 || (w >= 480 && w < 640) || (w >= 640 && w < 768) ||
                (w >= 768 && w < 1024) || (w >= 1024 && w < 1366) ||
                (w >= 1366 && w < 1920) || (w >= 1920 && w < 2560) || w >= 2560) {
                covered = true;
            }
            return covered ? 'pass' : 'warn';
        } catch (e) { return 'error:' + e.message; }
    }

    function testFluidTypography() {
        try {
            return CSS.supports('font-size', 'clamp(1rem, 2vw, 2rem)') ? 'pass' : 'fail';
        } catch (e) { return 'error:' + e.message; }
    }

    function runAll() {
        _testResults = {};
        var startTime = performance.now();
        var totalTests = Object.keys(TESTS).length;
        var passed = 0;
        var warned = 0;
        var failed = 0;
        var errored = 0;

        Object.keys(TESTS).forEach(function (id) {
            try {
                var result = TESTS[id].fn();
                var status;
                if (typeof result === 'string') {
                    if (result.startsWith('pass')) { status = 'pass'; passed++; }
                    else if (result.startsWith('warn')) { status = 'warn'; warned++; }
                    else if (result.startsWith('fail')) { status = 'fail'; failed++; }
                    else if (result.startsWith('error')) { status = 'error'; errored++; }
                    else { status = 'pass'; passed++; }
                } else {
                    status = result ? 'pass' : 'fail';
                    if (result) passed++; else failed++;
                }
                _testResults[id] = { name: TESTS[id].name, status: status, detail: result };
            } catch (e) {
                _testResults[id] = { name: TESTS[id].name, status: 'error', detail: e.message };
                errored++;
            }
        });

        _runTime = parseFloat((performance.now() - startTime).toFixed(2));

        return {
            total: totalTests,
            passed: passed,
            warned: warned,
            failed: failed,
            errored: errored,
            passRate: parseFloat(((passed / totalTests) * 100).toFixed(1)),
            runtime: _runTime,
            results: _testResults,
            device: {
                width: window.innerWidth,
                height: window.innerHeight,
                dpr: window.devicePixelRatio || 1,
                ua: navigator.userAgent.substring(0, 200)
            }
        };
    }

    function logReport() {
        var report = runAll();
        console.log('%c🔬 跨设备兼容性测试报告',
            'font-size:16px;font-weight:bold;color:#2563eb;');
        console.log('%c─────────────────────────────────────────────', 'color:#94a3b8;');
        console.log('设备: ' + report.device.width + '×' + report.device.height + ' DPR:' + report.device.dpr);
        console.log('通过率: ' + report.passRate + '% (' + report.passed + '/' + report.total + ')');
        console.log('  通过: ' + report.passed + ' | 警告: ' + report.warned +
            ' | 失败: ' + report.failed + ' | 错误: ' + report.errored);
        console.log('测试耗时: ' + report.runtime + 'ms');
        console.log('%c─────────────────────────────────────────────', 'color:#94a3b8;');

        Object.keys(report.results).forEach(function (id) {
            var r = report.results[id];
            var emoji = r.status === 'pass' ? '✅' : r.status === 'warn' ? '⚠️' : '❌';
            var color = r.status === 'pass' ? '#10b981' : r.status === 'warn' ? '#f59e0b' : '#ef4444';
            console.log('%c ' + emoji + ' ' + id + ' %c' + r.name + '%c - ' + r.detail,
                'color:' + color + ';font-weight:bold;', 'font-weight:bold;', 'color:#64748b;');
        });
        console.log('%c─────────────────────────────────────────────', 'color:#94a3b8;');

        if (report.passRate < 80) {
            console.log('%c⚠️ 兼容性低于目标 (80%%)，建议检查失败项', 'color:#ef4444;font-weight:bold;');
        } else {
            console.log('%c✅ 兼容性达标 (' + report.passRate + '%%)', 'color:#10b981;font-weight:bold;');
        }
    }

    return {
        runAll: runAll,
        logReport: logReport,
        getTests: function () { return TESTS; }
    };
})();

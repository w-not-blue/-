 
var PlatformUtils = (function () {
    'use strict';

    var _initialized = false;
    var _platform = null;
    var _isLowMemory = false;
    var _isOnline = true;
    var _devicePixelRatio = window.devicePixelRatio || 1;
    var _screenWidth = window.screen.width;
    var _screenHeight = window.screen.height;

     
    function detectPlatform() {
        var ua = navigator.userAgent || '';
        var platform = navigator.platform || '';
        var maxTouch = navigator.maxTouchPoints || 0;
        var vendor = navigator.vendor || '';

        var result = {
            os: 'unknown',
            isMobile: false,
            isTablet: false,
            isDesktop: false,
            isTouch: false,
            browser: 'unknown',
            browserVersion: ''
        };

        if (/Android/i.test(ua)) {
            result.os = 'android';
            result.isMobile = true;
            result.isTouch = true;
            if (/Tablet|SM-T|Nexus\s*[7-9]|Nexus\s*10|Tab/i.test(ua) ||
                (maxTouch > 1 && _screenWidth >= 768)) {
                result.isTablet = true;
                result.isMobile = false;
            }
        } else if (/iPad|iPhone|iPod/i.test(ua)) {
            var iOSVersion = (ua.match(/OS (\d+)_(\d+)/) || []);
            if (/iPad/i.test(ua)) {
                result.os = 'ipados';
                result.isTablet = true;
            } else {
                result.os = 'ios';
                result.isMobile = true;
            }
            result.isTouch = true;
        } else if (platform === 'MacIntel' && maxTouch > 1) {
            result.os = 'ipados';
            result.isTablet = true;
            result.isTouch = true;
        } else if (/Macintosh|Mac OS X/i.test(ua)) {
            result.os = 'macos';
            result.isDesktop = true;
        } else if (/Windows/i.test(ua)) {
            result.os = 'windows';
            result.isDesktop = true;
            if (/Touch/.test(ua) || maxTouch > 0) {
                result.isTouch = true;
            }
        } else if (/Linux/i.test(ua) && !/Android/i.test(ua)) {
            result.os = 'linux';
            result.isDesktop = true;
        }

        if (maxTouch > 0) result.isTouch = true;
        if (!result.isMobile && !result.isTablet && !result.isDesktop) {
            result.isDesktop = true;
        }

        if (/Edg\//i.test(ua)) { result.browser = 'edge'; result.browserVersion = (ua.match(/Edg\/([\d.]+)/) || [])[1]; }
        else if (/Chrome\//i.test(ua) && !/Edg\//i.test(ua)) { result.browser = 'chrome'; result.browserVersion = (ua.match(/Chrome\/([\d.]+)/) || [])[1]; }
        else if (/Firefox\//i.test(ua)) { result.browser = 'firefox'; result.browserVersion = (ua.match(/Firefox\/([\d.]+)/) || [])[1]; }
        else if (/Safari\//i.test(ua) && !/Chrome\//i.test(ua)) { result.browser = 'safari'; result.browserVersion = (ua.match(/Version\/([\d.]+)/) || [])[1]; }

        return result;
    }

     
    function detectLowMemory() {
        if ('deviceMemory' in navigator) {
            return navigator.deviceMemory < 4;
        }
        if (_devicePixelRatio < 1.5 && _screenWidth <= 360) {
            return true;
        }
        if (_platform && _platform.os === 'android' && _screenWidth <= 480) {
            return true;
        }
        return false;
    }

     
    function injectMobileBottomNav() {
        if (!_platform) return;
        if (_platform.isDesktop && !_platform.isTouch) return;

        var existing = document.querySelector('.mobile-bottom-nav');
        if (existing) return;

        var navHTML = '<nav class="mobile-bottom-nav" role="navigation" aria-label="移动端导航">' +
            '<a class="nav-item active" href="#dashboard" title="仪表盘"><span>实时仪表盘</span></a>' +
            '<a class="nav-item" href="#charts" title="数据分析"><span>分析</span></a>' +
            '<a class="nav-item" href="#plugins" title="插件"><span>插件</span></a>' +
            '<a class="nav-item" href="#control" title="控制"><span>控制</span></a>' +
            '<a class="nav-item" href="/download.html" title="下载APP"><span>下载</span></a>' +
            '</nav>';

        var container = document.createElement('div');
        container.innerHTML = navHTML;
        document.body.appendChild(container.firstElementChild);

        var items = document.querySelectorAll('.mobile-bottom-nav .nav-item');
        Array.prototype.forEach.call(items, function (item) {
            item.addEventListener('click', function (e) {
                var href = this.getAttribute('href');
                if (href && href.startsWith('#')) {
                    e.preventDefault();
                    var target = document.querySelector(href);
                    if (target) {
                        target.scrollIntoView({ behavior: 'smooth' });
                        updateMobileNavActive(this);
                    }
                }
            }, { passive: false });
        });

        var ticking = false;
        window.addEventListener('scroll', function () {
            if (!ticking) {
                window.requestAnimationFrame(function () {
                    updateMobileNavOnScroll();
                    ticking = false;
                });
                ticking = true;
            }
        }, { passive: true });
    }

    function updateMobileNavActive(activeItem) {
        var items = document.querySelectorAll('.mobile-bottom-nav .nav-item');
        Array.prototype.forEach.call(items, function (item) {
            item.classList.remove('active');
        });
        if (activeItem) activeItem.classList.add('active');
    }

    function updateMobileNavOnScroll() {
        var sections = ['dashboard', 'charts', 'plugins', 'control'];
        var navItems = document.querySelectorAll('.mobile-bottom-nav .nav-item');
        if (!navItems.length) return;

        var scrollPos = window.scrollY + 100;
        for (var i = sections.length - 1; i >= 0; i--) {
            var el = document.getElementById(sections[i]);
            if (el && el.offsetTop <= scrollPos) {
                if (navItems[i]) {
                    updateMobileNavActive(navItems[i]);
                }
                break;
            }
        }
    }

     
    function injectPlatformClasses() {
        if (!_platform) return;
        var html = document.documentElement;
        html.classList.remove('android-platform', 'ios-platform');

        if (_platform.os === 'android') html.classList.add('android-platform');
        else if (_platform.os === 'ios' || _platform.os === 'ipados') html.classList.add('ios-platform');
    }

     
    function injectLowMemoryClass() {
        if (_isLowMemory) {
            document.documentElement.classList.add('low-memory');
            console.log('[PlatformUtils] 低内存模式已启用');
        }
    }

     
    function initNetworkMonitor() {
        _isOnline = navigator.onLine;
        window.addEventListener('online', function () {
            _isOnline = true;
            document.body.classList.remove('offline');
            showNetworkToast('网络已恢复');
        }, { passive: true });
        window.addEventListener('offline', function () {
            _isOnline = false;
            document.body.classList.add('offline');
            showNetworkToast('网络已断开，部分功能可能不可用', 'warning');
        }, { passive: true });
    }

    function showNetworkToast(msg, type) {
        var toast = document.createElement('div');
        toast.className = 'toast-enhanced ' + (type || 'success');
        toast.innerHTML = '<i class="fas fa-' + (type === 'warning' ? 'wifi-slash' : 'wifi') + '"></i> ' + msg;
        document.body.appendChild(toast);
        setTimeout(function () { toast.remove(); }, 3000);
    }

     
    return {
        init: function () {
            if (_initialized) return;
            _initialized = true;

            _platform = detectPlatform();
            _isLowMemory = detectLowMemory();

            injectPlatformClasses();
            injectLowMemoryClass();
            injectMobileBottomNav();
            initNetworkMonitor();

            console.log('[PlatformUtils] 初始化完成:', {
                os: _platform.os,
                isMobile: _platform.isMobile,
                isTablet: _platform.isTablet,
                isDesktop: _platform.isDesktop,
                isTouch: _platform.isTouch,
                isLowMemory: _isLowMemory,
                browser: _platform.browser + ' ' + _platform.browserVersion,
                dpr: _devicePixelRatio
            });
        },

        getPlatform: function () { return _platform; },
        isLowMemory: function () { return _isLowMemory; },
        isOnline: function () { return _isOnline; },
        isTouchDevice: function () { return _platform ? _platform.isTouch : false; },

        on: function (element, eventType, handler, options) {
            var opts = options || {};
            if (typeof opts === 'boolean') opts = { capture: opts };
            if (opts.passive === undefined) {
                if (eventType === 'touchstart' || eventType === 'touchmove' ||
                    eventType === 'wheel' || eventType === 'mousewheel') {
                    opts.passive = true;
                }
            }
            element.addEventListener(eventType, handler, opts);
        },

        off: function (element, eventType, handler) {
            element.removeEventListener(eventType, handler);
        },

        pointerDown: function (element, handler, options) {
            var opts = options || { capture: false };
            element.addEventListener('pointerdown', handler, opts);
            if (!window.PointerEvent) {
                element.addEventListener('touchstart', handler, opts);
                element.addEventListener('mousedown', handler, opts);
            }
        },

        pointerUp: function (element, handler, options) {
            var opts = options || { capture: false };
            element.addEventListener('pointerup', handler, opts);
            if (!window.PointerEvent) {
                element.addEventListener('touchend', handler, opts);
                element.addEventListener('mouseup', handler, opts);
            }
        },

        detectPlatform: detectPlatform,
        _platform: _platform
    };
})();

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
        PlatformUtils.init();
    }, { passive: true });
} else {
    PlatformUtils.init();
}


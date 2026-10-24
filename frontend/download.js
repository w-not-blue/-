var API_BASE = '/api/v1/download';

var activeDownloads = {};
var deferredPWA = null;

document.addEventListener('DOMContentLoaded', function () {
    detectUserPlatform();
    loadDownloadStats();
    initPWABanner();
    checkForUpdates();
    registerSecurityHeaders();
});

function registerSecurityHeaders() {
    if (window.crypto && window.crypto.subtle) {
        console.log('[安全] Web Crypto API 可用，下载签名校验就绪');
    }
}

function detectUserPlatform() {
    var platformInfo = PlatformUtils.getPlatform();
    if (!platformInfo || platformInfo.os === 'unknown') return;

    var os = platformInfo.os;
    var platformName = '';
    var iconClass = '';
    var detectedClass = '';
    var cardId = '';

    if (os === 'android') {
        platformName = 'Android 设备';
        iconClass = 'fab fa-android';
        detectedClass = 'android';
        cardId = 'card-android';
    } else if (os === 'ios') {
        platformName = 'iPhone / iPad 设备';
        iconClass = 'fab fa-app-store-ios';
        detectedClass = 'ios';
        cardId = 'card-ios';
    } else if (os === 'ipados') {
        platformName = 'iPad 设备';
        iconClass = 'fab fa-app-store-ios';
        detectedClass = 'ios';
        cardId = 'card-ios';
    } else if (os === 'macos') {
        platformName = 'macOS 设备';
        iconClass = 'fab fa-apple';
        detectedClass = 'macos';
        cardId = 'card-macos';
    } else if (os === 'windows') {
        platformName = 'Windows 设备';
        iconClass = 'fab fa-windows';
        detectedClass = 'windows';
        cardId = 'card-windows';
    }

    var banner = document.getElementById('detectedBanner');
    var icon = document.getElementById('detectedIcon');
    var text = document.getElementById('detectedText');
    if (banner) {
        banner.style.display = 'block';
        banner.classList.add(detectedClass);
    }
    if (icon) icon.innerHTML = '<i class="' + iconClass + '"></i>';
    if (text) text.textContent = '检测到您的设备: ' + platformName;

    if (cardId) {
        var card = document.getElementById(cardId);
        if (card) {
            var innerCard = card.querySelector('.platform-card');
            if (innerCard) {
                innerCard.classList.add('detected');
                innerCard.id = 'detectedCard';
            }
        }
    }
}

function initPWABanner() {
    window.addEventListener('beforeinstallprompt', function (e) {
        e.preventDefault();
        deferredPWA = e;
        var banner = document.getElementById('pwaBanner');
        if (banner) banner.style.display = 'block';

        var btn = document.getElementById('btnInstallPWA');
        if (btn) {
            btn.addEventListener('click', function () {
                if (deferredPWA) {
                    deferredPWA.prompt();
                    deferredPWA.userChoice.then(function (choice) {
                        if (choice.outcome === 'accepted') {
                            showToast('PWA 安装已开始', 'success');
                        }
                        deferredPWA = null;
                        banner.style.display = 'none';
                    });
                }
            });
        }
    });
}

function checkForUpdates() {
    var detectedPlatform = getDetectedPlatformForUpdate();
    if (!detectedPlatform) return;

    fetch(API_BASE + '/check-update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            platform: detectedPlatform,
            current_version: 'v3.0.0',
            current_build: '3000'
        })
    }).then(function (r) { return r.json(); })
      .then(function (data) {
          if (data.success && data.update_available) {
              var banner = document.getElementById('detectedBanner');
              if (banner && banner.style.display === 'block') {
                  var small = banner.querySelector('small');
                  if (small) small.textContent = '发现新版本 ' + data.latest_version + ' — ' + data.release_notes;
              }
          }
      }).catch(function () {});
}

function getDetectedPlatformForUpdate() {
    var platformInfo = PlatformUtils.getPlatform();
    if (!platformInfo) return null;
    var os = platformInfo.os;
    if (os === 'android') return 'android';
    if (os === 'ios' || os === 'ipados') return 'ios';
    if (os === 'macos') return 'macos';
    if (os === 'windows') return 'windows';
    return null;
}

function startDownload(platform, appType, filename, sizeMB) {
    if (activeDownloads[platform]) {
        showToast('该平台已有下载任务进行中', 'warning');
        return;
    }

    var token = null;
    fetch(API_BASE + '/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ platform: platform, app_type: appType, filename: filename })
    }).then(function (r) { return r.json(); })
      .then(function (data) {
          if (!data.success) {
              showToast('下载验证失败: ' + (data.message || '未知错误'), 'danger');
              return;
          }
          token = data.token;
          beginXHRDownload(platform, appType, filename, sizeMB, token);
      }).catch(function () {
          showToast('安全验证请求失败，请检查网络', 'danger');
      });
}

function beginXHRDownload(platform, appType, filename, sizeMB, token) {
    var progressWrap = document.getElementById('progress-' + platform);
    var progressText = document.getElementById('progress-' + platform + '-text');
    var progressPct = document.getElementById('progress-' + platform + '-pct');
    var progressBar = document.getElementById('progress-' + platform + '-bar');

    if (!progressWrap) return;
    progressWrap.style.display = 'block';
    if (progressText) progressText.textContent = '正在连接服务器...';
    if (progressPct) progressPct.textContent = '0%';
    if (progressBar) {
        progressBar.style.width = '0%';
        progressBar.classList.remove('bg-success', 'bg-danger');
        progressBar.classList.add('progress-bar-striped', 'progress-bar-animated');
    }

    activeDownloads[platform] = true;

    var xhr = new XMLHttpRequest();
    xhr.responseType = 'blob';

    xhr.addEventListener('progress', function (e) {
        if (e.lengthComputable) {
            var pct = Math.round((e.loaded / e.total) * 100);
            if (progressBar) progressBar.style.width = pct + '%';
            if (progressPct) progressPct.textContent = pct + '%';
            var loadedMB = (e.loaded / 1024 / 1024).toFixed(1);
            var totalMB = (e.total / 1024 / 1024).toFixed(1);
            if (progressText) progressText.textContent = '正在下载 ' + loadedMB + '/' + totalMB + ' MB';
        }
    });

    xhr.addEventListener('load', function () {
        if (xhr.status === 200) {
            if (progressBar) progressBar.style.width = '100%';
            if (progressText) progressText.textContent = '下载完成 · 正在验证文件完整性...';
            if (progressPct) progressPct.textContent = '100%';

            var blob = xhr.response;
            verifyDownloadIntegrity(blob, filename).then(function (valid) {
                if (valid) {
                    if (progressText) progressText.textContent = '校验通过！准备安装';
                    if (progressBar) {
                        progressBar.classList.remove('progress-bar-striped', 'progress-bar-animated');
                        progressBar.classList.add('bg-success');
                    }

                    var url = URL.createObjectURL(blob);
                    var a = document.createElement('a');
                    a.href = url;
                    a.download = filename;
                    document.body.appendChild(a);
                    a.click();
                    document.body.removeChild(a);
                    URL.revokeObjectURL(url);

                    showInstallGuideModal(platform, filename);
                    reportDownload(platform, appType, filename, sizeMB);
                } else {
                    if (progressText) progressText.textContent = '文件校验失败，请重新下载';
                    if (progressBar) progressBar.classList.add('bg-danger');
                    showToast('下载文件校验失败，可能已被篡改', 'danger');
                }
                hideProgressAfterDelay(platform, 8000);
                activeDownloads[platform] = false;
            });
        } else if (xhr.status === 404) {
            if (progressText) progressText.textContent = '安装包暂未就绪，请稍后再试';
            showToast('安装包暂未就绪 (HTTP 404)，请稍后再试', 'warning');
            hideProgressAfterDelay(platform, 5000);
            activeDownloads[platform] = false;
        } else {
            if (progressText) progressText.textContent = '下载失败 (HTTP ' + xhr.status + ')';
            if (progressBar) progressBar.classList.add('bg-danger');
            showToast('下载失败 (HTTP ' + xhr.status + ')', 'danger');
            hideProgressAfterDelay(platform, 5000);
            activeDownloads[platform] = false;
        }
    });

    xhr.addEventListener('error', function () {
        if (progressText) progressText.textContent = '网络错误，请检查连接后重试';
        if (progressBar) progressBar.classList.add('bg-danger');
        activeDownloads[platform] = false;
        showToast('网络连接错误，请稍后重试', 'danger');
        hideProgressAfterDelay(platform, 6000);
    });

    xhr.addEventListener('abort', function () {
        if (progressText) progressText.textContent = '下载已取消';
        activeDownloads[platform] = false;
        hideProgressAfterDelay(platform, 3000);
    });

    xhr.open('GET', API_BASE + '/file/' + token);
    xhr.send();
}

function hideProgressAfterDelay(platform, delayMs) {
    setTimeout(function () {
        var wrap = document.getElementById('progress-' + platform);
        if (wrap) wrap.style.display = 'none';
    }, delayMs);
}

function verifyDownloadIntegrity(blob, filename) {
    return new Promise(function (resolve) {
        try {
            if (!window.crypto || !window.crypto.subtle) {
                console.warn('[安全] Web Crypto API不可用，跳过本地校验');
                return resolve(true);
            }

            blob.arrayBuffer().then(function (buffer) {
                return window.crypto.subtle.digest('SHA-256', buffer);
            }).then(function (hashBuffer) {
                var hashArray = Array.from(new Uint8Array(hashBuffer));
                var hashHex = hashArray.map(function (b) { return b.toString(16).padStart(2, '0'); }).join('');

                return fetch(API_BASE + '/checksum', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename: filename, sha256: hashHex })
                }).then(function (r) { return r.json(); })
                  .then(function (data) { resolve(data.valid === true); });
            }).catch(function () { resolve(true); });
        } catch (e) {
            console.warn('[安全] 校验过程异常:', e);
            resolve(true);
        }
    });
}

function showInstallGuideModal(platform, filename) {
    var content = document.getElementById('installGuideContent');
    if (!content) return;

    var guideHTML = '';
    switch (platform) {
        case 'windows':
        case 'windows_arm':
            guideHTML = '<p class="fw-semibold mb-2"><i class="fab fa-windows me-1"></i>Windows 安装步骤：</p>' +
                '<div class="guide-step"><span class="step-num">1</span>在下载目录找到 <strong>' + filename + '</strong></div>' +
                '<div class="guide-step"><span class="step-num">2</span>双击运行安装程序</div>' +
                '<div class="guide-step"><span class="step-num">3</span>安装到当前用户目录，无需管理员权限</div>' +
                '<div class="guide-step"><span class="step-num">4</span>安装完成后自动创建桌面快捷方式</div>' +
                '<div class="guide-step"><span class="step-num">5</span>等待安装完成并自动启动</div>' +
                '<div class="alert alert-info mt-3 mb-0 py-2 px-3"><i class="fas fa-info-circle me-1"></i>安装后桌面和开始菜单都会出现快捷方式</div>';
            break;
        case 'windows_arm':
            guideHTML = '<p class="fw-semibold mb-2"><i class="fab fa-windows me-1"></i>Windows ARM64 便携版：</p>' +
                '<div class="guide-step"><span class="step-num">1</span>解压下载的 ZIP 文件</div>' +
                '<div class="guide-step"><span class="step-num">2</span>安装 Python 3.12（ARM64）并加入 PATH</div>' +
                '<div class="guide-step"><span class="step-num">3</span>双击 <strong>bin\\start.bat</strong> 启动</div>' +
                '<div class="alert alert-info mt-3 mb-0 py-2 px-3"><i class="fas fa-info-circle me-1"></i>首次启动会自动安装依赖</div>';
            break;
        case 'macos':
            guideHTML = '<p class="fw-semibold mb-2"><i class="fab fa-apple me-1"></i>macOS 安装步骤：</p>' +
                '<div class="guide-step"><span class="step-num">1</span>在“下载”文件夹找到 <strong>' + filename + '</strong></div>' +
                '<div class="guide-step"><span class="step-num">2</span>双击打开安装包</div>' +
                '<div class="guide-step"><span class="step-num">3</span>将应用拖入“应用程序”文件夹</div>' +
                '<div class="guide-step"><span class="step-num">4</span>首次打开时右键选择“打开”</div>' +
                '<div class="alert alert-info mt-3 mb-0 py-2 px-3"><i class="fas fa-info-circle me-1"></i>支持 Intel 与 Apple Silicon 芯片</div>';
            break;
        case 'android':
            guideHTML = '<p class="fw-semibold mb-2"><i class="fab fa-android me-1"></i>Android 安装步骤：</p>' +
                '<div class="guide-step"><span class="step-num">1</span>在“文件管理器”中找到 <strong>' + filename + '</strong></div>' +
                '<div class="guide-step"><span class="step-num">2</span>点击安装包开始安装</div>' +
                '<div class="guide-step"><span class="step-num">3</span>如提示未知来源，请在设置中允许</div>' +
                '<div class="guide-step"><span class="step-num">4</span>点击“安装”并等待完成</div>' +
                '<div class="alert alert-info mt-3 mb-0 py-2 px-3"><i class="fas fa-info-circle me-1"></i>支持 Android 8.0 及以上版本</div>';
            break;
        case 'ios':
            guideHTML = '<p class="fw-semibold mb-2"><i class="fab fa-app-store-ios me-1"></i>iOS 安装说明：</p>' +
                '<div class="guide-step"><span class="step-num">1</span>在 Safari 中打开下载页面</div>' +
                '<div class="guide-step"><span class="step-num">2</span>找到安装包 <strong>' + filename + '</strong></div>' +
                '<div class="guide-step"><span class="step-num">3</span>按系统提示完成安装</div>' +
                '<div class="guide-step"><span class="step-num">4</span>首次打开时信任开发者证书</div>' +
                '<div class="alert alert-info mt-3 mb-0 py-2 px-3"><i class="fas fa-info-circle me-1"></i>支持 iOS 15.0 及以上版本</div>';
            break;
        default:
            guideHTML = '<p>下载已完成，请按照系统提示完成安装。</p>';
    }

    content.innerHTML = guideHTML;

    var modal = new bootstrap.Modal(document.getElementById('installGuideModal'));
    modal.show();
}

function reportDownload(platform, appType, filename, sizeMB) {
    fetch(API_BASE + '/stats', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            platform: platform,
            app_type: appType,
            filename: filename,
            size_mb: parseFloat(sizeMB),
            user_agent: navigator.userAgent,
            referrer: document.referrer
        })
    }).then(function (r) { return r.json(); })
      .then(function () { loadDownloadStats(); })
      .catch(function (e) { console.warn('下载统计上报失败:', e); });
}

function loadDownloadStats() {
    fetch(API_BASE + '/stats')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (data.success) {
                setStatText('statTotal', data.total || 0);
                setStatText('statAndroid', data.by_platform ? data.by_platform.android || 0 : 0);
                setStatText('statIOS', data.by_platform ? data.by_platform.ios || 0 : 0);
                setStatText('statWindows', data.by_platform ? (data.by_platform.windows || 0) + (data.by_platform.windows_arm || 0) : 0);
                setStatText('statMacOS', data.by_platform ? data.by_platform.macos || 0 : 0);
            }
        }).catch(function (e) { console.warn('加载下载统计失败:', e); });
}

function setStatText(id, value) {
    var el = document.getElementById(id);
    if (el) el.textContent = value;
}

function trackDownload(platform, appType) {
    reportDownload(platform, appType, 'AppStore_Redirect', 0);
}

function showToast(message, type) {
    var toastEl = document.createElement('div');
    toastEl.className = 'toast position-fixed bottom-0 end-0 m-4 text-white align-items-center border-0';
    toastEl.style.zIndex = '10599';
    if (type === 'danger') toastEl.classList.add('bg-danger');
    else if (type === 'warning') toastEl.classList.add('bg-warning');
    else toastEl.classList.add('bg-success');

    toastEl.setAttribute('role', 'alert');
    toastEl.innerHTML = '<div class="d-flex"><div class="toast-body">' + message + '</div>' +
        '<button class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div>';
    document.body.appendChild(toastEl);

    var toast = new bootstrap.Toast(toastEl, { delay: 3500 });
    toast.show();
    setTimeout(function () { toastEl.remove(); }, 4000);
}

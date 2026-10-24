(function () {
    'use strict';

    var AUTH_API = '/api/v1/auth';

    function byId(id) {
        return document.getElementById(id);
    }

    function request(path, options) {
        options = options || {};
        var headers = options.headers || {};
        headers['Content-Type'] = 'application/json';
        return fetch(AUTH_API + path, {
            method: options.method || 'GET',
            headers: headers,
            body: options.body,
            credentials: 'same-origin',
            cache: 'no-store'
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (data) {
                if (!response.ok || data.success === false) {
                    var error = new Error(data.message || '请求失败，请稍后重试');
                    error.status = response.status;
                    throw error;
                }
                return data;
            });
        });
    }

    function displayDate(value) {
        if (!value) return '--';
        return String(value).replace('T', ' ').slice(0, 19);
    }

    function shortDate(value) {
        if (!value) return '--';
        var text = String(value).replace('T', ' ');
        return text.length >= 16 ? text.slice(5, 16) : text;
    }

    function showAlert(message, type) {
        var alert = byId('passwordAlert');
        if (!alert) return;
        alert.textContent = message;
        alert.className = 'auth-alert ' + (type === 'success' ? 'auth-alert-success' : 'auth-alert-error');
        alert.hidden = false;
    }

    function hideAlert() {
        var alert = byId('passwordAlert');
        if (!alert) return;
        alert.hidden = true;
        alert.textContent = '';
    }

    function loadProfile() {
        request('/me').then(function (data) {
            if (!data.authenticated || !data.user) {
                window.location.href = '/login.html';
                return;
            }
            var user = data.user;
            var username = user.username || '--';
            var avatar = byId('profileAvatar');
            var heroName = byId('profileUsername');
            var infoName = byId('profileInfoUsername');
            var created = byId('profileCreated');
            var lastLogin = byId('profileLastLogin');
            if (avatar) avatar.textContent = username.charAt(0).toUpperCase();
            if (heroName) heroName.textContent = username;
            if (infoName) infoName.textContent = username;
            if (created) created.textContent = displayDate(user.created_at);
            if (lastLogin) lastLogin.textContent = displayDate(user.last_login);
        }).catch(function () {
            window.location.href = '/login.html';
        });
    }

    function loadRuns() {
        fetch('/api/v1/runs?limit=100', { credentials: 'same-origin', cache: 'no-store' })
            .then(function (response) { return response.json(); })
            .then(function (data) {
                var runs = (data && data.runs) || [];
                var count = byId('profileRunCount');
                var latest = byId('profileLastRun');
                if (count) count.textContent = runs.length ? String(runs.length) + (runs.length >= 100 ? '+' : '') : '0';
                if (latest) latest.textContent = runs.length ? shortDate(runs[0].created_at) : '--';
            })
            .catch(function () {
                var count = byId('profileRunCount');
                var latest = byId('profileLastRun');
                if (count) count.textContent = '--';
                if (latest) latest.textContent = '--';
            });
    }

    function initPasswordForm() {
        var form = byId('passwordForm');
        if (!form) return;
        var submit = byId('passwordSubmit');
        var label = byId('passwordSubmitLabel');
        var current = byId('currentPassword');
        var next = byId('newPassword');
        var confirm = byId('confirmPassword');

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            hideAlert();

            var currentValue = current.value;
            var nextValue = next.value;
            var confirmValue = confirm.value;

            if (!currentValue) {
                showAlert('请输入当前密码', 'error');
                current.focus();
                return;
            }
            if (nextValue.length < 6 || nextValue.length > 64) {
                showAlert('新密码长度需为 6-64 位', 'error');
                next.focus();
                return;
            }
            if (nextValue !== confirmValue) {
                showAlert('两次输入的新密码不一致', 'error');
                confirm.focus();
                return;
            }

            if (submit) submit.disabled = true;
            if (label) label.textContent = '保存中...';
            request('/change-password', {
                method: 'POST',
                body: JSON.stringify({
                    current_password: currentValue,
                    new_password: nextValue,
                    confirm_password: confirmValue
                })
            }).then(function () {
                showAlert('密码已修改，下次登录请使用新密码。', 'success');
                form.reset();
            }).catch(function (error) {
                showAlert(error.message || '修改密码失败', 'error');
            }).finally(function () {
                if (submit) submit.disabled = false;
                if (label) label.textContent = '保存新密码';
            });
        });
    }

    function initLogout() {
        var button = byId('profileLogoutBtn');
        if (!button) return;
        button.addEventListener('click', function () {
            button.disabled = true;
            request('/logout', { method: 'POST' }).then(function () {
                window.location.href = '/login.html';
            }).catch(function () {
                window.location.href = '/login.html';
            });
        });
    }

    function initSidebar() {
        var toggle = byId('sidebarToggle');
        if (!toggle) return;
        toggle.addEventListener('click', function () {
            document.body.classList.toggle('sidebar-open');
        });
    }

    document.addEventListener('DOMContentLoaded', function () {
        loadProfile();
        loadRuns();
        initPasswordForm();
        initLogout();
        initSidebar();
    });
})();
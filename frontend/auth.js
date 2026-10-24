(function () {
    'use strict';

    var API_BASE = '/api/v1/auth';

    function byId(id) {
        return document.getElementById(id);
    }

    function showAlert(id, message) {
        var element = byId(id);
        if (!element) return;
        element.textContent = message;
        element.hidden = false;
    }

    function hideAlert(id) {
        var element = byId(id);
        if (!element) return;
        element.textContent = '';
        element.hidden = true;
    }

    function request(path, options) {
        options = options || {};
        var headers = options.headers || {};
        headers['Content-Type'] = 'application/json';
        return fetch(API_BASE + path, {
            method: options.method || 'GET',
            headers: headers,
            body: options.body,
            credentials: 'same-origin',
            cache: 'no-store'
        }).then(function (response) {
            return response.json().catch(function () {
                return {};
            }).then(function (data) {
                if (!response.ok || data.success === false) {
                    var error = new Error(data.message || '请求失败，请稍后重试');
                    error.status = response.status;
                    throw error;
                }
                return data;
            });
        });
    }

    function setBusy(button, label, busy, text) {
        if (button) button.disabled = !!busy;
        if (label) label.textContent = text;
    }

    function initPasswordToggles() {
        document.querySelectorAll('[data-toggle-password]').forEach(function (button) {
            button.addEventListener('click', function () {
                var input = byId(button.getAttribute('data-toggle-password'));
                if (!input) return;
                var visible = input.type === 'text';
                input.type = visible ? 'password' : 'text';
                var icon = button.querySelector('i');
                if (icon) {
                    icon.className = visible ? 'fas fa-eye' : 'fas fa-eye-slash';
                }
            });
        });
    }

    function initCaptcha() {
        var image = byId('captchaImage');
        var refresh = byId('captchaRefresh');
        var input = byId('captcha');
        if (!image || !refresh) return function () {};

        function load() {
            image.style.opacity = '0.45';
            return request('/captcha').then(function (data) {
                image.src = data.image;
                if (input) input.value = '';
            }).catch(function () {
                showAlert('authError', '验证码加载失败，请点击图片刷新');
            }).finally(function () {
                image.style.opacity = '1';
            });
        }

        refresh.addEventListener('click', load);
        image.addEventListener('click', load);
        load();
        return load;
    }

    function initLogin() {
        var form = byId('loginForm');
        if (!form) return;

        var refreshCaptcha = initCaptcha();
        var submit = byId('loginSubmit');
        var submitLabel = byId('loginSubmitLabel');
        var username = byId('username');
        var password = byId('password');
        var captcha = byId('captcha');

        var params = new URLSearchParams(window.location.search);
        if (params.get('registered') === '1') {
            showAlert('authSuccess', '注册成功，请使用新账号登录。');
        }

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            hideAlert('authError');

            var user = username.value.trim();
            var pass = password.value;
            var code = captcha.value.trim();

            if (!user) {
                showAlert('authError', '请输入账号');
                username.focus();
                return;
            }
            if (!pass) {
                showAlert('authError', '请输入密码');
                password.focus();
                return;
            }
            if (!code) {
                showAlert('authError', '请输入图形验证码');
                captcha.focus();
                return;
            }

            hideAlert('authSuccess');
            setBusy(submit, submitLabel, true, '正在登录...');
            request('/login', {
                method: 'POST',
                body: JSON.stringify({ username: user, password: pass, captcha: code })
            }).then(function () {
                window.location.href = '/';
            }).catch(function (error) {
                showAlert('authError', error.message || '登录失败，请稍后重试');
                setBusy(submit, submitLabel, false, '登录');
                if (refreshCaptcha) refreshCaptcha();
            });
        });
    }

    function initRegister() {
        var form = byId('registerForm');
        if (!form) return;

        var submit = byId('registerSubmit');
        var submitLabel = byId('registerSubmitLabel');
        var username = byId('username');
        var password = byId('password');
        var confirmPassword = byId('confirmPassword');

        form.addEventListener('submit', function (event) {
            event.preventDefault();
            hideAlert('authError');

            var user = username.value.trim();
            var pass = password.value;
            var confirm = confirmPassword.value;

            if (!user) {
                showAlert('authError', '请输入用户名');
                username.focus();
                return;
            }
            if (pass.length < 6) {
                showAlert('authError', '密码至少需要 6 位');
                password.focus();
                return;
            }
            if (pass !== confirm) {
                showAlert('authError', '两次输入的密码不一致');
                confirmPassword.focus();
                return;
            }

            setBusy(submit, submitLabel, true, '正在创建...');
            request('/register', {
                method: 'POST',
                body: JSON.stringify({
                    username: user,
                    password: pass,
                    confirm_password: confirm
                })
            }).then(function () {
                window.location.href = '/login.html?registered=1';
            }).catch(function (error) {
                showAlert('authError', error.message || '注册失败，请稍后重试');
                setBusy(submit, submitLabel, false, '创建账号');
            });
        });
    }

    function initUserMenu() {
        var menu = byId('userMenu');
        if (!menu) return;

        request('/me').then(function (data) {
            if (!data.authenticated || !data.user) {
                window.location.href = '/login.html';
                return;
            }
            var name = data.user.username;
            var userName = byId('userName');
            var userAvatar = byId('userAvatar');
            var menuUserName = byId('menuUserName');
            if (userName) userName.textContent = name;
            if (userAvatar) userAvatar.textContent = name.charAt(0).toUpperCase();
            if (menuUserName) menuUserName.textContent = name;
        }).catch(function () {
            window.location.href = '/login.html';
        });

        var logout = byId('logoutBtn');
        if (logout) {
            logout.addEventListener('click', function () {
                logout.disabled = true;
                request('/logout', { method: 'POST' }).then(function () {
                    window.location.href = '/login.html';
                }).catch(function () {
                    window.location.href = '/login.html';
                });
            });
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        initPasswordToggles();
        initLogin();
        initRegister();
        initUserMenu();
    });
})();
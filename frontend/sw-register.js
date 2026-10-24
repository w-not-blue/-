if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
      .then((reg) => {
        console.log('[SW] Registered:', reg.scope);
        reg.update().catch(() => {});
      })
      .catch((err) => console.warn('[SW] Registration failed:', err));
  });
}

if (window.matchMedia('(display-mode: standalone)').matches) {
  document.documentElement.classList.add('pwa-standalone');
  console.log('[PWA] Running in standalone mode');
}

window.addEventListener('appinstalled', () => {
  console.log('[PWA] App installed successfully');
  if (typeof gtag === 'function') gtag('event', 'pwa_install', {});
});

let deferredPrompt;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  deferredPrompt = e;
  const installBtn = document.getElementById('pwaInstallBtn');
  if (installBtn) {
    installBtn.style.display = 'inline-block';
    installBtn.addEventListener('click', () => {
      deferredPrompt.prompt();
      deferredPrompt.userChoice.then((choice) => {
        console.log(`[PWA] User chose: ${choice.outcome}`);
        deferredPrompt = null;
      });
    });
  }
});

document.addEventListener('DOMContentLoaded', () => {
  if (window.matchMedia('(display-mode: standalone)').matches) {
    const browserLinks = document.querySelectorAll('[data-hide-in-pwa]');
    browserLinks.forEach(el => el.style.display = 'none');
  }
});

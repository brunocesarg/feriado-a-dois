/* Notificações do app Feriado a Dois. Sem cache: o site sempre carrega a versão nova. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { body: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.title || 'Feriado a Dois', {
    body: d.body || '', icon: 'apple-touch-icon.png', badge: 'icon-192.png',
    tag: d.tag || 'feriado', data: { url: d.url || './' }
  }));
});
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL(e.notification.data.url, self.registration.scope).href;
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then(ws => {
    for (const w of ws) { if ('focus' in w) { w.navigate(url); return w.focus(); } }
    return clients.openWindow(url);
  }));
});

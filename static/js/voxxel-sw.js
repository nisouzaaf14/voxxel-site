self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/impressora/painel';
  event.waitUntil((async () => {
    const clientsList = await clients.matchAll({type:'window', includeUncontrolled:true});
    for (const client of clientsList) {
      if ('focus' in client) {
        try { if ('navigate' in client) await client.navigate(target); } catch (_) {}
        await client.focus();
        return;
      }
    }
    if (clients.openWindow) await clients.openWindow(target);
  })());
});

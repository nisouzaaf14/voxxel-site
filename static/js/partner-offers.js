(() => {
  const cfg = window.VOXXEL_PARTNER_OFFERS;
  const pop = document.getElementById('partner-offer-popover');
  if (!cfg || !pop) return;

  const els = {
    countdown: document.getElementById('partner-offer-countdown'),
    progress: document.getElementById('partner-offer-clock-progress'),
    title: document.getElementById('partner-offer-title'),
    description: document.getElementById('partner-offer-description'),
    earning: document.getElementById('partner-offer-earning'),
    distanceWrap: document.getElementById('partner-offer-distance-wrap'),
    distance: document.getElementById('partner-offer-distance'),
    materialWrap: document.getElementById('partner-offer-material-wrap'),
    material: document.getElementById('partner-offer-material'),
    accept: document.getElementById('partner-offer-accept'),
    decline: document.getElementById('partner-offer-decline'),
    notify: document.getElementById('partner-offer-notify'),
    optin: document.getElementById('partner-alert-optin'),
    optinEnable: document.getElementById('partner-alert-enable'),
    optinClose: document.getElementById('partner-alert-optin-close')
  };

  const RADIUS = 17;
  const CIRC = 2 * Math.PI * RADIUS;
  let current = null;
  let timer = null;
  let polling = false;
  let lastShownId = null;
  let workerRegistration = null;

  if (els.progress) {
    els.progress.style.strokeDasharray = `${CIRC}`;
    els.progress.style.strokeDashoffset = '0';
  }

  const money = value => Number(value || 0).toLocaleString('pt-BR', {style:'currency', currency:'BRL'});

  async function registerWorker() {
    if (!('serviceWorker' in navigator)) return null;
    try {
      workerRegistration = await navigator.serviceWorker.register(cfg.swUrl, {scope:'/'});
      return workerRegistration;
    } catch (_) { return null; }
  }

  function permissionState() {
    return ('Notification' in window) ? Notification.permission : 'unsupported';
  }

  function syncNotificationUi() {
    const state = permissionState();
    const mayAsk = state === 'default';
    if (els.notify) els.notify.hidden = !mayAsk;
    if (els.optin) {
      const dismissed = localStorage.getItem('voxxel_partner_alert_optin_dismissed') === '1';
      els.optin.hidden = !(mayAsk && !dismissed && !current);
    }
  }

  async function requestNotifications() {
    if (!('Notification' in window)) return false;
    const result = await Notification.requestPermission();
    if (result === 'granted') await registerWorker();
    syncNotificationUi();
    return result === 'granted';
  }

  async function showSystemNotification(o) {
    if (!document.hidden || permissionState() !== 'granted') return;
    const reg = workerRegistration || await registerWorker();
    if (!reg) return;

    const key = `voxxel_offer_notified_${o.id}`;
    const previous = Number(localStorage.getItem(key) || 0);
    const now = Date.now();
    // Evita múltiplas abas disparando a mesma chamada ao mesmo tempo.
    if (now - previous < 15000) return;
    localStorage.setItem(key, String(now));

    const parts = [];
    if (o.material) parts.push(o.material);
    if (o.distancia_km != null) parts.push(`${o.distancia_km} km`);
    parts.push(`Você recebe ${money(o.ganho)}`);

    await reg.showNotification('Novo projeto Voxxel', {
      body: parts.join(' • '),
      icon: '/static/images/favicon-192.png',
      badge: '/static/images/favicon-64.png',
      tag: `voxxel-offer-${o.id}`,
      renotify: true,
      requireInteraction: true,
      data: {url: cfg.panelUrl, offerId: o.id}
    });
  }

  function hideOffer() {
    pop.hidden = true;
    current = null;
    if (timer) { clearInterval(timer); timer = null; }
    syncNotificationUi();
  }

  function startTimer(seconds) {
    if (timer) clearInterval(timer);
    const total = Math.max(1, Number(seconds || 0));
    let remaining = total;
    const tick = () => {
      const min = Math.floor(remaining / 60);
      const sec = remaining % 60;
      els.countdown.textContent = `${min}:${String(sec).padStart(2,'0')}`;
      if (els.progress) {
        const ratio = Math.max(0, Math.min(1, remaining / total));
        els.progress.style.strokeDashoffset = String(CIRC * (1 - ratio));
      }
      pop.classList.toggle('partner-offer-urgent', remaining <= 60);
      if (remaining <= 0) { hideOffer(); setTimeout(checkOffer, 700); return; }
      remaining -= 1;
    };
    tick();
    timer = setInterval(tick, 1000);
  }

  function showOffer(o) {
    current = o;
    els.title.textContent = o.tipo || 'Pedido da Rede Voxxel';
    els.description.textContent = o.detalhes || 'Novo projeto disponível para análise.';
    els.earning.textContent = money(o.ganho);
    els.distanceWrap.hidden = o.distancia_km == null;
    if (o.distancia_km != null) els.distance.textContent = `${o.distancia_km} km`;
    els.materialWrap.hidden = !o.material;
    if (o.material) els.material.textContent = o.material;
    pop.hidden = false;
    if (els.optin) els.optin.hidden = true;
    if (lastShownId !== o.id) {
      lastShownId = o.id;
      pop.classList.remove('partner-offer-enter');
      void pop.offsetWidth;
      pop.classList.add('partner-offer-enter');
      showSystemNotification(o).catch(()=>{});
    }
    startTimer(o.segundos_restantes);
    syncNotificationUi();
  }

  async function checkOffer() {
    if (polling) return;
    polling = true;
    try {
      const res = await fetch(cfg.endpoint, {headers:{'Accept':'application/json'}, cache:'no-store', credentials:'same-origin'});
      if (!res.ok) return;
      const data = await res.json();
      if (data.oferta) {
        if (!current || current.id !== data.oferta.id) showOffer(data.oferta);
      } else if (current) hideOffer();
    } catch (_) {} finally { polling = false; }
  }

  async function respond(action) {
    if (!current) return;
    const offer = current;
    els.accept.disabled = els.decline.disabled = true;
    pop.classList.add('partner-offer-loading');
    try {
      const res = await fetch(`/impressora/api/oferta/${offer.id}/responder`, {
        method:'POST',
        credentials:'same-origin',
        headers:{'Content-Type':'application/x-www-form-urlencoded','Accept':'application/json'},
        body:new URLSearchParams({csrf_token:cfg.csrf, acao:action})
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.ok) {
        hideOffer();
        setTimeout(checkOffer, 600);
        return;
      }
      hideOffer();
      if (action === 'aceitar') window.location.href = cfg.panelUrl;
      else setTimeout(checkOffer, 1200);
    } catch (_) {
      els.accept.disabled = els.decline.disabled = false;
    } finally {
      pop.classList.remove('partner-offer-loading');
    }
  }

  els.accept?.addEventListener('click', () => respond('aceitar'));
  els.decline?.addEventListener('click', () => respond('recusar'));
  els.notify?.addEventListener('click', requestNotifications);
  els.optinEnable?.addEventListener('click', requestNotifications);
  els.optinClose?.addEventListener('click', () => {
    localStorage.setItem('voxxel_partner_alert_optin_dismissed', '1');
    syncNotificationUi();
  });

  navigator.serviceWorker?.addEventListener('message', event => {
    if (event.data?.type === 'VOXXEL_OPEN_PARTNER_PANEL') window.location.href = cfg.panelUrl;
  });

  registerWorker().finally(syncNotificationUi);
  checkOffer();
  setInterval(checkOffer, 5000);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) checkOffer();
  });
  window.addEventListener('focus', checkOffer);
})();

(() => {
  // Evita envio duplo em ações sensíveis (checkout, pagamento, mudança de status).
  // Formulários que falham na validação nativa não chegam ao evento submit.
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement) || form.dataset.allowRepeat === 'true') return;
    const submitters = form.querySelectorAll('button[type="submit"], input[type="submit"]');
    requestAnimationFrame(() => {
      submitters.forEach(control => {
        control.disabled = true;
        control.setAttribute('aria-disabled', 'true');
      });
      form.setAttribute('aria-busy', 'true');
    });
  });

  // Fecha o menu móvel com Esc, comportamento esperado em navegação por teclado.
  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const menu = document.getElementById('mobile-menu');
    const toggle = document.getElementById('menu-toggle');
    if (!menu?.classList.contains('open')) return;
    menu.classList.remove('open');
    toggle?.classList.remove('open');
    toggle?.setAttribute('aria-expanded', 'false');
    toggle?.focus();
  });
})();

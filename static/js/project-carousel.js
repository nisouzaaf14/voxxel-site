(() => {
  const root = document.querySelector('[data-project-carousel]');
  if (!root) return;

  const cards = Array.from(root.querySelectorAll('[data-carousel-card]'));
  const dots = Array.from(root.querySelectorAll('[data-carousel-dot]'));
  const prevButton = root.querySelector('[data-carousel-prev]');
  const nextButton = root.querySelector('[data-carousel-next]');
  const live = root.querySelector('[data-carousel-live]');
  let activeIndex = Math.max(0, cards.findIndex(card => card.classList.contains('is-active')));
  let touchStartX = null;
  let touchStartY = null;

  const mod = (value, size) => ((value % size) + size) % size;

  function render(nextIndex, focusCard = false) {
    activeIndex = mod(nextIndex, cards.length);
    const prevIndex = mod(activeIndex - 1, cards.length);
    const nextCardIndex = mod(activeIndex + 1, cards.length);

    cards.forEach((card, index) => {
      const active = index === activeIndex;
      card.classList.toggle('is-active', active);
      card.classList.toggle('is-prev', index === prevIndex);
      card.classList.toggle('is-next', index === nextCardIndex);
      card.setAttribute('aria-current', active ? 'true' : 'false');
      card.setAttribute('tabindex', active ? '0' : '-1');
    });

    dots.forEach((dot, index) => {
      const active = index === activeIndex;
      dot.classList.toggle('is-active', active);
      dot.setAttribute('aria-current', active ? 'true' : 'false');
    });

    const selected = cards[activeIndex];
    if (live && selected) {
      live.textContent = `${selected.dataset.title || 'Opção'} selecionado. Toque novamente para entrar.`;
    }
    if (focusCard && selected) selected.focus({ preventScroll: true });
  }

  cards.forEach((card, index) => {
    card.addEventListener('click', event => {
      if (index !== activeIndex) {
        event.preventDefault();
        render(index, true);
      }
    });

    card.addEventListener('focus', () => {
      if (index !== activeIndex) render(index);
    });
  });

  dots.forEach((dot, index) => {
    dot.addEventListener('click', () => render(index, true));
  });

  prevButton?.addEventListener('click', () => render(activeIndex - 1, true));
  nextButton?.addEventListener('click', () => render(activeIndex + 1, true));

  root.addEventListener('keydown', event => {
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      render(activeIndex - 1, true);
    } else if (event.key === 'ArrowRight') {
      event.preventDefault();
      render(activeIndex + 1, true);
    }
  });

  root.addEventListener('touchstart', event => {
    const touch = event.changedTouches[0];
    touchStartX = touch.clientX;
    touchStartY = touch.clientY;
  }, { passive: true });

  root.addEventListener('touchend', event => {
    if (touchStartX === null || touchStartY === null) return;
    const touch = event.changedTouches[0];
    const dx = touch.clientX - touchStartX;
    const dy = touch.clientY - touchStartY;
    touchStartX = null;
    touchStartY = null;

    if (Math.abs(dx) < 42 || Math.abs(dx) < Math.abs(dy) * 1.2) return;
    render(activeIndex + (dx < 0 ? 1 : -1));
  }, { passive: true });

  render(activeIndex);
})();
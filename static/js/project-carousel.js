(() => {
  const root = document.querySelector('[data-project-carousel]');
  if (!root) return;

  const track = root.querySelector('[data-carousel-track]');
  const cards = Array.from(root.querySelectorAll('[data-carousel-card]'));
  const dots = Array.from(root.querySelectorAll('[data-carousel-dot]'));
  const prev = root.querySelector('[data-carousel-prev]');
  const next = root.querySelector('[data-carousel-next]');
  const live = root.querySelector('[data-carousel-live]');
  if (!track || !cards.length) return;

  let activeIndex = 0;
  let frame = 0;

  const clamp = (value) => Math.max(0, Math.min(cards.length - 1, value));

  function cardCenter(index) {
    const card = cards[index];
    return card.offsetLeft - (track.clientWidth - card.offsetWidth) / 2;
  }

  function setActive(index) {
    activeIndex = clamp(index);
    cards.forEach((card, i) => card.classList.toggle('is-active', i === activeIndex));
    dots.forEach((dot, i) => {
      const active = i === activeIndex;
      dot.classList.toggle('is-active', active);
      dot.setAttribute('aria-current', active ? 'true' : 'false');
    });
    if (live) live.textContent = `${cards[activeIndex].dataset.title || 'Opção'} em destaque.`;
    if (prev) prev.disabled = activeIndex === 0;
    if (next) next.disabled = activeIndex === cards.length - 1;
  }

  function goTo(index) {
    const target = clamp(index);
    track.scrollTo({ left: cardCenter(target), behavior: 'smooth' });
    setActive(target);
  }

  function syncFromScroll() {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const center = track.scrollLeft + track.clientWidth / 2;
      let nearest = 0;
      let distance = Infinity;
      cards.forEach((card, index) => {
        const cardMid = card.offsetLeft + card.offsetWidth / 2;
        const delta = Math.abs(cardMid - center);
        if (delta < distance) {
          distance = delta;
          nearest = index;
        }
      });
      setActive(nearest);
    });
  }

  prev?.addEventListener('click', () => goTo(activeIndex - 1));
  next?.addEventListener('click', () => goTo(activeIndex + 1));
  dots.forEach((dot, index) => dot.addEventListener('click', () => goTo(index)));
  track.addEventListener('scroll', syncFromScroll, { passive: true });
  window.addEventListener('resize', syncFromScroll);

  setActive(0);
})();
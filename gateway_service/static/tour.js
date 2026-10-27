/* Интерактивный тур: подсвечивает реальные элементы страницы и объясняет их по порядку.
 * На телефоне (< 640 px) тура нет: ни кнопки «Подсказки», ни предложения, ни клавиши «?» — маленький экран занимает сама страница.
 * Данные — <script id="tour-data" type="application/json"> (см. gateway_service/tours.py).
 * Необязательный: предлагается один раз, пропускается в любой момент (кнопка, Esc), запускается кнопкой «?» или клавишей «?».
 * Прогресс хранится в localStorage отдельно для каждого аккаунта. «Больше не предлагать» действует на весь браузер (localStorage + cookie):
 * демо-аккаунты переключаются быстро, и отказ не должен возвращаться при входе под другим. Без хранилища тур просто предлагается снова. */
(() => {
  const node = document.getElementById('tour-data');
  if (!node) return;
  let data;
  try { data = JSON.parse(node.textContent); } catch { return; }
  if (!data || !data.tour) return;

  const tour = data.tour;
  const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const phone = matchMedia('(max-width: 639px)');
  const KEY = `fsp.tour:${data.user || 'guest'}`;
  const read = () => { try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; } };
  const write = (patch) => { try { localStorage.setItem(KEY, JSON.stringify({ ...read(), ...patch })); } catch { /* без хранилища — не страшно */ } };
  // Отказ от предложений — на браузер, а не на аккаунт; cookie страхует от недоступного localStorage.
  const MUTE_KEY = 'fsp.tour:muted', MUTE_COOKIE = 'fsp_tour_muted';
  const isMuted = () => {
    try { if (localStorage.getItem(MUTE_KEY)) return true; } catch { /* без хранилища — смотрим cookie */ }
    return document.cookie.split('; ').includes(`${MUTE_COOKIE}=1`) || !!read().muted;
  };
  const mute = () => {
    try { localStorage.setItem(MUTE_KEY, '1'); } catch { /* ничего */ }
    document.cookie = `${MUTE_COOKIE}=1; max-age=31536000; path=/; SameSite=Lax`;
    write({ muted: true });
  };
  const mark = (field) => { const s = read(); write({ [field]: { ...(s[field] || {}), [tour.id]: Date.now() } }); };

  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
  const visible = (n) => !!n && n.getClientRects().length > 0 && getComputedStyle(n).visibility !== 'hidden';
  const resolve = (step) => {
    let n = null;
    try { n = document.querySelector(step.sel); } catch { return null; }
    if (n && step.closest) n = n.closest(step.closest) || n;
    return visible(n) ? n : null;
  };

  // ---------- постоянная кнопка «?» ----------
  const launcher = el('button', 'tour-launcher no-print');
  launcher.type = 'button';
  launcher.setAttribute('data-tour-start', '');
  launcher.setAttribute('aria-label', 'Тур по странице: показать, что где находится');
  launcher.title = 'Тур по странице (клавиша «?»)';
  launcher.innerHTML = '<span class="tour-launcher-mark" aria-hidden="true">?</span><span class="tour-launcher-text">Подсказки</span>';
  launcher.style.display = phone.matches ? 'none' : '';
  document.body.append(launcher);

  // ---------- тур ----------
  let layer = null, pop = null, hole = null, steps = [], index = 0, current = null, opener = null, raf = 0, offer = null;

  const closeOffer = () => { offer?.remove(); offer = null; };

  function stop(finished) {
    if (!layer) return;
    cancelAnimationFrame(raf);
    removeEventListener('resize', place);
    removeEventListener('scroll', schedule, true);
    document.removeEventListener('keydown', onKey, true);
    layer.remove();
    layer = pop = hole = current = null;
    document.documentElement.classList.remove('tour-on');
    mark(finished ? 'done' : 'offered');
    (opener && document.contains(opener) ? opener : launcher).focus({ preventScroll: true });
  }

  function start(from) {
    closeOffer();
    if (layer || phone.matches) return;
    opener = from || document.activeElement;
    steps = tour.steps.map((s) => ({ ...s, node: resolve(s) })).filter((s) => s.node);
    if (!steps.length) { toast('На этой странице пока нечего подсвечивать.'); return; }
    layer = el('div', 'tour-layer no-print');
    layer.append(el('div', 'tour-shield'));
    hole = el('div', 'tour-hole');
    hole.setAttribute('aria-hidden', 'true');
    pop = el('div', 'tour-pop');
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-modal', 'true');
    pop.setAttribute('aria-labelledby', 'tour-title');
    pop.setAttribute('aria-describedby', 'tour-text');
    pop.tabIndex = -1;
    layer.append(hole, pop);
    document.body.append(layer);
    document.documentElement.classList.add('tour-on');
    document.addEventListener('keydown', onKey, true);
    addEventListener('resize', place);
    addEventListener('scroll', schedule, true);
    show(0);
  }

  const head = () => (document.querySelector('header.sticky')?.getBoundingClientRect().bottom || 0) + 12;

  function show(i, dir = 1) {
    // шаг, который исчез со страницы (например, свернулся блок), пропускаем в сторону движения
    while (steps[i] && !visible(steps[i].node) && i >= 0 && i < steps.length) i += dir;
    if (i < 0 || i >= steps.length) { stop(i >= steps.length); return; }
    index = i;
    const s = steps[i];
    current = s.node;
    const last = i === steps.length - 1;

    pop.replaceChildren();
    const meta = el('div', 'tour-meta');
    meta.append(el('span', 'tour-count', `Шаг ${i + 1} из ${steps.length}`), el('span', 'tour-tourname', tour.title));
    const bar = el('div', 'tour-bar');
    bar.setAttribute('aria-hidden', 'true');
    const fill = el('span');
    fill.style.width = `${((i + 1) / steps.length) * 100}%`;
    bar.append(fill);
    const title = el('h2', 'tour-title', s.title);
    title.id = 'tour-title';
    const text = el('p', 'tour-text', s.text);
    text.id = 'tour-text';
    pop.append(meta, bar, title, text);

    if (last && tour.next) {
      const next = el('a', 'tour-next-link', `${tour.next.label} →`);
      next.href = `${tour.next.href}${tour.next.href.includes('?') ? '&' : '?'}tour=1`;
      pop.append(next);
    }

    const row = el('div', 'tour-actions');
    const skip = el('button', 'tour-btn tour-btn-quiet', last ? 'Закрыть' : 'Пропустить тур');
    skip.type = 'button';
    skip.addEventListener('click', () => stop(last));
    const back = el('button', 'tour-btn tour-btn-ghost', 'Назад');
    back.type = 'button';
    back.disabled = i === 0;
    back.addEventListener('click', () => show(index - 1, -1));
    const nextBtn = el('button', 'tour-btn tour-btn-main', last ? 'Готово' : 'Далее');
    nextBtn.type = 'button';
    nextBtn.addEventListener('click', () => (last ? stop(true) : show(index + 1)));
    row.append(skip, back, nextBtn);
    pop.append(row, el('p', 'tour-keys', 'Клавиши: ← назад · → далее · Esc закрыть'));

    reveal(current, () => { place(); pop.focus({ preventScroll: true }); });
  }

  // Прокручиваем цель в «безопасную зону»: ниже липкой шапки.
  function reveal(target, done) {
    const top = head() + (target.closest('header') || target.closest('[data-spy]') ? 0 : document.querySelector('[data-spy]') ? 64 : 0);
    const bottom = innerHeight - 20;
    const r = target.getBoundingClientRect();
    const inHeader = !!target.closest('header');
    let dy = 0;
    if (!inHeader) {
      const room = bottom - top;
      if (r.height <= room) {
        if (r.top < top || r.bottom > bottom) dy = r.top - (top + Math.max(0, (room - r.height) / 2));
      } else if (r.top < top || r.top > top + 40) {
        dy = r.top - top;
      }
    }
    if (Math.abs(dy) < 2) { done(); return; }
    scrollBy({ top: dy, behavior: reduce ? 'auto' : 'smooth' });
    // ждём, пока прокрутка успокоится
    let last = -1, still = 0;
    const wait = () => {
      const y = scrollY;
      still = y === last ? still + 1 : 0;
      last = y;
      if (still >= 4 || reduce) { done(); return; }
      requestAnimationFrame(wait);
    };
    requestAnimationFrame(wait);
  }

  function schedule() { if (!raf) raf = requestAnimationFrame(() => { raf = 0; place(); }); }

  function place() {
    if (!layer || !current) return;
    const pad = 8, vw = innerWidth, vh = innerHeight;
    const r = current.getBoundingClientRect();
    const x = Math.max(4, r.left - pad), y = Math.max(4, r.top - pad);
    const w = Math.min(vw - 8, r.right + pad) - x, h = Math.min(vh - 8, r.bottom + pad) - y;
    const first = !hole.dataset.ready;  // первое появление — без «выезда» рамки из угла
    if (first) hole.style.transition = 'none';
    Object.assign(hole.style, { left: `${x}px`, top: `${y}px`, width: `${Math.max(0, w)}px`, height: `${Math.max(0, h)}px` });
    if (first) { hole.getBoundingClientRect(); hole.style.transition = ''; hole.dataset.ready = '1'; }

    const pw = Math.min(380, vw - 24), ph = pop.offsetHeight, gap = 14;
    let left, top;
    if (y + h + gap + ph <= vh - 8) { top = y + h + gap; left = x; }
    else if (y - gap - ph >= 8) { top = y - gap - ph; left = x; }
    else if (x + w + gap + pw <= vw - 8) { left = x + w + gap; top = Math.max(8, Math.min(y, vh - ph - 8)); }
    else if (x - gap - pw >= 8) { left = x - gap - pw; top = Math.max(8, Math.min(y, vh - ph - 8)); }
    else { left = (vw - pw) / 2; top = vh - ph - 16; }  // цель огромная — подсказка поверх, внизу
    left = Math.max(12, Math.min(left, vw - pw - 12));
    Object.assign(pop.style, { width: `${pw}px`, left: `${left}px`, top: `${Math.max(8, top)}px`, right: 'auto', bottom: 'auto' });
  }

  function onKey(e) {
    if (!layer) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); stop(false); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); index === steps.length - 1 ? stop(true) : show(index + 1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); if (index > 0) show(index - 1, -1); }
    else if (e.key === 'Tab') {
      const items = [...pop.querySelectorAll('button:not(:disabled), a')];
      if (!items.length) return;
      const first = items[0], lastItem = items[items.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === pop)) { e.preventDefault(); lastItem.focus(); }
      else if (!e.shiftKey && document.activeElement === lastItem) { e.preventDefault(); first.focus(); }
      else if (!pop.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
    }
  }

  // ---------- предложение «показать тур» (один раз, без навязчивости) ----------
  function toast(message) {
    const t = el('div', 'tour-toast no-print', message);
    t.setAttribute('role', 'status');
    document.body.append(t);
    setTimeout(() => t.remove(), 3500);
  }

  function showOffer() {
    if (layer || offer || phone.matches) return;
    offer = el('section', 'tour-offer no-print');
    offer.setAttribute('role', 'region');
    offer.setAttribute('aria-label', 'Предложение пройти тур');
    const t = el('p', 'tour-offer-title', 'Впервые здесь?');
    const p = el('p', 'tour-offer-text', `Покажем за минуту, что где находится: «${tour.title}». Тур необязательный — его можно пропустить в любой момент.`);
    const row = el('div', 'tour-actions');
    const yes = el('button', 'tour-btn tour-btn-main', 'Показать тур');
    yes.type = 'button';
    yes.addEventListener('click', () => start(launcher));
    const later = el('button', 'tour-btn tour-btn-ghost', 'Не сейчас');
    later.type = 'button';
    later.addEventListener('click', () => { mark('offered'); closeOffer(); });
    const never = el('button', 'tour-btn tour-btn-quiet', 'Больше не предлагать');
    never.type = 'button';
    never.addEventListener('click', () => { mute(); closeOffer(); toast('Хорошо. Тур всегда доступен по кнопке «?» внизу справа.'); });
    row.append(yes, later, never);
    offer.append(t, p, row);
    document.body.append(offer);
  }

  launcher.addEventListener('click', () => (layer ? stop(false) : start(launcher)));
  document.addEventListener('keydown', (e) => {
    if (e.key !== '?' || e.ctrlKey || e.metaKey || e.altKey || layer || phone.matches) return;
    const a = document.activeElement;
    if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))) return;
    e.preventDefault();
    start(launcher);
  });

  // Окно сузили до телефона (поворот, изменение размера): тур и предложение закрываются, кнопка прячется; на планшете и шире возвращается
  phone.addEventListener('change', () => {
    launcher.style.display = phone.matches ? 'none' : '';
    if (!phone.matches) return;
    if (layer) stop(false);
    closeOffer();
  });

  // ---------- автозапуск ----------
  const params = new URLSearchParams(location.search);
  if (params.get('tour') === '1') {
    params.delete('tour');
    history.replaceState(null, '', location.pathname + (params.toString() ? `?${params}` : '') + location.hash);
    setTimeout(() => start(launcher), 400);  // на телефоне start() ничего не покажет
    return;
  }
  const state = read();
  const seen = (state.done && state.done[tour.id]) || (state.offered && state.offered[tour.id]);
  const quiet = document.documentElement.getAttribute('data-a11y-sr') === '1';  // режим для экранного диктора: окно не всплывает само
  if (!isMuted() && !seen && !quiet && !document.querySelector('[data-celebrate]')) setTimeout(showOffer, 1200);
})();

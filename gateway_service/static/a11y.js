/* Специальные возможности: панель настроек, быстрые профили и озвучивание (Web Speech API).
   Настройки хранятся в localStorage ('fsp.a11y') и применяются атрибутами <html data-a11y-*>; тот же разбор делает ранний
   скрипт в <head> base.html, чтобы схема и размер были видны с первой отрисовки. Оформление режимов — a11y.css. */
(() => {
  const KEY = 'fsp.a11y';
  const root = document.documentElement;
  const DEFAULTS = { size: '100', theme: 'default', font: 'brand', spacing: '0', links: '0', focus: '0', cursor: '0', targets: '0', calm: '0', sr: '0', speak: '0' };
  const PRESETS = {
    'low-vision': { size: '150', theme: 'yb', font: 'plain', links: '1', focus: '1', calm: '1' },
    'screen-reader': { sr: '1', calm: '1', links: '1', focus: '1' },
    dyslexia: { size: '125', font: 'plain', spacing: '1', calm: '1' },
    motor: { size: '125', targets: '1', cursor: '1', focus: '1' },
    calm: { calm: '1' },
  };
  const LABELS = {
    size: (v) => `Размер текста ${v}%`,
    theme: (v) => ({ default: 'Обычная цветовая схема', bw: 'Чёрное на белом', wb: 'Белое на чёрном', yb: 'Жёлтое на чёрном' }[v]),
    font: (v) => (v === 'plain' ? 'Простой шрифт' : 'Фирменный шрифт'),
  };
  const TOGGLE_LABELS = {
    spacing: 'Увеличенные интервалы', links: 'Подчёркивание ссылок', focus: 'Яркая рамка фокуса', cursor: 'Крупный курсор',
    targets: 'Крупные кнопки и поля', calm: 'Спокойный режим', sr: 'Режим для экранного диктора', speak: 'Называть элемент при выборе',
  };

  // Исходное состояние — как при первом заходе: значения по умолчанию, а при системном prefers-contrast: more — «Белое на чёрном» (то же делает ранний скрипт в base.html)
  const initial = () => ({ ...DEFAULTS, rate: 1, ...(matchMedia('(prefers-contrast: more)').matches ? { theme: 'wb' } : {}) });
  const load = () => {
    try { return { ...initial(), ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch { return initial(); }
  };
  let state = load();
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* приватный режим */ } };
  const apply = () => Object.keys(DEFAULTS).forEach((k) => root.setAttribute(`data-a11y-${k}`, state[k]));

  const panel = document.getElementById('a11y-panel');
  const status = panel?.querySelector('[data-a11y-status]');
  const say = (text) => { if (status) { status.textContent = ''; setTimeout(() => { status.textContent = text; }, 30); } };

  const sync = () => {
    if (!panel) return;
    panel.querySelectorAll('input[data-a11y-set]').forEach((i) => { i.checked = state[i.dataset.a11ySet] === i.value; });
    panel.querySelectorAll('input[data-a11y-toggle]').forEach((i) => { i.checked = state[i.dataset.a11yToggle] === '1'; });
    const rate = panel.querySelector('[data-a11y-rate]');
    if (rate) rate.value = state.rate;
    const out = panel.querySelector('[data-a11y-rate-out]');
    if (out) out.textContent = `${Number(state.rate).toFixed(1)}×`;
    syncReset();
  };
  // Кнопка «Вернуть по умолчанию» неактивна, пока менять нечего
  const isInitial = () => Object.entries(initial()).every(([k, v]) => String(state[k]) === String(v));
  function syncReset() {
    const pristine = isInitial();
    panel?.querySelectorAll('[data-a11y-reset]').forEach((b) => { b.disabled = pristine; });
    const note = panel?.querySelector('[data-a11y-reset-note]');
    if (note) note.textContent = pristine ? 'Сейчас всё по умолчанию.' : 'Есть изменения — их можно сбросить одним нажатием.';
  }
  // Полный сброс: удаляем сохранённые настройки (а не записываем значения по умолчанию), как будто панель ещё не открывали
  const reset = () => {
    try { localStorage.removeItem(KEY); } catch { /* приватный режим */ }
    state = initial();
    apply(); sync(); stopSpeech();
    say('Все настройки сброшены к значениям по умолчанию');
  };

  const change = (patch, message) => {
    state = { ...state, ...patch };
    apply(); save(); sync();
    if (message) say(message);
  };

  // ---------- панель ----------
  if (panel) {
    const open = () => { sync(); if (!panel.open) panel.showModal(); };
    const close = () => { if (panel.open) panel.close(); };
    document.addEventListener('click', (e) => {
      if (e.target.closest('[data-a11y-open]')) { e.preventDefault(); open(); }
      else if (e.target.closest('[data-a11y-close]') || e.target === panel) close();
    });
    document.addEventListener('keydown', (e) => {
      if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyA') { e.preventDefault(); panel.open ? close() : open(); }
    });
    panel.addEventListener('change', (e) => {
      const set = e.target.closest('[data-a11y-set]');
      const tog = e.target.closest('[data-a11y-toggle]');
      if (set) change({ [set.dataset.a11ySet]: set.value }, LABELS[set.dataset.a11ySet](set.value));
      else if (tog) {
        const on = tog.checked ? '1' : '0';
        change({ [tog.dataset.a11yToggle]: on }, `${TOGGLE_LABELS[tog.dataset.a11yToggle]}: ${on === '1' ? 'включено' : 'выключено'}`);
        if (tog.dataset.a11yToggle === 'speak' && on === '0') stopSpeech();
      }
    });
    panel.addEventListener('input', (e) => {
      if (!e.target.matches('[data-a11y-rate]')) return;
      state.rate = Number(e.target.value); save();
      panel.querySelector('[data-a11y-rate-out]').textContent = `${state.rate.toFixed(1)}×`;
      syncReset();
    });
    panel.addEventListener('click', (e) => {
      const preset = e.target.closest('[data-a11y-preset]');
      if (preset) {
        change({ ...DEFAULTS, ...PRESETS[preset.dataset.a11yPreset] }, `Профиль «${preset.querySelector('span').textContent}» включён`);
        return;
      }
      if (e.target.closest('[data-a11y-reset]')) { reset(); return; }
      const read = e.target.closest('[data-a11y-read]');
      if (read) readAloud(read.dataset.a11yRead);
    });
  }
  apply();
  addEventListener('storage', (e) => { if (e.key === KEY) { state = load(); apply(); sync(); } });

  // ---------- озвучивание ----------
  const synth = 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window ? window.speechSynthesis : null;
  if (!synth) {
    panel?.querySelectorAll('[data-a11y-read], [data-a11y-rate], [data-a11y-toggle="speak"]').forEach((el) => { el.disabled = true; });
    panel?.querySelector('[data-a11y-nospeech]')?.classList.remove('hidden');
  }
  const stopSpeech = () => synth?.cancel();
  const chunks = (text) => {
    const parts = text.replace(/\s+/g, ' ').split(/(?<=[.!?…:;])\s+/);
    const out = [];
    let cur = '';
    for (const p of parts) {
      if ((cur + ' ' + p).length > 220 && cur) { out.push(cur); cur = p; } else cur = cur ? `${cur} ${p}` : p;
    }
    if (cur) out.push(cur);
    return out;
  };
  const speak = (text, { queue = false } = {}) => {
    if (!synth || !text) return;
    if (!queue) synth.cancel();
    const voice = synth.getVoices().find((v) => /^ru/i.test(v.lang));
    chunks(text).forEach((c) => {
      const u = new SpeechSynthesisUtterance(c);
      u.lang = 'ru-RU';
      if (voice) u.voice = voice;
      u.rate = Number(state.rate) || 1;
      synth.speak(u);
    });
  };
  function readAloud(what) {
    if (what === 'stop') { stopSpeech(); say('Озвучивание остановлено'); return; }
    if (what === 'selection') {
      const text = String(getSelection() || '').trim();
      if (!text) { say('Ничего не выделено. Выделите текст на странице и повторите.'); speak('Ничего не выделено'); return; }
      speak(text);
      return;
    }
    panel?.close();
    const main = document.getElementById('main');
    const title = document.title.split('·')[0].trim();
    speak(`${title}. ${main ? main.innerText : document.body.innerText}`);
  }
  addEventListener('pagehide', stopSpeech);

  // Название элемента при выборе клавишей Tab (для тех, кто не пользуется диктором, но хочет слышать подсказки)
  const ROLE = { A: 'ссылка', BUTTON: 'кнопка', SELECT: 'список', TEXTAREA: 'поле ввода', SUMMARY: 'раскрывающийся блок' };
  document.addEventListener('focusin', (e) => {
    if (state.speak !== '1' || !synth || panel?.contains(e.target)) return;
    const el = e.target;
    let name = el.getAttribute('aria-label') || '';
    if (!name && el.id) name = document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.textContent || '';
    if (!name) name = el.closest('label')?.textContent || el.getAttribute('placeholder') || el.getAttribute('title') || el.textContent || '';
    name = name.replace(/\s+/g, ' ').trim().slice(0, 160);
    if (!name) return;
    const role = el.tagName === 'INPUT' ? ({ checkbox: 'переключатель', radio: 'выбор', submit: 'кнопка', button: 'кнопка' }[el.type] || 'поле ввода') : ROLE[el.tagName] || '';
    speak(role ? `${name}, ${role}` : name);
  });
})();

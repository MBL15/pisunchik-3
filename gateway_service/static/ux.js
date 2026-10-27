/* Общие приёмы удобства интерфейса:
 *  · data-confirm — подтверждение необратимых действий в оформленном диалоге (формы и кнопки отправки);
 *  · блокировка кнопок и индикатор при отправке формы — защита от двойного нажатия;
 *  · закрытие сообщений [data-dismiss];
 *  · панель выбора в сборную на странице рейтинга ([data-pick-bar]);
 *  · большие списки: автоприменение фильтров [data-autosubmit], «/» — к поиску, размер страницы [data-nav-select],
 *    быстрый фильтр по странице [data-filter-input], массовый выбор [data-bulk]. */
(() => {
  // ---------- подтверждение ----------
  let dialog = null;
  const ask = (message, okLabel) => new Promise((resolve) => {
    if (typeof HTMLDialogElement === 'undefined') { resolve(confirm(message)); return; }
    dialog?.remove();
    dialog = document.createElement('dialog');
    dialog.className = 'ux-dialog';
    dialog.setAttribute('aria-labelledby', 'ux-dialog-text');
    const text = document.createElement('p');
    text.id = 'ux-dialog-text';
    text.className = 'ux-dialog-text';
    text.textContent = message;
    const row = document.createElement('div');
    row.className = 'ux-dialog-actions';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'tour-btn tour-btn-ghost';
    cancel.textContent = 'Отмена';
    const ok = document.createElement('button');
    ok.type = 'button';
    ok.className = 'tour-btn tour-btn-danger';
    ok.textContent = okLabel || 'Подтвердить';
    row.append(cancel, ok);
    dialog.append(text, row);
    document.body.append(dialog);
    let answer = false;
    ok.addEventListener('click', () => { answer = true; dialog.close(); });
    cancel.addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => { dialog.remove(); dialog = null; resolve(answer); }, { once: true });
    dialog.showModal();
    cancel.focus();  // безопасное действие по умолчанию — «Отмена»
  });

  document.addEventListener('submit', async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || form.dataset.confirmed === '1') return;
    const src = e.submitter?.dataset.confirm ? e.submitter : form;
    const message = src.dataset.confirm;
    if (!message) return;
    e.preventDefault();
    if (!(await ask(message, src.dataset.confirmLabel))) return;
    form.dataset.confirmed = '1';
    e.submitter ? form.requestSubmit(e.submitter) : form.requestSubmit();
  }, true);

  // ---------- защита от двойной отправки ----------
  document.addEventListener('submit', (e) => {
    const form = e.target;
    if (e.defaultPrevented || !(form instanceof HTMLFormElement) || form.target === '_blank') return;
    const sender = e.submitter;
    setTimeout(() => {  // после того, как браузер собрал данные формы (иначе значение кнопки потеряется)
      form.setAttribute('aria-busy', 'true');
      form.querySelectorAll('button:not([type=button]), input[type=submit]').forEach((b) => { b.disabled = true; });
      sender?.classList.add('is-pending');
    }, 0);
  });
  addEventListener('pageshow', (e) => {
    if (!e.persisted) return;
    document.querySelectorAll('form[aria-busy]').forEach((f) => {
      f.removeAttribute('aria-busy');
      delete f.dataset.confirmed;
      f.querySelectorAll('button:disabled, input[type=submit]:disabled').forEach((b) => { b.disabled = false; b.classList.remove('is-pending'); });
    });
  });

  // ---------- закрытие сообщений ----------
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-dismiss]');
    if (btn) btn.closest('[data-flash]')?.remove();
  });

  // ---------- выбор спортсменов для сборной ----------
  document.querySelectorAll('[data-pick-bar]').forEach((bar) => {
    const boxes = () => [...document.querySelectorAll('input[name="fids"]')];
    const submit = document.querySelector('[data-pick-submit]');
    const counter = bar.querySelector('[data-pick-count]');
    const sync = () => {
      const n = boxes().filter((b) => b.checked).length;
      if (counter) counter.textContent = String(n);
      if (submit) { submit.disabled = n === 0; submit.title = n ? '' : 'Сначала отметьте спортсменов в таблице рейтинга'; }
      bar.classList.toggle('is-active', n > 0);
    };
    bar.querySelectorAll('[data-pick-top]').forEach((b) => b.addEventListener('click', () => {
      const top = Number(b.dataset.pickTop);
      boxes().forEach((x, i) => { x.checked = i < top; });
      sync();
    }));
    bar.querySelector('[data-pick-clear]')?.addEventListener('click', () => { boxes().forEach((x) => { x.checked = false; }); sync(); });
    document.addEventListener('change', (e) => { if (e.target.matches?.('input[name="fids"]')) sync(); });
    sync();
  });

  // ---------- большие списки ----------
  const FOCUS_KEY = 'fsp.ux:focus';
  const typing = (el) => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));

  // Формы фильтров [data-autosubmit]: списки и флажки применяются сразу, поиск — через паузу в наборе.
  // Пустые поля не попадают в адрес; фокус и курсор в поиске восстанавливаются после перезагрузки.
  document.querySelectorAll('form[data-autosubmit]').forEach((form) => {
    const go = () => {
      const el = document.activeElement;
      try {
        if (el?.id && form.contains(el)) sessionStorage.setItem(FOCUS_KEY, JSON.stringify({ id: el.id, path: location.pathname }));
      } catch { /* приватный режим */ }
      form.requestSubmit();
    };
    form.addEventListener('submit', () => {
      form.querySelectorAll('input, select').forEach((f) => { if (f.name && f.value === '' && f.type !== 'hidden') f.disabled = true; });
    });
    addEventListener('pageshow', (e) => { if (e.persisted) form.querySelectorAll(':disabled').forEach((f) => { f.disabled = false; }); });
    form.addEventListener('change', (e) => { if (!e.target.matches('[data-autosubmit-input]')) go(); });
    let timer = null;
    form.querySelectorAll('[data-autosubmit-input]').forEach((input) => {
      let last = input.value;
      input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(() => { if (input.value.trim() !== last.trim()) { last = input.value; go(); } }, 450);
      });
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && input.value) { e.preventDefault(); input.value = ''; go(); }
      });
    });
  });
  try {
    const saved = JSON.parse(sessionStorage.getItem(FOCUS_KEY) || 'null');
    sessionStorage.removeItem(FOCUS_KEY);
    const el = saved && saved.path === location.pathname && document.getElementById(saved.id);
    if (el) {
      el.focus({ preventScroll: true });
      if (typeof el.setSelectionRange === 'function' && /^(search|text)$/.test(el.type)) el.setSelectionRange(el.value.length, el.value.length);
    }
  } catch { /* нет sessionStorage */ }

  // Телефон: дополнительные фильтры (.fsp-more) раскрываются кнопкой [data-filters-toggle]
  document.addEventListener('click', (e) => {
    const btn = e.target.closest?.('[data-filters-toggle]');
    if (!btn) return;
    const open = btn.closest('form').classList.toggle('is-open');
    btn.setAttribute('aria-expanded', String(open));
  });

  // «/» — к поиску на странице
  document.addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey || typing(document.activeElement)) return;
    const search = document.querySelector('[data-search]');
    if (!search) return;
    e.preventDefault();
    search.focus();
    search.select?.();
  });

  // Выпадающий список-навигация (размер страницы): значение — готовый адрес
  document.addEventListener('change', (e) => {
    if (e.target.matches?.('select[data-nav-select]')) location.href = e.target.value;
  });

  // Быстрый фильтр по уже загруженному списку: [data-filter-input data-filter-target="#id"] → элементы [data-filter-item]
  document.querySelectorAll('[data-filter-input]').forEach((input) => {
    const scope = document.querySelector(input.dataset.filterTarget);
    if (!scope) return;
    const items = [...scope.querySelectorAll('[data-filter-item]')];
    const counter = document.querySelector(input.dataset.filterCount || '#none');
    const empty = scope.querySelector('[data-filter-empty]');
    const norm = (s) => s.toLowerCase().replace(/ё/g, 'е');
    const texts = items.map((it) => norm(it.dataset.filterText || it.textContent));
    const apply = () => {
      const words = norm(input.value).split(/\s+/).filter(Boolean);
      let shown = 0;
      items.forEach((it, i) => {
        const ok = words.every((w) => texts[i].includes(w));
        it.hidden = !ok;
        if (it.dataset.filterWith) document.querySelectorAll(it.dataset.filterWith).forEach((x) => { if (!ok) x.classList.add('hidden'); });
        shown += ok;
      });
      if (counter) counter.textContent = words.length ? `${shown} из ${items.length}` : String(items.length);
      if (empty) empty.hidden = shown > 0;
    };
    input.addEventListener('input', apply);
    input.addEventListener('keydown', (e) => { if (e.key === 'Escape' && input.value) { input.value = ''; apply(); } });
    apply();
  });

  // Поиск внутри длинного списка: [data-option-filter="#select"] скрывает неподходящие варианты и выбирает первый подходящий
  document.querySelectorAll('[data-option-filter]').forEach((input) => {
    const select = document.querySelector(input.dataset.optionFilter);
    if (!select) return;
    const norm = (s) => s.toLowerCase().replace(/ё/g, 'е');
    const options = [...select.options];
    input.addEventListener('input', () => {
      const words = norm(input.value).split(/\s+/).filter(Boolean);
      let first = null;
      options.forEach((o) => {
        const ok = words.every((w) => norm(o.textContent + ' ' + o.value).includes(w));
        o.hidden = !ok;
        if (ok && !o.disabled && !first) first = o;
      });
      if (first && (select.selectedOptions[0]?.hidden ?? true)) first.selected = true;
    });
  });

  // Массовый выбор: [data-bulk] с флажками [data-bulk-item], «выбрать все» [data-bulk-all], счётчик [data-bulk-count],
  // кнопки [data-bulk-action] активны только при выборе. Shift+клик отмечает диапазон.
  document.querySelectorAll('[data-bulk]').forEach((root) => {
    const items = () => [...root.querySelectorAll('input[data-bulk-item]')].filter((b) => !b.closest('[hidden]'));
    const all = root.querySelector('input[data-bulk-all]');
    let lastIndex = null;
    const sync = () => {
      const list = items();
      const n = list.filter((b) => b.checked).length;
      root.querySelectorAll('[data-bulk-count]').forEach((c) => { c.textContent = String(n); });
      root.querySelectorAll('[data-bulk-action]').forEach((b) => { b.disabled = n === 0; });
      root.classList.toggle('is-selecting', n > 0);
      if (all) { all.checked = n > 0 && n === list.length; all.indeterminate = n > 0 && n < list.length; }
    };
    all?.addEventListener('change', () => { items().forEach((b) => { b.checked = all.checked; }); sync(); });
    root.addEventListener('click', (e) => {
      const box = e.target.closest?.('input[data-bulk-item]');
      if (!box) return;
      const list = items();
      const i = list.indexOf(box);
      if (e.shiftKey && lastIndex !== null) {
        const [a, b] = [Math.min(i, lastIndex), Math.max(i, lastIndex)];
        list.slice(a, b + 1).forEach((x) => { x.checked = box.checked; });
      }
      lastIndex = i;
      sync();
    });
    sync();
  });
  // ---------- Адаптация: таблицы-карточки, затухание у края прокрутки, меню шапки ----------
  // table.cards: ux.js проставляет подписи ячеек из заголовков столбцов (ячейки — плитки «подпись / значение»);
  // роли ARIA возвращают таблице семантику, которую CSS display:block у неё отнимает.
  const cellText = (th) => {
    if (!th) return '';
    const c = th.cloneNode(true);
    c.querySelectorAll('.sr-only, input, svg').forEach((n) => n.remove());
    return c.textContent.replace(/[▲▼↑↓]/g, '').replace(/\s+/g, ' ').trim();
  };
  document.querySelectorAll('table.cards').forEach((table) => {
    const heads = [...(table.tHead?.rows[table.tHead.rows.length - 1]?.cells || [])];
    table.setAttribute('role', 'table');
    table.tHead?.setAttribute('role', 'rowgroup');
    [...table.tBodies].forEach((b) => b.setAttribute('role', 'rowgroup'));
    table.querySelectorAll('tr').forEach((tr) => {
      tr.setAttribute('role', 'row');
      let col = 0, titled = false;
      [...tr.cells].forEach((td) => {
        if (td.tagName === 'TH') { td.setAttribute('role', 'columnheader'); return; }
        td.setAttribute('role', 'cell');
        const label = cellText(heads[col]);
        const span = td.colSpan || 1;
        col += span;
        if (!td.dataset.card) {
          if (!titled && !td.querySelector('input[type=checkbox]') ) td.dataset.card = 'title';
          else if (td.querySelector('input[type=checkbox]') && !titled) td.dataset.card = 'lead';
          else if (!label || td.querySelector('form, button, [data-confirm]')) td.dataset.card = label ? 'full' : 'actions';
          else if (span > 1) td.dataset.card = 'full';
        }
        if (td.dataset.card === 'title') titled = true;
        if (td.dataset.label === undefined) td.dataset.label = label;
      });
    });
  });

  // Горизонтально прокручиваемые обёртки: затухание там, где есть скрытое содержимое
  document.querySelectorAll('.overflow-x-auto').forEach((el) => {
    const upd = () => {
      const max = el.scrollWidth - el.clientWidth;
      if (max < 4) { el.removeAttribute('data-fade'); return; }
      const rtl = getComputedStyle(el).direction === 'rtl';
      const x = Math.abs(el.scrollLeft);
      const atStart = x < 4, atEnd = x > max - 4;
      el.dataset.fade = atStart && !atEnd ? (rtl ? 'start' : 'end') : !atStart && atEnd ? (rtl ? 'end' : 'start') : atStart ? '' : 'both';
      if (!el.dataset.fade) el.removeAttribute('data-fade');
    };
    el.addEventListener('scroll', upd, { passive: true });
    if ('ResizeObserver' in window) new ResizeObserver(upd).observe(el);
    window.addEventListener('resize', upd);
    upd();
  });

  // Меню шапки: закрывается по Esc, касанию вне панели и по переходу; открытое меню «Ещё» не мешает мобильному
  const menus = () => [...document.querySelectorAll('header details')];
  document.addEventListener('click', (e) => {
    menus().forEach((d) => { if (d.open && !d.contains(e.target)) d.open = false; });
    const a = e.target.closest?.('header details a[href]');
    if (a) a.closest('details').open = false;
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    menus().forEach((d) => { if (d.open) { d.open = false; d.querySelector('summary')?.focus(); } });
  });
  document.querySelectorAll('header details').forEach((d) => d.addEventListener('toggle', () => {
    if (d.open) menus().forEach((o) => { if (o !== d) o.open = false; });
  }));

})();

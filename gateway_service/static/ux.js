/* Общие приёмы удобства интерфейса:
 *  · data-confirm — подтверждение необратимых действий в оформленном диалоге (формы и кнопки отправки);
 *  · блокировка кнопок и индикатор при отправке формы — защита от двойного нажатия;
 *  · закрытие сообщений [data-dismiss];
 *  · панель выбора в сборную на странице рейтинга ([data-pick-bar]). */
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
})();

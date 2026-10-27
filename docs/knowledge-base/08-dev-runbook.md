# 8. Разработка и эксплуатация

## Окружение

- Python 3.14 через лаунчер **`py`** (на этой машине `python` указывает на заглушку Microsoft Store).
- Зависимости: `py -m pip install -r requirements.txt`.
- Для UI нужен интернет: Tailwind и шрифты грузятся с CDN.
- Проект лежит в OneDrive; `run_all.py` включает `WATCHFILES_FORCE_POLLING=true`, потому что события
  файловой системы в синхронизируемых папках ненадёжны.
- Папка не является git-репозиторием.

## Запуск

```powershell
py run_all.py              # три сервиса + live-reload
py run_all.py --no-reload  # без перезагрузки
```

Оркестратор поднимает `sportsman → competition → gateway`, ждёт `/health` каждого, печатает ссылки
и демо-аккаунты. Если порт занят — сообщает и выходит. Ctrl+C гасит всё дерево процессов.

Адреса: платформа `http://127.0.0.1:8000`, Swagger `:8001/docs` и `:8002/docs`.

### Запуск в фоне на Windows (без окна)

Консоль Windows в кодировке cp866 роняет сервисы на кириллице в логах (`UnicodeEncodeError`), поэтому
нужны `-X utf8` и `PYTHONIOENCODING=utf-8`:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
Start-Process py -ArgumentList '-X','utf8','run_all.py' -WorkingDirectory (Get-Location) -WindowStyle Hidden `
  -RedirectStandardOutput "$env:TEMP\fsp_stack.log" -RedirectStandardError "$env:TEMP\fsp_stack.err"
```

### Полный перезапуск

```powershell
Get-CimInstance Win32_Process -Filter "Name='py.exe'" | Where-Object { $_.CommandLine -match 'run_all' } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }
Get-NetTCPConnection -LocalPort 8000,8001,8002 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

Затем запустить снова. Перезапуск возвращает демо-данные к исходным.

## Тесты

```powershell
py -m unittest -v                 # движки: ФСП ID, парсер логов, рейтинг (10 тестов, без сети)
py -X utf8 tests/e2e_smoke.py     # сквозные сценарии через шлюз, нужен запущенный стек
```

e2e (`tests/e2e_smoke.py`) ходит как браузер (cookie, формы, редиректы) и проверяет:

- публичные страницы, токены дизайна, рейтинг (222.0, исключение Жукова), live-фрагмент, allowlist прокси;
- **каждый** демо-аккаунт: вход с нужной ролью и наличие текста `check` в кабинете;
- регистрацию (отказ без согласия 152-ФЗ, выдачу ФСП ID, переключение ролей, запрет эскалации до admin);
- кабинет тренера и судьи, заявку за спортсмена;
- кабинет федерации: загрузку лога с предупреждением о контрольной цифре, настройки рейтинга,
  справку с QR, проверку VALID/INVALID, публикацию live-протокола.

e2e меняет данные в памяти — после прогона перезапустите стек, если дальше будет демонстрация.

## Визуальная проверка

- **Публичные страницы** — headless Chrome:
  `"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --screenshot=out.png --window-size=1440,900 http://127.0.0.1:8000/`.
  В Git Bash задайте `MSYS_NO_PATHCONV=1`, если передаёте аргументы, начинающиеся с `/`.
- **Мобильная ширина** — Chrome не даёт окно уже ~500 px, поэтому проверяйте через страницу с
  `<iframe width="375">`.
- **Уведомления Telegram**: создайте `.env` в корне (`FSP_TELEGRAM_TOKEN=…`, `FSP_TELEGRAM_BOT=…`). В кабинете «Подключить Telegram» → в боте Start. Привязки лежат в `.telegram_links.json`. Два запущенных шлюза с одним токеном конфликтуют (long polling).
- **Кабинеты** требуют входа — удобнее встроенный браузер Cursor: открыть `/login` и нажать кнопку
  нужного демо-аккаунта. Анимации в headless/встроенном браузере могут замирать (троттлинг rAF) —
  для скриншота подождите пару секунд или прокрутите страницу.

## Типовые проблемы

| Симптом | Причина | Что делать |
|---|---|---|
| Сайт перестал открываться после правки `.py` или `.html` | На Windows перезагрузка gateway зависает, пока открытые вкладки держат соединения (live-опрос `/partials/live`) | Полный перезапуск стека |
| Страница показывает старое поведение после правки `main.py` | Перезагрузка не произошла (в логе нет новых запросов) | Полный перезапуск |
| `UnicodeEncodeError` при старте | Консоль cp866 | `py -X utf8`, `PYTHONIOENCODING=utf-8` |
| «Порты заняты» | Остался прошлый запуск | Остановить процессы на 8000–8002 |
| `[WARN] … деградированном режиме` | Один из сервисов недоступен | Посмотреть лог `%TEMP%\fsp_stack.log` |
| Кириллица ломается в однострочниках PowerShell | Кодировка конвейера | Писать скрипт в файл и запускать `py -X utf8 файл.py` |
| e2e падает на тексте в кабинете | Изменён текст, на который завязан `check` | Обновить `DEMO_ACCOUNTS[].check` в `shared/demo_roster.py` вместе с шаблоном |
| Синяя рамка вокруг элемента без причины | Свой класс совпал с утилитой Tailwind (`ring`, `shadow`…) | Переименовать класс |

## Как добавить…

**Страницу**: маршрут в `gateway_service/main.py` → `render(request, "x.html", user=…, …)`; шаблон
`{% extends "base.html" %}{% import "macros.html" as m %}`; данные — через `asyncio.gather(safe(…))`,
чтобы страница переживала падение сервиса.

**Эндпоинт сервиса**: маршрут в `*/main.py`, модель в `models.py`, состояние в `store.py`;
действие пишите в журнал `store.log(actor, action, obj)`. Если эндпоинт должен быть публичным —
добавьте его в `_PUBLIC_ROUTES` шлюза осознанно (без персональных данных).

**Демо-сценарий**: человек в `_ROWS`, аккаунт в `DEMO_ACCOUNTS` (с `check`), при необходимости связь
в `TRAINER_LINKS`, заявка в `seed.APPLICATIONS`; e2e подхватит аккаунт автоматически.

**Эффект**: по возможности переиспользуйте data-атрибуты ([UI](06-ui-design-system.md#система-эффектов-data-атрибуты));
новый — с поддержкой reduced-motion и без скрытия контента при отключённом JS.

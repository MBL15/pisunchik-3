# 8. Разработка и эксплуатация

## Окружение

- Python 3.14 через лаунчер **`py`** (на этой машине `python` указывает на заглушку Microsoft Store).
- Зависимости: `py -m pip install -r requirements.txt` (в том числе `openpyxl`, `python-docx`, `reportlab` для выгрузки документов).
  Удобно работать в `.venv` (в `.gitignore`).
- Для выгрузки в PDF нужен TTF-шрифт с кириллицей — Arial, DejaVu или Liberation находятся сами;
  иначе укажите `FSP_PDF_FONT` (и `FSP_PDF_FONT_BOLD`).
- Для UI нужен интернет: Tailwind и шрифты грузятся с CDN.
- Проект лежит в OneDrive; `run_all.py` включает `WATCHFILES_FORCE_POLLING=true`, потому что события
  файловой системы в синхронизируемых папках ненадёжны.
- Папка — git-репозиторий (`origin`: MBL15/pisunchik-3).

## Запуск

``powershell
py run_all.py              # три сервиса + live-reload
py run_all.py --no-reload  # без перезагрузки
``

Оркестратор поднимает `sportsman → competition → gateway`, ждёт `/health` каждого, печатает ссылки
и демо-аккаунты. Если порт занят — сообщает и выходит. Ctrl+C гасит всё дерево процессов.

Адреса: платформа `http://127.0.0.1:8000`, Swagger `:8001/docs`, `:8002/docs` и `:8003/docs` (биржа).

### Запуск в фоне на Windows (без окна)

Консоль Windows в кодировке cp866 роняет сервисы на кириллице в логах (`UnicodeEncodeError`), поэтому
нужны `-X utf8` и `PYTHONIOENCODING=utf-8`:

``powershell
$env:PYTHONIOENCODING = 'utf-8'
Start-Process py -ArgumentList '-X','utf8','run_all.py' -WorkingDirectory (Get-Location) -WindowStyle Hidden `
  -RedirectStandardOutput "$env:TEMP\fsp_stack.log" -RedirectStandardError "$env:TEMP\fsp_stack.err"
``

### Полный перезапуск

``powershell
Get-CimInstance Win32_Process -Filter "Name='py.exe'" | Where-Object { $_.CommandLine -match 'run_all' } |
  ForEach-Object { taskkill /PID $_.ProcessId /T /F | Out-Null }
Get-NetTCPConnection -LocalPort 8000,8001,8002,8003 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
``

Затем запустить снова. Перезапуск возвращает демо-данные к исходным.

## Тесты

``powershell
py -m unittest -v                 # движки, сервисы через TestClient, выгрузки (без сети и без запущенного стека)
py -X utf8 tests/e2e_smoke.py     # сквозные сценарии через шлюз, нужен запущенный стек
``

e2e (`tests/e2e_smoke.py`) ходит как браузер (cookie, формы, редиректы) и проверяет:

- публичные страницы, токены дизайна, рейтинг (222.0, исключение Жукова), live-фрагмент, allowlist прокси;
- **каждый** демо-аккаунт: вход с нужной ролью и наличие текста `check` в кабинете;
- регистрацию (отказ без согласия 152-ФЗ, выдачу ФСП ID, переключение ролей, запрет эскалации до admin);
- кабинет тренера и судьи, заявку за спортсмена;
- кабинет федерации: загрузку лога с предупреждением о контрольной цифре, настройки рейтинга,
  справку с QR, проверку VALID/INVALID, публикацию live-протокола;
- инструменты федерации: справочник типов, ручное закрытие/открытие приёма заявок (в т.ч. отказ в заявке при закрытом приёме),
  подсветку ЕВСК, отчёт о численности и выгрузки .xlsx/.docx/.pdf (по магическим байтам), сборную из рейтинга с утверждением
  приказом, права на отчёт спортсмена (сам, тренер подопечного, чужой — 403);
- реестр (фильтры, организации, выгрузки), ленту уведомлений, антидопинг, сроки отчётов;
- большие списки: страницы и сортировку, счётчики фильтров, массовую модерацию, поиск по журналу; защиту от Enter в
  форме массового решения, фильтры вкладок реестра, «прочитано только показанное».

Юнит-тесты по разделам: `test_engines` (ФСП ID, парсер, рейтинг), `test_qualification`, `test_competition_admin`,
`test_evsk`, `test_headcount`, `test_teams`, `test_exports`, `test_tours`, `test_registry` (реестр, организации, лента,
сроки и напоминания), `test_listing` (поиск, фасеты, страницы, массовые решения, чипы и вкладки, `FSP_DEMO_SCALE`).

Проверка неиспользуемых и неопределённых имён (не в `requirements.txt`, ставится отдельно):

```powershell
py -m pip install pyflakes
py -m pyflakes gateway_service sportsman_service competition_service shared tests
```

e2e меняет данные (в памяти или в Postgres) — после прогона сбросьте состояние (перезапуск стека; с БД — `docker compose down -v`), если дальше будет демонстрация. Юнит-тесты запускайте без `FSP_DATABASE_URL` (они рассчитаны на in-memory; с включённой БД часть падает).

## Визуальная проверка

- **Публичные страницы** — headless Chrome:
  `"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --screenshot=out.png --window-size=1440,900 http://127.0.0.1:8000/`.
  В Git Bash задайте `MSYS_NO_PATHCONV=1`, если передаёте аргументы, начинающиеся с `/`.
- **Мобильная ширина** — Chrome не даёт окно уже ~500 px, поэтому проверяйте через страницу с
  `<iframe width="375">`.
- **Уведомления Telegram**: создайте `.env` в корне (`FSP_TELEGRAM_TOKEN=…`, `FSP_TELEGRAM_BOT=…`). В кабинете «Подключить Telegram» → в боте Start. Привязки лежат в `.telegram_links.json`. Два запущенных шлюза с одним токеном конфликтуют (long polling). На боевом сервере токен и имя бота лежат только в серверном `.env` (в репозиторий не попадают); пока бот работает на сервере, локальный стек запускайте без `FSP_TELEGRAM_TOKEN`.
- **Кабинеты** требуют входа — удобнее встроенный браузер Cursor: открыть `/login` и нажать кнопку
  нужного демо-аккаунта. Анимации в headless/встроенном браузере могут замирать (троттлинг rAF) —
  для скриншота подождите пару секунд или прокрутите страницу.

## Типовые проблемы

| Симптом | Причина | Что делать |
|---|---|---|
| Сайт перестал открываться после правки `.py` или `.html` | На Windows перезагрузка gateway зависает, пока открытые вкладки держат соединения (live-опрос `/partials/live`) | Полный перезапуск стека |
| Страница показывает старое поведение после правки `main.py` | Перезагрузка не произошла (в логе нет новых запросов) | Полный перезапуск |
| `UnicodeEncodeError` при старте | Консоль cp866 | `py -X utf8`, `PYTHONIOENCODING=utf-8` |
| «Порты заняты» | Остался прошлый запуск | Остановить процессы на 8000–8003 |
| `[WARN] … деградированном режиме` | Один из сервисов недоступен | Посмотреть лог `%TEMP%\fsp_stack.log` |
| Кириллица ломается в однострочниках PowerShell | Кодировка конвейера | Писать скрипт в файл и запускать `py -X utf8 файл.py` |
| e2e падает на тексте в кабинете | Изменён текст, на который завязан `check` | Обновить `DEMO_ACCOUNTS[].check` в `shared/demo_roster.py` вместе с шаблоном |
| Синяя рамка вокруг элемента без причины | Свой класс совпал с утилитой Tailwind (`ring`, `shadow`…) | Переименовать класс |

## Как добавить…

**Страницу**: маршрут в `gateway_service/main.py` или — для отдельного раздела — `APIRouter` в `gateway_service/routes/`
(подключается в `main.py` через `app.include_router`); общий слой (`clients`, `render`, `require`, `back`, `safe`,
`download`) берите из `gateway_service/web.py`. → `render(request, "x.html", user=…, …)`; шаблон
`{% extends "base.html" %}{% import "macros.html" as m %}`; данные — через `asyncio.gather(safe(…))`,
чтобы страница переживала падение сервиса.

**Эндпоинт сервиса**: маршрут в `*/main.py`, модель в `models.py`, состояние в `store.py`;
действие пишите в журнал `store.log(actor, action, obj)`. Если эндпоинт должен быть публичным —
добавьте его в `_PUBLIC_ROUTES` шлюза осознанно (без персональных данных).

**Демо-сценарий**: человек в `_ROWS`, аккаунт в `DEMO_ACCOUNTS` (с `check`), при необходимости связь
в `TRAINER_LINKS`, заявка в `seed.APPLICATIONS`; e2e подхватит аккаунт автоматически.

**Большой список**: фильтрация, сортировка и страница — в сервисе через `shared/listing.py` (`tokens`/`matches`, `page`,
`facets`); в шлюзе — `list_args`, `pages`, `chips` из `listing_ui.py`; в шаблоне — форма `data-autosubmit`, `m.search_box`,
`m.facet_option`, `m.sort_th`, `m.pager`, `m.filter_chips` ([UI](06-ui-design-system.md#большие-списки)).

**Эффект**: по возможности переиспользуйте data-атрибуты ([UI](06-ui-design-system.md#система-эффектов-data-атрибуты));
новый — с поддержкой reduced-motion и без скрытия контента при отключённом JS.

## Проверка на больших объёмах

`FSP_DEMO_SCALE=3000 py -X utf8 run_all.py` добавляет 3000 вымышленных людей (≈ 85 % спортсменов) и ≈ 650 их заявок на
открытые старты; генерация детерминирована. Под ними не войти, в протоколы и рейтинг они не попадают. e2e и юнит-тесты
запускайте без переменной (e2e рассчитан на базовые демо-данные). Ориентиры на 5000 людей: страница реестра с фасетами —
≈ 50 мс в сервисе, выгрузка всего реестра в .xlsx — ≈ 1–2 с.

## Postgres (опционально)

`docker compose up -d postgres` поднимает только Postgres 17 (fsp/fsp, БД fsp). Включение: `FSP_DATABASE_URL=postgresql://fsp:fsp@127.0.0.1:5432/fsp` (можно в .env). Без переменной — in-memory, как раньше. Таблицы `sp_*` (sportsman) и `comp_*` (competition) создаются автоматически; первый запуск засевает демо-данные, дальше состояние берётся из БД и пишется после каждого не-GET запроса (`shared/db.py`). Сброс: `docker compose down -v`.

## Telegram через туннель (если хостинг блокирует api.telegram.org)

Бот ходит по Bot API (HTTPS на `api.telegram.org`). Если сервер до него не достаёт (проверка: `curl -4 -m 8 https://api.telegram.org/`), шлюз можно пустить через SOCKS5 — `FSP_TELEGRAM_PROXY`
(`shared/config.py`, клиент — `notify.make_client()`; логин и пароль прокси в журнал не пишутся). Бесплатная схема — обратный SSH-туннель со своего компьютера, откуда Telegram открывается:

1. На сервере в `.env`: `COMPOSE_FILE=docker-compose.yml:docker-compose.telegram-tunnel.yml` и `FSP_TELEGRAM_PROXY=socks5://host.docker.internal:1081`, затем `docker compose up -d --build`.
   Надстройка `docker-compose.telegram-tunnel.yml` добавляет контейнер `tg-relay` (socat в сети хоста): он пробрасывает `127.0.0.1:1080` сервера на `172.17.0.1:1081` (docker0) — единственный адрес, который видят контейнеры; шлюзу в `docker-compose.yml` уже добавлен `host.docker.internal` → хост. Настройки sshd не меняются, порт наружу не открыт.
2. На своём компьютере держать открытым `scripts/telegram_tunnel.ps1 -Key <ключ.pem> -Server ubuntu@<ip>` (по сути `ssh -N -R 127.0.0.1:1080 …` — динамический SOCKS5-форвард; при обрыве поднимается заново). Пока окно открыто, уведомления идут; без туннеля работает только лента кабинета.
3. Проверка с сервера: `curl -s --socks5-hostname 127.0.0.1:1080 https://api.telegram.org/` отвечает (404 с JSON тоже ответ), в журнале шлюза — строка «telegram: через прокси socks5://host.docker.internal:1081».

Ограничения: бот работает, только пока компьютер включён и туннель поднят; постоянное решение — обратный прокси на хосте, откуда Telegram доступен (Cloudflare Worker, VPS).

## Всё в Docker

`docker compose up -d --build` поднимает Postgres, sportsman, competition, market и gateway. Наружу опубликован только шлюз (http://localhost:8000); сервисы доступны по внутренней сети compose и требуют служебный токен (`FSP_SERVICE_TOKEN`), Postgres слушает только `127.0.0.1:5432`. Порты сервисов для отладки открывайте в `docker-compose.override.yml`. Состояние хранится в томе `fsp_pg`; `docker compose down -v` — полный сброс. Переменные (`FSP_SECRET_KEY`, `FSP_PUBLIC_BASE_URL`, `FSP_TELEGRAM_TOKEN`, `FSP_DEMO_TODAY`) берутся из окружения или `.env`. Порты 8000–8003 должны быть свободны (остановите `py run_all.py`).

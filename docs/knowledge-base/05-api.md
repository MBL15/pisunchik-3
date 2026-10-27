# 5. API сервисов

Swagger: `http://127.0.0.1:8001/docs` (sportsman), `http://127.0.0.1:8002/docs` (competition).
Все сервисы отдают `GET /health`. Ошибки — стандартный FastAPI `{"detail": …}` с русским текстом;
шлюз превращает их в одну фразу (`humanize`) для flash `[ERR]`.

## sportsman_service :8001

| Метод и путь | Назначение | Правила |
|---|---|---|
| `GET /api/v1/persons` | Список (фильтры `role`, `discipline`, `municipality`, …) | — |
| `GET /api/v1/persons/{fsp_id}` | Карточка участника с вычисляемыми полями | `404` |
| `POST /api/v1/persons` | Регистрация, выдача ФСП ID | email уникален (`409`), возраст ≥ 6, представитель для < 18 |
| `POST /api/v1/persons/lookup` | ФИО/муниципалитет по списку ФСП ID | Для парсера протоколов |
| `POST /api/v1/auth/verify` | Проверка email + пароль | `401` при ошибке |
| `GET /api/v1/stats` | Агрегаты реестра (по дисциплинам, муниципалитетам, годам) | Публичный через шлюз |
| `GET /api/v1/audit?limit=` | Журнал действий | ≤ 200 |
| `GET /api/v1/sanctions/active?on=` | Действующие санкции на дату | — |
| `GET /api/v1/alerts/ranks?within_days=&trainer_fsp_id=` | Истекающие разряды (по тренеру — только активные подопечные) | 0…730 дней |
| `GET /api/v1/alerts/judge-categories?within_days=` | Истекающие судейские категории | — |
| `GET /api/v1/trainers/{fsp_id}/athletes?with_performance=` | Связи тренера (+ сводка выступлений) | `403`, если нет роли тренера; порядок PENDING → ACTIVE → SUSPENDED → ENDED |
| `GET /api/v1/athletes/{fsp_id}/links` | Связи спортсмена | — |
| `POST /api/v1/links` | Запрос на привязку `{trainer_fsp_id, athlete_fsp_id, discipline}` | См. [домен](03-domain.md#связь-тренер--спортсмен-trainerlink) |
| `POST /api/v1/links/{id}/status` | Переход статуса `{status, actor_fsp_id, note}` | Только участники связи; подтверждает только спортсмен |
| `GET /api/v1/persons/{fsp_id}/performance` | История выступлений | `422` на невалидный ФСП ID |
| `GET /api/v1/reports/headcount?years=&discipline=&municipality=&organization=` | Численность занимающихся на конец каждого из последних лет, разрезы, динамика | `years` 2–6, по умолчанию 3; `422` на неизвестный фильтр |

## competition_service :8002

| Метод и путь | Назначение | Правила |
|---|---|---|
| `GET /api/v1/competitions` | Календарь (`discipline`, `status`, `plan`, `format`, `season`, `upcoming`) | — |
| `GET /api/v1/competitions/{id}` | Соревнование | `404` |
| `POST /api/v1/competitions` | Создание | Статус по сроку приёма заявок |
| `POST /api/v1/competitions/{id}/registration` | Ручное открытие / закрытие / продление приёма заявок `{action, deadline?, actor}` | `409` не открыт / уже открыт; `422` срок прошёл, позже старта, соревнование идёт или завершено |
| `GET/POST /api/v1/competition-types` | Справочник типов соревнований (с числом использований); добавить `{title, actor}` | `409` дубль (без учёта регистра); `422` название |
| `DELETE /api/v1/competition-types?title=&actor=` | Удалить неиспользуемый тип | `409` используется; `404` нет |
| `GET /api/v1/evsk/requirements` | Таблица требований ЕВСК, по которой идёт подсветка | — |
| `GET /api/v1/evsk/candidates?competition_id=&since_days=` | Результаты спортсменов региона, выполняющие требования ЕВСК, с состоянием `assign/confirm/have/unknown` | `since_days` 1–1460, по умолчанию 365 |
| `GET /api/v1/teams?discipline=&season=` · `GET /api/v1/teams/{id}` | Сборные | `404` |
| `POST /api/v1/teams` | Сформировать сборную из рейтинга `{title, discipline, season, purpose, age_group, min_rank, fsp_ids[], actor}` | `422` не в рейтинге / санкция / дубль; `503` реестр недоступен |
| `PUT /api/v1/teams/{id}` · `DELETE /api/v1/teams/{id}?actor=` | Название и цель · удалить черновик | `409` состав утверждён |
| `POST /api/v1/teams/{id}/members` · `DELETE …/members/{fsp_id}?actor=` · `POST …/members/{fsp_id}/role` | Добавить в резерв · исключить · основной состав / резерв | `409` дубль или утверждён; `422` нет в рейтинге или не останется основного состава |
| `POST /api/v1/teams/{id}/approve` | Утвердить приказом `{order_no, order_date, actor}` | `422` нет номера приказа; `409` уже утверждён |
| `GET /api/v1/competitions/{id}/protocol` | Протокол | — |
| `GET /api/v1/competitions/{id}/protocol/entry-sheet` | Лист ввода: участники с одобренными заявками, внесённые результаты, заявки на модерации, текущий черновик | — |
| `POST /api/v1/competitions/{id}/protocol/results` | Ввод результатов `{results: [{fsp_id, solved, penalty, score, dsq, dsq_reason}], actor, publish}` → протокол ранжируется автоматически | `422`: старт не наступил, нет одобренных заявок, участник без одобренной заявки, дубль; `409`: уже опубликован или live из лога |
| `POST /api/v1/competitions/{id}/protocol/ingest` | Загрузка лога `{log, actor}` | `422`, если нет участников; предупреждения парсера в ответе |
| `POST /api/v1/competitions/{id}/live/tick` | Следующий шаг live-потока | Не чаще 1.5 с |
| `POST /api/v1/competitions/{id}/protocol/publish` | Публикация → `FINISHED` | `404` нет протокола, `409` уже опубликован |
| `GET /api/v1/results?fsp_ids=a,b&season=` | Опубликованные результаты участников | — |
| `GET/PUT /api/v1/rating/settings` | Таблица баллов, коэффициент, минимум призовых | PUT пересчитывает все протоколы |
| `GET /api/v1/rating?discipline=&season=&age_group=&min_rank=` | Рейтинг сезона с разбором | `422` неизвестная дисциплина |
| `GET /api/v1/applications` | Заявки (`competition_id`, `athlete_fsp_id`, `submitted_by`, `status`) | Новые сверху |
| `POST /api/v1/applications` | Подача `{competition_id, athlete_fsp_id, submitted_by}` | 6 проверок допуска, см. [домен](03-domain.md#заявка-application) |
| `POST /api/v1/applications/{id}/decision` | Решение `{status, comment, actor}` | Только из `PENDING` |
| `GET /api/v1/judges/{fsp_id}/assignments` | Назначения `{upcoming, past}` | — |
| `GET /api/v1/dashboard` | KPI федерации (сезон, live, ближайшие, дедлайны ≤ 45 дн, заявки, справки, премия Губернатора) | — |
| `POST /api/v1/documents/participation` | Справка `{fsp_id, competition_id, issued_by, purpose}` | Только по опубликованному протоколу и участнику |
| `GET /api/v1/documents/{number}` | Справка | `404` |
| `GET /api/v1/documents/{number}/verify?code=` | Проверка подлинности | `{valid, document}` |
| `GET /api/v1/audit?limit=` | Журнал действий | — |

## gateway_service :8000 (HTML и формы)

| Путь | Роль | Что делает |
|---|---|---|
| `GET /`, `/calendar`, `/rating`, `/register`, `/login` | все | Публичные страницы |
| `GET /partials/live/{id}` | все | HTML-фрагмент live-протокола (опрос каждые 2.5 с) |
| `GET /verify/{number}?c=` | все | Публичная проверка справки |
| `POST /register` | — | Регистрация, вход и баннер с ФСП ID |
| `POST /login`, `/logout`, `/role` | — | Сессия и переключение активной роли. `GET /login` и `GET /register` для вошедшего редиректят в `/cabinet`; выход показывает flash «Вы вышли из аккаунта» |
| `POST /cabinet/telegram` | любая | `action=link` — редирект на `t.me/<бот>?start=<код>`; `action=unlink` — отключить Telegram |
| `GET /cabinet` | любая | Кабинет по активной роли |
| `POST /cabinet/links` | тренер | Запрос на привязку |
| `POST /cabinet/links/{id}/status` | тренер, спортсмен | Смена статуса связи (конфетти при `ACTIVE`) |
| `POST /cabinet/applications` | тренер, спортсмен | Подача заявки → `/cabinet#starts` или `#apps`, подсветка и конфетти |
| `POST /cabinet/admin/applications/{id}` | федерация | Решение по заявке |
| `POST /cabinet/admin/rating-settings` | федерация | Настройки рейтинга |
| `POST /cabinet/admin/protocol/{id}/results` | федерация | Ввод результатов: `action=draft` — черновик, `action=publish` — публикация и переход на протокол |
| `POST /cabinet/admin/protocol/{id}/ingest` / `publish` | федерация | Загрузка лога / публикация |
| `GET /protocols/{id}` | все (черновик — федерация) | Протокол по форме для печати / PDF |
| `POST /cabinet/admin/competitions` | федерация | Создание соревнования |
| `POST /documents/participation` | федерация, спортсмен | Справка (спортсмен — только на себя) |
| `POST /cabinet/admin/competitions/{id}/registration` | федерация | Открыть / закрыть / продлить приём заявок (`action`, `deadline`) |
| `POST /cabinet/admin/competition-types` · `…/remove` | федерация | Пополнить справочник типов · удалить неиспользуемый |
| `GET /reports/headcount` | федерация | Отчёт о численности с фильтрами (`years`, `discipline`, `municipality`, `organization`) |
| `GET /reports/headcount/export.{xlsx,docx,pdf}` | федерация | Выгрузка отчёта о численности |
| `GET /protocols/{id}/export.{xlsx,docx,pdf}` | все (черновик — федерация) | Выгрузка протокола |
| `GET /athletes/{fsp_id}/report.{xlsx,docx,pdf}` | сам спортсмен, его тренер с активной привязкой, федерация | Отчёт о соревнованиях спортсмена |
| `POST /cabinet/admin/teams` | федерация | Сформировать сборную из отмеченных на `/rating` спортсменов (`fids`, фильтры, `title`, `purpose`) |
| `GET /teams` · `GET /teams/{id}` | федерация | Список сборных · состав, правка, утверждение |
| `POST /cabinet/admin/teams/{id}/update` · `members` · `members/{fsp_id}/remove` · `members/{fsp_id}/role` · `approve` · `delete` | федерация | Действия над сборной |
| `GET /teams/{id}/{roster,order}.{xlsx,docx,pdf}` | федерация | Список сборной · проект приказа |
| `GET /documents/{number}` | владелец, федерация | Печатная справка с QR |
| `GET /api/public/{service}/{path}` | все | Allowlist-прокси, см. [архитектуру](02-architecture.md#публичный-api-шлюз) |
| `GET /health` | все | Сводный статус трёх сервисов (его показывает терминал на главной) |

Формы используют паттерн POST → 303 Redirect → GET с flash-сообщением (`back(url, kind, text, **extra)`).

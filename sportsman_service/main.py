"""sportsman_service (port 8001): unified registry, FSP ID issuance, trainer↔athlete links, performance history."""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from shared import config
from shared.domain import (
    AGE_GROUPS, DISCIPLINE_BY_CODE, JUDGE_CATEGORY_BY_CODE, MUNICIPALITIES, NO_ORGANIZATION, ORGANIZATION_INFO,
    ORGANIZATIONS, RANK_BY_CODE, RANK_ORDER, TRAINING_STAGES, ApplicationStatus, LinkStatus, Role,
)
from shared.fsp_id import is_valid_fsp_id
from shared.listing import AUDIT_LIMIT, MAX_LIMIT, audit_page, facets, matches, norm, page, search_audit, tokens
from shared.http import ServiceClient, ServiceError
from sportsman_service import groups, norms, oidc, squads
from sportsman_service.headcount import build_report
from sportsman_service.models import (
    Antidoping, AntidopingIn, AuthIn, DisciplinesIn, Guardian, Sanction, SanctionIn, JudgeCategoryRecord, LinkCreateIn, LinkEvent, LinkStatusIn, LookupIn,
    NotificationIn, NotificationsReadIn, PersonRecord, QualificationDecisionIn, QualificationIn, QualificationRequest, RankRecord, RegistrationIn,
)
from sportsman_service.security import hash_password, verify_password
from shared import service_auth
from shared.db import Persistence
from sportsman_service.store import DDL, age_on, restore, seed, snapshot, store

db = Persistence(DDL)
_NO_ACCOUNT_HASH = hash_password("no-such-account")
clients: dict[str, ServiceClient] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    if db.enabled:
        db.init()
        rows = db.load()
        if rows["sp_persons"]:
            restore(store, rows)
            db.mark_loaded(snapshot(store))
        else:
            seed(store)
            db.save(snapshot(store))
    else:
        seed(store)
    clients["competition"] = ServiceClient("competition_service", config.COMPETITION_URL, caller="sportsman_service")
    yield
    await clients["competition"].aclose()


app = FastAPI(title="ФСП Оренбург · Sportsman Service", version="1.0.0", lifespan=lifespan)
service_auth.install(app)
app.include_router(norms.router)
app.include_router(groups.router)
app.include_router(oidc.router)
app.include_router(squads.router)


@app.middleware("http")
async def persist_writes(request: Request, call_next):
    response = await call_next(request)
    if db.enabled and request.method != "GET":
        await run_in_threadpool(lambda: db.save(snapshot(store)))
    return response

# Allowed link transitions: (from, to) -> who may perform it.
_TRANSITIONS: dict[tuple[str, str], set[str]] = {
    (LinkStatus.PENDING, LinkStatus.ACTIVE): {"athlete"},
    (LinkStatus.PENDING, LinkStatus.ENDED): {"athlete", "trainer"},
    (LinkStatus.ACTIVE, LinkStatus.SUSPENDED): {"athlete", "trainer"},
    (LinkStatus.ACTIVE, LinkStatus.ENDED): {"athlete", "trainer"},
    (LinkStatus.SUSPENDED, LinkStatus.ACTIVE): {"athlete", "trainer"},
    (LinkStatus.SUSPENDED, LinkStatus.ENDED): {"athlete", "trainer"},
}


def _person(fsp_id: str) -> PersonRecord:
    p = store.persons.get(fsp_id)
    if not p:
        raise HTTPException(404, f"Участник {fsp_id} не найден в реестре")
    return p


def _on(on: date | None) -> date:
    return on or config.today()


@app.get("/health")
async def health():
    return {"service": "sportsman_service", "status": "ok", "persons": len(store.persons), "links": len(store.links)}


# ---------------------------------------------------------------- registry
# Фильтры реестра с фасетами. `rank` — точный разряд или `none` (без разряда); `stage` — код этапа или `none`.
REGISTRY_FACETS = ("role", "discipline", "organization", "municipality", "rank", "stage", "age_group")
REGISTRY_SORTS = {
    "name": lambda p: (norm(p.last_name), norm(p.first_name), norm(p.middle_name)),
    "age": lambda p: (-p.birth_date.toordinal(), norm(p.last_name)),
    "rank": lambda p: (RANK_ORDER[p.rank.code] if p.rank else -1, norm(p.last_name)),
    "organization": lambda p: (norm(p.organization), norm(p.last_name)),
    "municipality": lambda p: (norm(p.municipality), norm(p.last_name)),
    "start_year": lambda p: (p.start_year, norm(p.last_name)),
    "created": lambda p: (p.created_at, p.seq),
}


def _facet_values(p: PersonRecord, age: int) -> dict:
    return {"role": p.roles, "discipline": p.disciplines, "organization": p.organization, "municipality": p.municipality,
            "rank": p.rank.code if p.rank else "none", "stage": p.training_stage or "none",
            "age_group": [g for g, (lo, hi) in AGE_GROUPS.items() if lo <= age <= hi]}


def _hit(values: dict, key: str, want: str) -> bool:
    v = values[key]
    return want in v if isinstance(v, list) else v == want


@dataclass
class PersonFilters:
    """Фильтры реестра из query-параметров — одни и те же для полного списка и для страниц."""
    role: str | None = None
    discipline: str | None = None
    municipality: str | None = None
    organization: str | None = None
    rank: str | None = None
    stage: str | None = None
    age_group: str | None = None
    q: str | None = None
    min_rank: str | None = None

    def facet_filters(self) -> dict:
        return {k: getattr(self, k) for k in REGISTRY_FACETS}


def _registry_rows(f: PersonFilters) -> list[tuple[PersonRecord, dict]]:
    """Люди, прошедшие поиск и `min_rank`, с их значениями фасетов (фильтры по фасетам — отдельно, см. `_filtered`)."""
    if f.age_group and f.age_group not in AGE_GROUPS:
        raise HTTPException(422, "Неизвестная возрастная группа")
    today, words = config.today(), tokens(f.q)
    out = []
    for p in store.persons.values():
        if words and not matches(words, p.last_name, p.first_name, p.middle_name, p.fsp_id, p.email, p.organization,
                                 p.municipality):
            continue
        if f.min_rank and (not p.rank or RANK_ORDER[p.rank.code] < RANK_ORDER.get(f.min_rank, 0)):
            continue
        out.append((p, _facet_values(p, age_on(p.birth_date, today))))
    return out


def _filtered(rows: list[tuple[PersonRecord, dict]], filters: dict, skip: str | None = None) -> list:
    active = [(k, v) for k, v in filters.items() if v and k != skip]
    return [(p, vals) for p, vals in rows if all(_hit(vals, k, v) for k, v in active)]


@app.get("/api/v1/persons")
async def list_persons(f: PersonFilters = Depends()):
    """Весь список по фильтрам (для выпадающих списков и рассылок); для экранов реестра — `/api/v1/registry/persons`."""
    today = config.today()
    rows = _filtered(_registry_rows(f), f.facet_filters())
    return [store.present(p, today) for p, _ in sorted(rows, key=lambda r: REGISTRY_SORTS["name"](r[0]))]


@app.get("/api/v1/registry/persons")
async def registry_persons(f: PersonFilters = Depends(), sort: str = "name",
                           order: str = Query("asc", pattern="^(asc|desc)$"),
                           offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=MAX_LIMIT)):
    """Страница реестра: `{total, offset, limit, items, facets}`.

    Фасет считается по всем остальным фильтрам (кроме своего), чтобы в списке было видно, сколько найдётся при смене значения.
    """
    if sort not in REGISTRY_SORTS:
        raise HTTPException(422, f"Сортировка: {', '.join(REGISTRY_SORTS)}")
    filters = f.facet_filters()
    base = _registry_rows(f)
    rows = _filtered(base, filters)
    rows.sort(key=lambda r: REGISTRY_SORTS[sort](r[0]), reverse=order == "desc")
    today = config.today()
    result = page(rows, offset, limit)
    result["items"] = [store.present(p, today) for p, _ in result["items"]]
    result["facets"] = {k: facets(_filtered(base, filters, skip=k), {k: lambda r, k=k: r[1][k]})[k]
                        for k in REGISTRY_FACETS}
    return result


@app.get("/api/v1/organizations")
async def list_organizations(q: str | None = None, discipline: str | None = None, municipality: str | None = None):
    """Организации реестра: карточка из справочника и состав по ролям и дисциплинам.

    Фильтры: `q` — слова в названии, типе, муниципалитете; дисциплина, которую ведёт организация; муниципалитет.
    """
    names = [o for o in ORGANIZATIONS if o != NO_ORGANIZATION]
    names += sorted({p.organization for p in store.persons.values()} - set(names) - {NO_ORGANIZATION})
    words = tokens(q)
    out = []
    for name in names:
        info = ORGANIZATION_INFO.get(name, {})
        kind, place = info.get("type", "—"), info.get("municipality", "—")
        if (municipality and place != municipality) or not matches(words, name, kind, place):
            continue
        people = [p for p in store.persons.values() if p.organization == name]
        disciplines = sorted({d for p in people for d in p.disciplines}, key=list(DISCIPLINE_BY_CODE).index)
        if discipline and discipline not in disciplines:
            continue
        athletes = [p for p in people if Role.ATHLETE in p.roles]
        out.append({
            "name": name, "type": kind, "municipality": place,
            "athletes": len(athletes), "trainers": sum(Role.TRAINER in p.roles for p in people),
            "judges": sum(Role.JUDGE in p.roles for p in people),
            "with_rank": sum(p.rank is not None for p in athletes),
            "disciplines": disciplines, "discipline_labels": [DISCIPLINE_BY_CODE[d]["short"] for d in disciplines],
            "stages": {code: sum(p.training_stage == code for p in athletes) for code in TRAINING_STAGES},
        })
    return out


@app.get("/api/v1/persons/{fsp_id}")
async def get_person(fsp_id: str):
    return store.present(_person(fsp_id))


@app.post("/api/v1/persons", status_code=201)
async def register_person(data: RegistrationIn):
    today = config.today()
    if store.by_email(data.email):
        raise HTTPException(409, "Пользователь с такой электронной почтой уже зарегистрирован")
    age = age_on(data.birth_date, today)
    if data.birth_date >= today or age < 6:
        raise HTTPException(422, "Проверьте дату рождения")
    guardian = None
    if age < 18:
        if not data.guardian_name.strip() or not data.guardian_consent:
            raise HTTPException(422, "Для несовершеннолетнего требуется ФИО и согласие законного представителя")
        guardian = Guardian(full_name=data.guardian_name.strip(), consent_at=datetime.now())
    fsp_id, seq = store.issue_fsp_id(today.year)
    now = datetime.now()
    person = PersonRecord(
        fsp_id=fsp_id, seq=seq, last_name=data.last_name.strip(), first_name=data.first_name.strip(),
        middle_name=data.middle_name.strip(), gender=data.gender, birth_date=data.birth_date,
        municipality=data.municipality, organization=data.organization, roles=data.roles,
        disciplines=data.disciplines, start_year=today.year, email=data.email,
        password_hash=hash_password(data.password), guardian=guardian, pd_consent_at=now, created_at=now,
    )
    store.persons[fsp_id] = person
    store.log(fsp_id, "Регистрация", f"Выдан {fsp_id}, роли: {', '.join(data.roles)}")
    return store.present(person, today)


@app.post("/api/v1/persons/{fsp_id}/antidoping")
async def set_antidoping(fsp_id: str, data: AntidopingIn):
    p = _person(fsp_id)
    if data.course_date > config.today():
        raise HTTPException(422, "Дата прохождения курса не может быть в будущем")
    p.antidoping = Antidoping(course_date=data.course_date, certificate_url=data.certificate_url)
    store.log(fsp_id, "Антидопинг", f"курс РУСАДА {data.course_date:%d.%m.%Y}")
    return store.present(p)


@app.put("/api/v1/persons/{fsp_id}/disciplines")
async def set_disciplines(fsp_id: str, data: DisciplinesIn):
    """Спортсмен сам выбирает свои дисциплины (федерация — любому). Снятая командная дисциплина выводит из её команды."""
    p = _person(fsp_id)
    if data.actor != fsp_id and Role.ADMIN not in _person(data.actor).roles:
        raise HTTPException(403, "Дисциплины меняет сам участник или федерация")
    if not data.disciplines and ({Role.ATHLETE, Role.TRAINER} & set(p.roles)):
        raise HTTPException(422, "Спортсмену и тренеру необходимо указать хотя бы одну дисциплину")
    removed = set(p.disciplines) - set(data.disciplines)
    if (data.disciplines, removed) != (p.disciplines, set()):
        p.disciplines = data.disciplines
        squads.on_disciplines_changed(fsp_id, removed)
        store.log(data.actor, "Дисциплины", f"{fsp_id}: {', '.join(data.disciplines) or '—'}")
    return store.present(p)


@app.post("/api/v1/persons/{fsp_id}/sanctions", status_code=201)
async def add_sanction(fsp_id: str, data: SanctionIn):
    p = _person(fsp_id)
    p.sanctions.append(Sanction(type=data.type, date_from=data.date_from, date_to=data.date_to, reason=data.reason.strip()))
    store.log(data.actor, "Наложена санкция", f"{fsp_id}: {data.type} {data.date_from:%d.%m.%Y}–{data.date_to:%d.%m.%Y}")
    return store.present(p)


@app.post("/api/v1/persons/lookup")
async def lookup_persons(data: LookupIn):
    return {fid: {"full_name": f"{p.last_name} {p.first_name} {p.middle_name}".strip(), "municipality": p.municipality,
                  "birth_date": p.birth_date.isoformat(), "roles": p.roles}
            for fid in data.fsp_ids if (p := store.persons.get(fid))}


@app.post("/api/v1/auth/verify")
async def verify_credentials(data: AuthIn):
    p = store.by_email(data.email.strip())
    stored = p.password_hash if p and p.password_hash else _NO_ACCOUNT_HASH
    ok = verify_password(data.password, stored)  # KDF считается и для несуществующей почты — по времени ответа учётные записи не перебрать
    if not p or not p.password_hash or not ok:
        raise HTTPException(401, "Неверная электронная почта или пароль")
    return store.present(p)


@app.get("/api/v1/stats")
async def stats():
    persons = list(store.persons.values())
    athletes = [p for p in persons if Role.ATHLETE in p.roles]
    by_disc: dict[str, int] = {}
    by_mun: dict[str, int] = {}
    by_year: dict[int, int] = {}
    for p in athletes:
        for d in p.disciplines:
            by_disc[d] = by_disc.get(d, 0) + 1
        by_mun[p.municipality] = by_mun.get(p.municipality, 0) + 1
        by_year[p.start_year] = by_year.get(p.start_year, 0) + 1
    return {
        "athletes": len(athletes),
        "trainers": sum(Role.TRAINER in p.roles for p in persons),
        "judges": sum(Role.JUDGE in p.roles for p in persons),
        "with_rank": sum(p.rank is not None for p in athletes),
        "by_discipline": by_disc, "by_municipality": dict(sorted(by_mun.items(), key=lambda x: -x[1])),
        "by_start_year": dict(sorted(by_year.items())),
    }


@app.get("/api/v1/reports/headcount")
async def headcount_report(years: int = Query(3, ge=2, le=6), discipline: str | None = None,
                           municipality: str | None = None, organization: str | None = None):
    """Численность занимающихся на конец каждого из последних `years` лет, разрезы и динамика."""
    if discipline and discipline not in DISCIPLINE_BY_CODE:
        raise HTTPException(422, "Неизвестная дисциплина")
    if municipality and municipality not in MUNICIPALITIES:
        raise HTTPException(422, "Неизвестный муниципалитет")
    if organization and organization not in ORGANIZATIONS:
        raise HTTPException(422, "Неизвестная организация")
    return build_report(list(store.persons.values()), config.today(), years, discipline or None, municipality or None,
                        organization or None, training=groups.report_data())


@app.get("/api/v1/audit")
async def audit(limit: int = Query(30, le=AUDIT_LIMIT), q: str | None = None, actor: str | None = None,
                date_from: date | None = None, date_to: date | None = None):
    """Журнал (новые сверху). Фильтры: слова в действии/объекте, автор, даты включительно."""
    return search_audit(store.audit, q, actor, date_from, date_to)[:limit]


@app.get("/api/v1/audit/search")
async def audit_search(q: str | None = None, actor: str | None = None, date_from: date | None = None,
                       date_to: date | None = None, order: str = Query("desc", pattern="^(asc|desc)$"),
                       offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=AUDIT_LIMIT)):
    return audit_page(store.audit, q, actor, date_from, date_to, order, offset, limit)


# ---------------------------------------------------------------- notifications (лента в кабинете)
@app.post("/api/v1/notifications", status_code=201)
async def create_notifications(data: NotificationIn):
    """Рассылка в ленту; возвращает тех, кому уведомление реально добавлено (с `key` — без повторов)."""
    return {"delivered": store.notify(data.fsp_ids, data.title, data.text, data.link, data.key)}


@app.get("/api/v1/notifications")
async def list_notifications(fsp_id: str, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                             unread_only: bool = False, q: str | None = None):
    mine = [n for n in store.notifications if n.fsp_id == fsp_id]
    words = tokens(q)
    shown = [n for n in mine if (not unread_only or not n.read) and matches(words, n.title, n.text)]
    shown.sort(key=lambda n: (n.at, n.id), reverse=True)
    result = page(shown, offset, limit, unread=sum(not n.read for n in mine), all=len(mine))
    result["items"] = [n.model_dump(mode="json") for n in result["items"]]
    return result


@app.post("/api/v1/notifications/read")
async def read_notifications(fsp_id: str, data: NotificationsReadIn | None = None):
    """Отметить прочитанными показанные уведомления (`ids`), без тела — все уведомления человека."""
    ids = set(data.ids) if data and data.ids is not None else None
    for n in store.notifications:
        if n.fsp_id == fsp_id and (ids is None or n.id in ids):
            n.read = True
    return {"unread": sum(not n.read for n in store.notifications if n.fsp_id == fsp_id)}


# ---------------------------------------------------------------- sanctions & alerts
@app.get("/api/v1/sanctions/active")
async def active_sanctions(on: date | None = None):
    on = _on(on)
    return [{"fsp_id": p.fsp_id, "full_name": f"{p.last_name} {p.first_name} {p.middle_name}".strip(),
             "type": s.type, "date_to": s.date_to.isoformat(), "reason": s.reason}
            for p in store.persons.values() for s in p.sanctions if s.date_from <= on <= s.date_to]


@app.get("/api/v1/alerts/ranks")
async def rank_alerts(within_days: int = Query(90, ge=0, le=730), trainer_fsp_id: str | None = None):
    today = config.today()
    scope = None
    if trainer_fsp_id:
        scope = {link.athlete_fsp_id for link in store.links
                 if link.trainer_fsp_id == trainer_fsp_id and link.status == LinkStatus.ACTIVE}
    out = []
    for p in store.persons.values():
        if scope is not None and p.fsp_id not in scope:
            continue
        if p.rank and p.rank.expires_at and (p.rank.expires_at - today).days <= within_days:
            out.append(store.present(p, today))
    return sorted(out, key=lambda x: x["rank"]["days_left"])


@app.get("/api/v1/alerts/judge-categories")
async def judge_alerts(within_days: int = Query(90, ge=0, le=730)):
    today = config.today()
    out = [store.present(p, today) for p in store.persons.values()
           if p.judge_category and (p.judge_category.expires_at - today).days <= within_days]
    return sorted(out, key=lambda x: x["judge_category"]["days_left"])


# ---------------------------------------------------------------- trainer ↔ athlete links
@app.get("/api/v1/trainers/{fsp_id}/athletes")
async def trainer_athletes(fsp_id: str, with_performance: bool = False):
    trainer = _person(fsp_id)
    if Role.TRAINER not in trainer.roles:
        raise HTTPException(403, "У участника нет роли «Тренер»")
    today = config.today()
    links = [store.present_link(link, today) for link in store.links if link.trainer_fsp_id == fsp_id]
    if with_performance and links:
        perf = await _compile_performance({link["athlete_fsp_id"] for link in links})
        for link in links:
            link["performance"] = perf.get(link["athlete_fsp_id"])
    order = {LinkStatus.PENDING: 0, LinkStatus.ACTIVE: 1, LinkStatus.SUSPENDED: 2, LinkStatus.ENDED: 3}
    return sorted(links, key=lambda x: (order[x["status"]], x["athlete"]["full_name"]))


@app.get("/api/v1/athletes/{fsp_id}/links")
async def athlete_links(fsp_id: str):
    _person(fsp_id)
    today = config.today()
    return [store.present_link(link, today) for link in store.links if link.athlete_fsp_id == fsp_id]


@app.post("/api/v1/links", status_code=201)
async def create_link(data: LinkCreateIn):
    trainer, athlete = _person(data.trainer_fsp_id), _person(data.athlete_fsp_id)
    if Role.TRAINER not in trainer.roles:
        raise HTTPException(403, "Запрос на привязку может отправить только тренер")
    if Role.ATHLETE not in athlete.roles:
        raise HTTPException(422, "Указанный участник не является спортсменом")
    if trainer.fsp_id == athlete.fsp_id:
        raise HTTPException(422, "Нельзя привязать самого себя")
    if data.discipline not in athlete.disciplines:
        raise HTTPException(422, "Спортсмен не занимается указанной дисциплиной")
    if any(link.trainer_fsp_id == trainer.fsp_id and link.athlete_fsp_id == athlete.fsp_id
           and link.discipline == data.discipline and link.status != LinkStatus.ENDED for link in store.links):
        raise HTTPException(409, "Связь по этой дисциплине уже существует")
    link = store.add_link(trainer.fsp_id, athlete.fsp_id, data.discipline, LinkStatus.PENDING, by=trainer.fsp_id, note="")
    store.log(trainer.fsp_id, "Запрос на привязку", f"{athlete.fsp_id} · {data.discipline}")
    return store.present_link(link)


@app.post("/api/v1/links/{link_id}/status")
async def change_link_status(link_id: int, data: LinkStatusIn):
    link = store.get_link(link_id)
    if not link:
        raise HTTPException(404, "Связь не найдена")
    side = "athlete" if data.actor_fsp_id == link.athlete_fsp_id else "trainer" if data.actor_fsp_id == link.trainer_fsp_id else None
    if not side:
        raise HTTPException(403, "Изменять связь могут только её участники")
    allowed = _TRANSITIONS.get((link.status, data.status))
    if not allowed:
        raise HTTPException(422, f"Переход {link.status} → {data.status} недопустим")
    if side not in allowed:
        raise HTTPException(403, "Подтвердить привязку может только спортсмен")
    link.status = data.status
    link.history.append(LinkEvent(status=data.status, at=datetime.now(), by=data.actor_fsp_id, note=data.note))
    store.log(data.actor_fsp_id, "Статус связи", f"#{link.id} → {data.status}")
    return store.present_link(link)


# ---------------------------------------------------------------- rank / judge category requests
def _request(req_id: int) -> QualificationRequest:
    r = next((x for x in store.requests if x.id == req_id), None)
    if not r:
        raise HTTPException(404, "Заявка не найдена")
    return r


def _plus_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 февраля
        return d.replace(year=d.year + years, day=28)


async def _competition_call(path: str, **params):
    try:
        return await clients["competition"].get(path, **params)
    except ServiceError as exc:
        raise HTTPException(503, f"Сервис соревнований недоступен: {exc.detail}") from exc


async def _rank_basis(person: PersonRecord, submitted_by: str, competition_id: int | None) -> dict:
    if Role.ATHLETE not in person.roles:
        raise HTTPException(422, "Разряд присваивается только спортсмену")
    active = [lk for lk in store.links if lk.athlete_fsp_id == person.fsp_id and lk.status == LinkStatus.ACTIVE]
    if submitted_by == person.fsp_id:
        if active:
            trainer = store.persons[active[0].trainer_fsp_id]
            raise HTTPException(403, f"Заявку на разряд подаёт ваш тренер — {trainer.last_name} {trainer.first_name}")
    elif not any(lk.trainer_fsp_id == submitted_by for lk in active):
        raise HTTPException(403, "Подать заявку может тренер с активной привязкой или сам спортсмен без тренера")
    if competition_id is None:
        raise HTTPException(422, "Выберите соревнование и результат — основание для разряда")
    rows = await _competition_call("/api/v1/results", fsp_ids=person.fsp_id)
    row = next((r for r in rows if r["competition_id"] == competition_id), None)
    if not row:
        raise HTTPException(422, "Результат на этом соревновании не найден в опубликованных протоколах")
    if row["status"] != "OK" or not row["place"]:
        raise HTTPException(422, "Дисквалификация не может быть основанием для разряда")
    return {
        "competition_id": competition_id, "competition_code": row["competition_code"], "title": row["title"],
        "date": row["date"], "discipline_label": row["discipline_label"], "level_label": row["level_label"],
        "place": row["place"], "protocol_size": row["protocol_size"],
        "text": f"{row['title']} ({date.fromisoformat(row['date']):%d.%m.%Y}) — {row['place']} место из {row['protocol_size']}",
    }


async def _judge_basis(person: PersonRecord, submitted_by: str, note: str) -> dict:
    if Role.JUDGE not in person.roles:
        raise HTTPException(422, "Судейская категория присваивается только судье")
    if submitted_by != person.fsp_id:
        raise HTTPException(403, "Заявку на судейскую категорию подаёт сам судья")
    past = (await _competition_call(f"/api/v1/judges/{person.fsp_id}/assignments"))["past"]
    practice = "; ".join(f"{c['title']} — {c['position'].lower()}" for c in past[:3])
    return {"refereed": len(past), "text": note.strip() or practice or "Судейская практика в системе не найдена"}


@app.get("/api/v1/qualification-requests")
async def list_requests(person_fsp_id: str | None = None, submitted_by: str | None = None,
                        status: str | None = None, kind: str | None = None):
    out = [store.present_request(r) for r in store.requests
           if (person_fsp_id is None or r.person_fsp_id == person_fsp_id)
           and (submitted_by is None or r.submitted_by == submitted_by)
           and (status is None or r.status == status) and (kind is None or r.kind == kind)]
    return sorted(out, key=lambda x: x["created_at"], reverse=True)


@app.post("/api/v1/qualification-requests", status_code=201)
async def submit_request(data: QualificationIn):
    person = _person(data.person_fsp_id)
    _person(data.submitted_by)
    catalog = RANK_BY_CODE if data.kind == "RANK" else JUDGE_CATEGORY_BY_CODE
    if data.code not in catalog:
        raise HTTPException(422, "Неизвестный разряд или категория")
    if any(r.person_fsp_id == person.fsp_id and r.kind == data.kind and r.status == ApplicationStatus.PENDING
           for r in store.requests):
        raise HTTPException(409, "Заявка уже на рассмотрении в федерации")
    if data.kind == "RANK":
        basis = await _rank_basis(person, data.submitted_by, data.competition_id)
        current = person.rank
    else:
        basis = await _judge_basis(person, data.submitted_by, data.note)
        current = person.judge_category
    req = QualificationRequest(
        id=max((r.id for r in store.requests), default=0) + 1, kind=data.kind, person_fsp_id=person.fsp_id,
        code=data.code, purpose="CONFIRM" if current and current.code == data.code else "ASSIGN", basis=basis,
        submitted_by=data.submitted_by, status=ApplicationStatus.PENDING, created_at=datetime.now(),
    )
    store.requests.append(req)
    store.log(data.submitted_by, "Заявка на квалификацию", f"{person.fsp_id} · {data.kind} {data.code}")
    return store.present_request(req)


@app.post("/api/v1/qualification-requests/{req_id}/decision")
async def decide_request(req_id: int, data: QualificationDecisionIn):
    r = _request(req_id)
    if r.status != ApplicationStatus.PENDING:
        raise HTTPException(422, "Решение по заявке уже принято")
    person, today = store.persons[r.person_fsp_id], config.today()
    order_no = data.order_no.strip()
    if data.status == ApplicationStatus.APPROVED:
        if r.kind == "RANK":
            years = RANK_BY_CODE[r.code]["validity_years"]
            expires = data.valid_until or (_plus_years(today, years) if years else None)
            person.rank = RankRecord(code=r.code, assigned_at=today, expires_at=expires, order_no=order_no,
                                     basis=r.basis.get("text", ""))
        else:
            if not data.valid_until or data.valid_until <= today:
                raise HTTPException(422, "Укажите срок действия категории (дата в будущем)")
            person.judge_category = JudgeCategoryRecord(code=r.code, expires_at=data.valid_until, order_no=order_no)
    r.status, r.decided_at, r.decided_by = data.status, datetime.now(), data.actor
    r.comment, r.order_no = data.comment.strip(), order_no
    store.log(data.actor, "Решение по квалификации", f"#{r.id} {person.fsp_id} {r.code} → {data.status}")
    return store.present_request(r)


# ---------------------------------------------------------------- performance history (aggregated from competition_service)
async def _compile_performance(fsp_ids: set[str]) -> dict[str, dict]:
    try:
        results = await clients["competition"].get("/api/v1/results", fsp_ids=",".join(sorted(fsp_ids)))
    except ServiceError as exc:
        return {fid: {"available": False, "error": str(exc.detail)} for fid in fsp_ids}
    compiled = {fid: {"available": True, "starts": 0, "podiums": 0, "golds": 0, "best_place": None,
                      "season_points": 0, "results": []} for fid in fsp_ids}
    for r in results:
        c = compiled.get(r["fsp_id"])
        if c is None:
            continue
        c["results"].append(r)
        c["starts"] += 1
        if r["status"] == "OK" and r["place"]:
            c["podiums"] += r["place"] <= 3
            c["golds"] += r["place"] == 1
            c["best_place"] = min(c["best_place"] or r["place"], r["place"])
            c["season_points"] += r.get("base_points", 0)
    for c in compiled.values():
        c["results"].sort(key=lambda x: x["date"], reverse=True)
    return compiled


@app.get("/api/v1/persons/{fsp_id}/performance")
async def person_performance(fsp_id: str):
    if not is_valid_fsp_id(fsp_id):
        raise HTTPException(422, "Некорректный FSP ID")
    _person(fsp_id)
    return (await _compile_performance({fsp_id}))[fsp_id]

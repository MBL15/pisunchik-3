"""competition_service (port 8002): calendar, applications, live protocols from logs, rating engine, documents."""
import asyncio
import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from datetime import date, datetime

from fastapi import FastAPI, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from competition_service import appeals, awards, development, evsk, integrations
from competition_service.deps import clients
from competition_service.log_parser import parse_log
from competition_service.models import (
    ActorIn, Application, ApplicationIn, BatchDecisionIn, Competition, CompetitionCreateIn, CompetitionTypeIn, DecisionIn,
    DocumentIn,
    IngestIn, JudgeAssignIn, JudgeAssignment, Protocol, RegistrationIn, ResultsIn, SettingsIn, Team, TeamApproveIn,
    TeamCreateIn, TeamMember, TeamMemberIn, TeamRoleIn, TeamUpdateIn,
)
from competition_service.rating import RatingSettings, compute_rating
from competition_service.seed import seed
from competition_service.store import DDL, restore, snapshot, store
from shared import config
from shared import service_auth
from shared.db import Persistence
from shared.domain import (
    AGE_GROUPS, DEFAULT_COMPETITION_TYPES, DISCIPLINE_BY_CODE, EVSK_REQUIREMENTS, JUDGE_POSITIONS, LEVEL_LABELS,
    RANK_BY_CODE, RANK_ORDER, REGION_CODE, SANCTION_LABELS, TEAM_ROLE_LABELS, ApplicationStatus, CompetitionStatus,
    LinkStatus, age_group_fit, protocol_mode,
)
from shared.http import ServiceClient, ServiceError
from shared.listing import AUDIT_LIMIT, MAX_LIMIT, audit_page, facets, matches, page, search_audit, tokens

db = Persistence(DDL)
LIVE_TICK_MIN_INTERVAL = 1.5  # seconds; many open dashboards must not fast-forward the stream


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed(store)  # календарь и живые протоколы пересоздаются всегда; сохранённое состояние накладывается сверху
    if db.enabled:
        db.init()
        rows = db.load()
        if rows["comp_competitions"]:
            restore(store, rows)
            db.mark_loaded(snapshot(store))
        else:
            db.save(snapshot(store))
    clients["sportsman"] = ServiceClient("sportsman_service", config.SPORTSMAN_URL, caller="competition_service")
    driver = asyncio.create_task(_live_driver())
    yield
    driver.cancel()
    await clients["sportsman"].aclose()


app = FastAPI(title="ФСП Оренбург · Competition Service", version="1.0.0", lifespan=lifespan)
service_auth.install(app)
app.include_router(development.router)
app.include_router(awards.router)
app.include_router(appeals.router)
app.include_router(integrations.router)


@app.middleware("http")
async def persist_writes(request: Request, call_next):
    response = await call_next(request)
    if db.enabled and request.method != "GET":
        await run_in_threadpool(lambda: db.save(snapshot(store)))
    return response


def _competition(cid: int) -> Competition:
    c = store.competitions.get(cid)
    if not c:
        raise HTTPException(404, "Соревнование не найдено")
    return c


def _present_protocol(cid: int) -> dict:
    proto = store.protocols.get(cid)
    if not proto:
        raise HTTPException(404, "Протокол ещё не сформирован")
    tasks = sorted({t for e in proto.entries for t in e["tasks"]})
    return {
        "competition": store.present_competition(store.competitions[cid]),
        "mode": proto.mode, "entries": proto.entries, "warnings": proto.warnings, "events": proto.events,
        "last_time": proto.last_time, "published": proto.published,
        "published_at": proto.published_at.isoformat(timespec="seconds") if proto.published_at else None,
        "updated_at": proto.updated_at.isoformat(timespec="seconds"), "source": proto.source,
        "corrections": proto.corrections, "raw_tail": proto.raw_log[-14:], "live_remaining": len(store.live_queue.get(cid, [])), "tasks": tasks,
    }


@app.get("/health")
async def health():
    return {"service": "competition_service", "status": "ok", "competitions": len(store.competitions),
            "protocols": len(store.protocols)}


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


# ---------------------------------------------------------------- calendar
@app.get("/api/v1/competitions")
async def list_competitions(discipline: str | None = None, status: str | None = None, plan: str | None = None,
                            format: str | None = None, season: int | None = None, upcoming: bool = False,
                            q: str | None = None, age_group: str | None = None, level: str | None = None):
    """Календарь. `q` — слова в названии, коде, месте, типе или номере плана."""
    today, words = config.today(), tokens(q)
    out = []
    for c in store.competitions.values():
        if (discipline and c.discipline != discipline) or (status and c.status != status) \
                or (plan and c.calendar_plan != plan) or (format and c.format != format) \
                or (season and c.end_date.year != season) or (age_group and c.age_group != age_group) \
                or (level and c.level != level):
            continue
        if words and not matches(words, c.title, c.code, c.venue, c.type, c.plan_number):
            continue
        if upcoming and c.end_date < today and c.status != CompetitionStatus.LIVE:
            continue
        out.append(store.present_competition(c, today))
    return sorted(out, key=lambda x: x["start_date"], reverse=not upcoming)


@app.get("/api/v1/competitions/{cid}")
async def get_competition(cid: int):
    return store.present_competition(_competition(cid))


@app.post("/api/v1/competitions", status_code=201)
async def create_competition(data: CompetitionCreateIn):
    today = config.today()
    if data.type not in store.competition_types:
        raise HTTPException(422, "Неизвестный тип соревнования — добавьте его в справочник типов")
    cid = max(store.competitions, default=0) + 1
    status = CompetitionStatus.REGISTRATION_OPEN if data.registration_deadline >= today else CompetitionStatus.PLANNED
    c = Competition(id=cid, code=f"ОРБ-{data.discipline}-{data.start_date.year}-{cid:02d}", status=status,
                    **data.model_dump(exclude={"actor"}))
    store.competitions[cid] = c
    store.log(data.actor, "Создано соревнование", f"{c.code} · {c.title}")
    integrations.emit("competition.published", integrations.competition_payload(store.present_competition(c, today)))
    return store.present_competition(c, today)


@app.post("/api/v1/competitions/{cid}/registration")
async def toggle_registration(cid: int, data: RegistrationIn):
    """Ручное открытие / закрытие приёма заявок. Открытие после истёкшего срока требует новой даты окончания."""
    c = _competition(cid)
    today = config.today()
    if c.status in (CompetitionStatus.LIVE, CompetitionStatus.FINISHED):
        raise HTTPException(422, "Соревнование уже идёт или завершено — приём заявок не меняется")
    if data.action == "close":
        if c.status != CompetitionStatus.REGISTRATION_OPEN:
            raise HTTPException(409, "Приём заявок не открыт")
        c.status = CompetitionStatus.REGISTRATION_CLOSED
        store.log(data.actor, "Закрыт приём заявок", f"{c.code} · заявок: {sum(a.competition_id == c.id for a in store.applications)}")
        return store.present_competition(c, today)

    already_open = c.status == CompetitionStatus.REGISTRATION_OPEN
    if already_open and not data.deadline:
        raise HTTPException(409, "Приём заявок уже открыт — чтобы продлить, укажите новую дату окончания")
    if c.start_date <= today:
        raise HTTPException(422, "Соревнование уже началось — открыть приём заявок нельзя")
    deadline = data.deadline or c.registration_deadline
    if not deadline or deadline < today:
        raise HTTPException(422, "Срок подачи заявок истёк или не задан — укажите новую дату окончания приёма")
    if deadline > c.start_date:
        raise HTTPException(422, "Приём заявок должен закрываться до начала соревнования")
    c.registration_deadline, c.status = deadline, CompetitionStatus.REGISTRATION_OPEN
    store.log(data.actor, "Продлён приём заявок" if already_open else "Открыт приём заявок", f"{c.code} · до {deadline:%d.%m.%Y}")
    return store.present_competition(c, today)


# ---------------------------------------------------------------- competition types directory
@app.get("/api/v1/competition-types")
async def list_competition_types():
    used: dict[str, int] = {}
    for c in store.competitions.values():
        used[c.type] = used.get(c.type, 0) + 1
    return [{"title": t, "used": used.get(t, 0), "default": t in DEFAULT_COMPETITION_TYPES} for t in store.competition_types]


@app.post("/api/v1/competition-types", status_code=201)
async def add_competition_type(data: CompetitionTypeIn):
    if data.title.casefold() in {t.casefold() for t in store.competition_types}:
        raise HTTPException(409, f"Тип «{data.title}» уже есть в справочнике")
    store.competition_types.append(data.title)
    store.log(data.actor, "Добавлен тип соревнования", data.title)
    return await list_competition_types()


@app.delete("/api/v1/competition-types")
async def remove_competition_type(title: str, actor: str):
    if title not in store.competition_types:
        raise HTTPException(404, "Тип не найден в справочнике")
    if any(c.type == title for c in store.competitions.values()):
        raise HTTPException(409, f"Тип «{title}» используется в календаре — удалить его нельзя")
    store.competition_types.remove(title)
    store.log(actor, "Удалён тип соревнования", title)
    return await list_competition_types()


# ---------------------------------------------------------------- protocols (logs → ranking)
@app.get("/api/v1/competitions/{cid}/protocol")
async def get_protocol(cid: int):
    _competition(cid)
    return _present_protocol(cid)


@app.post("/api/v1/competitions/{cid}/protocol/ingest")
async def ingest_log(cid: int, data: IngestIn):
    c = _competition(cid)
    parsed = parse_log(data.log)
    if not parsed.entries:
        raise HTTPException(422, {"message": "В логе не найдено ни одного участника", "warnings": parsed.warnings[:10]})
    proto = store.protocols.get(cid)
    names = dict(proto.names) if proto else {}
    unknown = [e["fsp_id"] for e in parsed.entries if e["fsp_id"] not in names]
    if unknown:
        try:
            found = await clients["sportsman"].post("/api/v1/persons/lookup", json={"fsp_ids": unknown})
            names.update({fid: {"full_name": v["full_name"], "region": v["municipality"]} for fid, v in found.items()})
        except ServiceError:
            pass  # protocol still builds; unresolved names are flagged in the table
    published = c.status == CompetitionStatus.FINISHED
    new = Protocol(competition_id=cid, mode=parsed.mode, raw_log=data.log.strip().splitlines(), names=names,
                   updated_at=datetime.now(), published=published,
                   published_at=datetime.now() if published else None, source="upload")
    store.protocols[cid] = new
    store.live_queue.pop(cid, None)
    store.live_replay.pop(cid, None)
    store.reparse(new)
    store.log(data.actor, "Загружен лог протокола", f"{c.code}: {new.events} событий, {len(new.entries)} участников")
    return _present_protocol(cid)


def _advance_live(cid: int) -> None:
    """Один шаг демонстрационного live-потока: до двух событий из очереди, не чаще `LIVE_TICK_MIN_INTERVAL`."""
    queue = store.live_queue.get(cid)
    now = time.monotonic()
    proto = store.protocols.get(cid)
    if not queue and cid in store.live_replay and proto and not proto.published \
            and now - store.last_tick.get(cid, 0) >= LIVE_TICK_MIN_INTERVAL * 4:
        # Demo loop: the seeded live round replays from its initial snapshot so the stream never runs dry.
        initial, future = store.live_replay[cid]
        proto.raw_log, store.live_queue[cid] = list(initial), list(future)
        store.last_tick[cid] = now
        store.reparse(proto)
    elif queue and now - store.last_tick.get(cid, 0) >= LIVE_TICK_MIN_INTERVAL:
        store.last_tick[cid] = now
        batch, store.live_queue[cid] = queue[:2], queue[2:]
        proto.raw_log.extend(batch)
        store.reparse(proto)


async def _live_driver() -> None:
    """Поток движется по часам сервиса, а не по запросам посетителей: чтение протокола остаётся безопасным GET."""
    while True:
        await asyncio.sleep(LIVE_TICK_MIN_INTERVAL)
        for cid in list(store.live_replay) + [c for c in store.live_queue if c not in store.live_replay]:
            _advance_live(cid)


@app.post("/api/v1/competitions/{cid}/live/tick")
async def live_tick(cid: int):
    """Принудительный шаг потока (отладка и тесты); в работе поток двигает `_live_driver`."""
    _competition(cid)
    _advance_live(cid)
    return _present_protocol(cid)


def _publish(c: Competition, proto: Protocol, actor: str) -> None:
    proto.raw_log.extend(store.live_queue.pop(c.id, []))
    proto.published, proto.published_at = True, datetime.now()
    c.status = CompetitionStatus.FINISHED
    store.reparse(proto)
    store.log(actor, "Протокол опубликован", f"{c.code}: {len(proto.entries)} участников → рейтинг и история")
    integrations.emit("protocol.published", {"competition_id": c.id, "code": c.code, "title": c.title, "participants": len(proto.entries),
                                             "protocol_url": f"/api/ext/v1/competitions/{c.id}/protocol"})


@app.post("/api/v1/competitions/{cid}/protocol/publish")
async def publish_protocol(cid: int, data: ActorIn):
    c = _competition(cid)
    proto = store.protocols.get(cid)
    if not proto:
        raise HTTPException(404, "Протокол ещё не сформирован")
    if proto.published:
        raise HTTPException(409, "Протокол уже опубликован")
    _publish(c, proto, data.actor)
    return _present_protocol(cid)


# ---------------------------------------------------------------- protocols (approved applications → manual results)
@app.get("/api/v1/competitions/{cid}/protocol/entry-sheet")
async def entry_sheet(cid: int):
    """Who goes into the protocol (approved applications) and what has already been entered."""
    c = _competition(cid)
    proto = store.protocols.get(cid)
    entered = {r["fsp_id"]: r for r in proto.results} if proto and proto.source == "manual" else {}
    participants = [{"fsp_id": a.athlete_fsp_id, "full_name": a.athlete_name, "application_id": a.id,
                     "result": entered.get(a.athlete_fsp_id)}
                    for a in sorted(store.approved(cid).values(), key=lambda a: a.athlete_name)]
    return {
        "competition": store.present_competition(c), "mode": protocol_mode(c.discipline),
        "participants": participants,
        "pending": [store.present_application(a) for a in store.applications
                    if a.competition_id == cid and a.status == ApplicationStatus.PENDING],
        "protocol": _present_protocol(cid) if proto else None,
    }


@app.post("/api/v1/competitions/{cid}/protocol/results")
async def enter_results(cid: int, data: ResultsIn):
    c = _competition(cid)
    proto = store.protocols.get(cid)
    if proto and proto.published:
        raise HTTPException(409, "Протокол уже опубликован — результаты изменить нельзя")
    if cid in store.live_replay or store.live_queue.get(cid):
        raise HTTPException(409, "Протокол этого соревнования ведётся в live-режиме из лога")
    if c.start_date > config.today():
        raise HTTPException(422, "Результаты вносятся после начала соревнования")
    approved = store.approved(cid)
    if not approved:
        raise HTTPException(422, "Нет одобренных заявок — протокол формируется из участников с одобренными заявками")
    ids = [r.fsp_id for r in data.results]
    if len(set(ids)) != len(ids):
        raise HTTPException(422, "Результат участника указан дважды")
    if strangers := [fid for fid in ids if fid not in approved]:
        raise HTTPException(422, f"Нет одобренной заявки на соревнование: {', '.join(strangers)}")

    names = {fid: {"full_name": approved[fid].athlete_name, "region": "—"} for fid in ids}
    try:
        found = await clients["sportsman"].post("/api/v1/persons/lookup", json={"fsp_ids": ids})
        for fid, v in found.items():
            names[fid]["region"] = v["municipality"]
    except ServiceError:
        pass  # names come from the applications; only the territory column stays empty
    new = Protocol(competition_id=cid, mode=protocol_mode(c.discipline), raw_log=[], names=names,
                   results=[r.model_dump() for r in data.results], updated_at=datetime.now(), source="manual")
    store.protocols[cid] = new
    store.reparse(new)
    store.log(data.actor, "Внесены результаты", f"{c.code}: {len(new.entries)} участников из одобренных заявок")
    if data.publish:
        _publish(c, new, data.actor)
    return _present_protocol(cid)


@app.post("/api/v1/integrations/import/{cid}")
async def import_results(cid: int, data: integrations.ImportIn):
    """Импорт результатов из внешней системы: участники — только с одобренными заявками; создаётся черновик, публикует федерация."""
    client = store.ext.table(integrations.CLIENTS).get(data.client_id)
    if not client or not client["active"] or "results:write" not in client["scopes"]:
        raise HTTPException(403, "У токена нет области results:write")
    actor = f"ext:{client['name']}" + (f" ({data.system})" if data.system else "")
    return await enter_results(cid, ResultsIn(results=data.results, actor=actor, publish=False))


@app.get("/api/v1/results")
async def results(fsp_ids: str = Query(..., max_length=20_000), season: int | None = None):
    wanted = set(filter(None, fsp_ids.split(",")))
    return [r for r in store.published_results(season) if r["fsp_id"] in wanted]


# ---------------------------------------------------------------- rating engine
@app.get("/api/v1/rating/settings")
async def get_settings():
    return {"points": store.settings.points, "coefficient": store.settings.coefficient,
            "min_podiums": store.settings.min_podiums}


@app.put("/api/v1/rating/settings")
async def put_settings(data: SettingsIn):
    store.settings = RatingSettings(points={str(k): v for k, v in data.points.items()},
                                    coefficient=data.coefficient, min_podiums=data.min_podiums)
    for proto in store.protocols.values():
        store.reparse(proto)
    store.log(data.actor, "Изменены настройки рейтинга", f"×{data.coefficient} при ≥{data.min_podiums} призовых")
    return await get_settings()


def _seasons() -> list[int]:
    return sorted({store.competitions[cid].end_date.year for cid, p in store.protocols.items() if p.published}, reverse=True)


@app.get("/api/v1/rating")
async def rating(discipline: str = "ALG", season: int | None = None, age_group: str | None = None,
                 min_rank: str | None = None):
    return (await _build_rating(discipline, season, age_group, min_rank))[0]


async def _build_rating(discipline: str, season: int | None, age_group: str | None, min_rank: str | None
                        ) -> tuple[dict, dict[str, dict]]:
    """Рейтинг сезона и реестр спортсменов, по которому он отфильтрован (нужен сборной для снимка состава)."""
    if discipline not in DISCIPLINE_BY_CODE:
        raise HTTPException(422, "Неизвестная дисциплина")
    seasons = _seasons()
    season = season or (seasons[0] if seasons else config.today().year)
    rows = store.published_results(season, discipline)

    persons: dict[str, dict] = {}
    sanctions_checked = True
    try:
        persons = {p["fsp_id"]: p for p in await clients["sportsman"].get("/api/v1/persons", role="athlete")}
    except ServiceError:
        sanctions_checked = False

    excluded = {}
    for fid, p in persons.items():
        for s in p["sanctions"]:
            if s["active"]:
                excluded[fid] = f"{SANCTION_LABELS[s['type']]} до {s['date_to']}: {s['reason']}"
    if persons and (age_group or min_rank):
        lo, hi = AGE_GROUPS.get(age_group, (0, 200))
        allowed = {fid for fid, p in persons.items()
                   if lo <= p["age"] <= hi and (not min_rank or (p["rank"] and RANK_ORDER[p["rank"]["code"]] >= RANK_ORDER.get(min_rank, 0)))}
        rows = [r for r in rows if r["fsp_id"] in allowed]

    out = compute_rating(rows, store.settings, excluded, region=REGION_CODE)
    for row in out["rows"] + out["excluded"]:
        p = persons.get(row["fsp_id"])
        row["rank_short"] = p["rank"]["short"] if p and p["rank"] else "б/р"
        row["municipality"] = p["municipality"] if p else "—"
        row["age"] = p["age"] if p else None
    counted = sorted({(r["competition_id"], r["competition_code"], r["title"], r["level"], r["date"]) for r in rows},
                     key=lambda x: x[4])
    return {
        "discipline": discipline, "discipline_label": DISCIPLINE_BY_CODE[discipline]["short"],
        "discipline_title": DISCIPLINE_BY_CODE[discipline]["title"], "season": season, "seasons": seasons,
        "settings": await get_settings(), "rows": out["rows"], "excluded": out["excluded"],
        "sanctions_checked": sanctions_checked, "filters": {"age_group": age_group, "min_rank": min_rank},
        "competitions": [{"id": c[0], "code": c[1], "title": c[2], "level": c[3], "date": c[4]} for c in counted],
    }, persons


# ---------------------------------------------------------------- сборные команды (ручной состав из рейтинга)
def _team(tid: int) -> Team:
    t = store.teams.get(tid)
    if not t:
        raise HTTPException(404, "Сборная не найдена")
    return t


def _editable(t: Team) -> Team:
    if t.status == "APPROVED":
        raise HTTPException(409, "Состав утверждён приказом — изменить его нельзя")
    return t


async def _team_member(row: dict, person: dict | None, discipline: str, role: str = "MAIN") -> TeamMember:
    coaches: list[str] = []
    try:
        links = await clients["sportsman"].get(f"/api/v1/athletes/{row['fsp_id']}/links")
        coaches = [lk["trainer_name"] for lk in links if lk["status"] == LinkStatus.ACTIVE and lk["discipline"] == discipline]
    except ServiceError:
        pass  # состав всё равно формируется; тренеры в документе останутся пустыми
    return TeamMember(fsp_id=row["fsp_id"], full_name=row["full_name"], role=role, position=row["position"],
                      points=row["final"], rank_short=row["rank_short"], municipality=row["municipality"],
                      organization=person["organization"] if person else "",
                      birth_date=person["birth_date"] if person else None, coaches=coaches)


async def _rating_rows_for(t: Team | TeamCreateIn) -> tuple[dict[str, dict], dict[str, dict]]:
    payload, persons = await _build_rating(t.discipline, t.season, t.age_group, t.min_rank)
    if not payload["sanctions_checked"]:
        raise HTTPException(503, "Реестр спортсменов недоступен — санкции не проверить, состав не изменён")
    return {r["fsp_id"]: r for r in payload["rows"]}, persons


@app.get("/api/v1/teams")
async def list_teams(discipline: str | None = None, season: int | None = None):
    return [store.present_team(t) for t in sorted(store.teams.values(), key=lambda t: t.updated_at, reverse=True)
            if (not discipline or t.discipline == discipline) and (not season or t.season == season)]


@app.get("/api/v1/teams/{tid}")
async def get_team(tid: int):
    return store.present_team(_team(tid))


@app.post("/api/v1/teams", status_code=201)
async def create_team(data: TeamCreateIn):
    rows, persons = await _rating_rows_for(data)
    if missing := [fid for fid in data.fsp_ids if fid not in rows]:
        raise HTTPException(422, f"Нет в рейтинге по выбранным фильтрам (или исключены по санкциям): {', '.join(missing)}")
    members = await asyncio.gather(*(_team_member(rows[fid], persons.get(fid), data.discipline) for fid in data.fsp_ids))
    now = datetime.now()
    t = Team(id=max(store.teams, default=0) + 1, title=data.title.strip(), discipline=data.discipline, season=data.season,
             purpose=data.purpose.strip(), age_group=data.age_group, min_rank=data.min_rank,
             members=sorted(members, key=lambda m: m.position), created_by=data.actor, created_at=now, updated_at=now)
    store.teams[t.id] = t
    store.log(data.actor, "Сформирована сборная", f"#{t.id} {t.title}: {len(t.members)} спортсменов")
    return store.present_team(t)


@app.put("/api/v1/teams/{tid}")
async def update_team(tid: int, data: TeamUpdateIn):
    t = _editable(_team(tid))
    t.title, t.purpose, t.updated_at = data.title.strip(), data.purpose.strip(), datetime.now()
    store.log(data.actor, "Изменена сборная", f"#{t.id} {t.title}")
    return store.present_team(t)


@app.post("/api/v1/teams/{tid}/members", status_code=201)
async def add_team_member(tid: int, data: TeamMemberIn):
    t = _editable(_team(tid))
    if any(m.fsp_id == data.fsp_id for m in t.members):
        raise HTTPException(409, "Спортсмен уже в составе")
    rows, persons = await _rating_rows_for(t)
    if data.fsp_id not in rows:
        raise HTTPException(422, "Спортсмена нет в рейтинге по фильтрам сборной (или он исключён по санкциям)")
    t.members.append(await _team_member(rows[data.fsp_id], persons.get(data.fsp_id), t.discipline, role="RESERVE"))
    t.members.sort(key=lambda m: m.position)
    t.updated_at = datetime.now()
    store.log(data.actor, "Добавлен в сборную", f"#{t.id} {rows[data.fsp_id]['full_name']} (резерв)")
    return store.present_team(t)


@app.delete("/api/v1/teams/{tid}/members/{fsp_id}")
async def remove_team_member(tid: int, fsp_id: str, actor: str):
    t = _editable(_team(tid))
    m = next((x for x in t.members if x.fsp_id == fsp_id), None)
    if not m:
        raise HTTPException(404, "Спортсмена нет в составе")
    if len(t.members) == 1:
        raise HTTPException(422, "В сборной должен остаться хотя бы один спортсмен")
    t.members.remove(m)
    t.updated_at = datetime.now()
    store.log(actor, "Исключён из сборной", f"#{t.id} {m.full_name}")
    return store.present_team(t)


@app.post("/api/v1/teams/{tid}/members/{fsp_id}/role")
async def set_team_member_role(tid: int, fsp_id: str, data: TeamRoleIn):
    t = _editable(_team(tid))
    m = next((x for x in t.members if x.fsp_id == fsp_id), None)
    if not m:
        raise HTTPException(404, "Спортсмена нет в составе")
    if data.role == "RESERVE" and not any(x.role == "MAIN" and x is not m for x in t.members):
        raise HTTPException(422, "В основном составе должен остаться хотя бы один спортсмен")
    m.role, t.updated_at = data.role, datetime.now()
    store.log(data.actor, "Изменён статус в сборной", f"#{t.id} {m.full_name} → {TEAM_ROLE_LABELS[m.role]}")
    return store.present_team(t)


@app.post("/api/v1/teams/{tid}/approve")
async def approve_team(tid: int, data: TeamApproveIn):
    t = _editable(_team(tid))
    if not any(m.role == "MAIN" for m in t.members):
        raise HTTPException(422, "В основном составе нет спортсменов")
    t.status, t.order_no, t.order_date, t.updated_at = "APPROVED", data.order_no, data.order_date, datetime.now()
    store.log(data.actor, "Утверждён состав сборной", f"#{t.id} {t.title} · приказ {t.order_no}")
    return store.present_team(t)


@app.delete("/api/v1/teams/{tid}")
async def delete_team(tid: int, actor: str):
    t = _editable(_team(tid))
    del store.teams[t.id]
    store.log(actor, "Удалена сборная", f"#{t.id} {t.title}")
    return {"deleted": t.id}


# ---------------------------------------------------------------- ЕВСК: результаты, выполнившие разрядные требования
@app.get("/api/v1/evsk/requirements")
async def evsk_requirements():
    return [{"code": code, "short": RANK_BY_CODE[code]["short"], "title": RANK_BY_CODE[code]["title"],
             "rules": [{"level": lvl, "level_label": LEVEL_LABELS[lvl], "max_place": mp, "min_participants": mn,
                        "text": evsk.requirement_text(lvl, mp, mn)} for lvl, mp, mn in rules]}
            for code, rules in EVSK_REQUIREMENTS.items()]


@app.get("/api/v1/evsk/candidates")
async def evsk_candidates(competition_id: int | None = None, since_days: int = Query(365, ge=1, le=1460)):
    """Спортсмены региона, чей результат в протоколе (в том числе черновике) выполняет требования ЕВСК.
    Для каждого результата — высший подходящий разряд и состояние относительно текущего: assign | confirm | have."""
    today = config.today()
    persons: dict[str, dict] = {}
    ranks_checked = True
    try:
        persons = {p["fsp_id"]: p for p in await clients["sportsman"].get("/api/v1/persons", role="athlete")}
    except ServiceError:
        ranks_checked = False
    rows = []
    for cid, proto in store.protocols.items():
        c = store.competitions[cid]
        if competition_id is not None and cid != competition_id:
            continue
        if competition_id is None and (today - c.end_date).days > since_days:
            continue
        if c.status == CompetitionStatus.LIVE:
            continue  # результаты идущего соревнования ещё не окончательны
        for e in proto.entries:
            if e["fsp_id"].split("-")[1] != REGION_CODE:
                continue
            met = evsk.met_ranks(c.level, e["place"], e["status"], len(proto.entries))
            p = persons.get(e["fsp_id"])
            if not met or (p and p["is_sanctioned"]):
                continue
            target = met[-1]
            held = p["rank"] if p else None
            rows.append({
                "fsp_id": e["fsp_id"], "full_name": e["full_name"], "municipality": p["municipality"] if p else "—",
                "competition_id": cid, "competition_code": c.code, "title": c.title, "date": c.end_date.isoformat(),
                "level": c.level, "level_label": LEVEL_LABELS[c.level], "published": proto.published,
                "place": e["place"], "participants": len(proto.entries), "target": target,
                "state": evsk.classify(target["code"], held) if ranks_checked and p else "unknown",
                "current_rank": held["short"] if held else None, "current_state": held["state"] if held else None,
            })
    rows.sort(key=lambda r: (r["date"], -RANK_ORDER[r["target"]["code"]]), reverse=True)
    return {"rows": rows, "ranks_checked": ranks_checked}


# ---------------------------------------------------------------- applications
@app.get("/api/v1/applications")
async def list_applications(competition_id: int | None = None, athlete_fsp_id: str | None = None,
                            submitted_by: str | None = None, status: str | None = None):
    out = [store.present_application(a) for a in store.applications
           if (competition_id is None or a.competition_id == competition_id)
           and (athlete_fsp_id is None or a.athlete_fsp_id == athlete_fsp_id)
           and (submitted_by is None or a.submitted_by == submitted_by)
           and (status is None or a.status == status)]
    return sorted(out, key=lambda x: x["created_at"], reverse=True)


@app.post("/api/v1/applications", status_code=201)
async def submit_application(data: ApplicationIn):
    c = _competition(data.competition_id)
    today = config.today()
    if c.status != CompetitionStatus.REGISTRATION_OPEN or (c.registration_deadline and c.registration_deadline < today):
        raise HTTPException(422, "Приём заявок на это соревнование закрыт")
    try:
        athlete = await clients["sportsman"].get(f"/api/v1/persons/{data.athlete_fsp_id}")
        links = await clients["sportsman"].get(f"/api/v1/athletes/{data.athlete_fsp_id}/links")
    except ServiceError as exc:
        raise HTTPException(exc.status if exc.status < 500 else 503, exc.detail)
    if "athlete" not in athlete["roles"] or c.discipline not in athlete["disciplines"]:
        raise HTTPException(422, "Спортсмен не заявлен в дисциплине соревнования")
    if athlete["is_sanctioned"]:
        raise HTTPException(422, "Спортсмен с действующей дисквалификацией не может быть заявлен")
    age, fits = age_group_fit(athlete["birth_date"], c.age_group, c.start_date)
    if not fits:
        raise HTTPException(422, f"Возраст спортсмена на дату старта ({age}) не соответствует группе «{c.age_group}»")
    if data.submitted_by != data.athlete_fsp_id and not any(
            link["trainer_fsp_id"] == data.submitted_by and link["status"] == LinkStatus.ACTIVE
            and link["discipline"] == c.discipline for link in links):
        raise HTTPException(403, "Подать заявку может сам спортсмен или его тренер с активной привязкой по дисциплине")
    if any(a.competition_id == c.id and a.athlete_fsp_id == data.athlete_fsp_id and a.status != ApplicationStatus.REJECTED
           for a in store.applications):
        raise HTTPException(409, "Заявка на это соревнование уже подана")
    app_ = Application(id=len(store.applications) + 1, competition_id=c.id, athlete_fsp_id=data.athlete_fsp_id,
                       athlete_name=athlete["full_name"], submitted_by=data.submitted_by,
                       status=ApplicationStatus.PENDING, created_at=datetime.now())
    store.applications.append(app_)
    store.log(data.submitted_by, "Подана заявка", f"{c.code} · {athlete['full_name']}")
    return store.present_application(app_)


APPLICATION_SORTS = {
    "created": lambda a, c: (a.created_at, a.id),
    "athlete": lambda a, c: (a.athlete_name.lower(), a.id),
    "start": lambda a, c: (c.start_date, a.id),
    "deadline": lambda a, c: (c.registration_deadline or date.max, a.id),
}


@app.get("/api/v1/applications/search")
async def search_applications(status: str | None = None, competition_id: int | None = None, discipline: str | None = None,
                              q: str | None = None, by: str | None = Query(None, pattern="^(trainer|self)$"),
                              sort: str = "created", order: str = Query("desc", pattern="^(asc|desc)$"),
                              offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=MAX_LIMIT)):
    """Заявки для модерации: `{total, offset, limit, items, facets}`; фасет считается без собственного фильтра.

    `q` — слова в ФИО, ФСП ID спортсмена, названии или коде соревнования; `by` — кто подал (тренер / сам спортсмен).
    """
    if sort not in APPLICATION_SORTS:
        raise HTTPException(422, f"Сортировка: {', '.join(APPLICATION_SORTS)}")
    words = tokens(q)
    base = []
    for a in store.applications:
        c = store.competitions[a.competition_id]
        if words and not matches(words, a.athlete_name, a.athlete_fsp_id, c.title, c.code):
            continue
        if by and (a.submitted_by != a.athlete_fsp_id) != (by == "trainer"):
            continue
        base.append((a, c))
    filters = {"status": status, "competition": str(competition_id) if competition_id else None, "discipline": discipline}
    keyfn = {"status": lambda r: r[0].status.value, "competition": lambda r: str(r[1].id), "discipline": lambda r: r[1].discipline}

    def keep(skip=None):
        return [r for r in base if all(not v or k == skip or keyfn[k](r) == v for k, v in filters.items())]

    rows = keep()
    rows.sort(key=lambda r: APPLICATION_SORTS[sort](*r), reverse=order == "desc")
    result = page(rows, offset, limit)
    result["items"] = [store.present_application(a) for a, _ in result["items"]]
    result["facets"] = {k: facets(keep(skip=k), {k: keyfn[k]})[k] for k in filters}
    return result


def _announce_approval(a: Application) -> None:
    """Вебхук `application.approved`: только идентификаторы, без ФИО."""
    if a.status == ApplicationStatus.APPROVED:
        c = store.competitions[a.competition_id]
        integrations.emit("application.approved", {"application_id": a.id, "competition_id": c.id, "competition_code": c.code,
                                                   "athlete_fsp_id": a.athlete_fsp_id})


@app.post("/api/v1/applications/decisions")
async def decide_applications(data: BatchDecisionIn):
    """Массовое решение: каждая заявка решается отдельно, ошибки не мешают остальным; протоколы пересчитываются один раз."""
    if data.status == ApplicationStatus.PENDING:
        raise HTTPException(422, "Выберите решение: одобрить или отклонить")
    by_id = {a.id: a for a in store.applications}
    done, failed, touched = [], [], set()
    for app_id in dict.fromkeys(data.ids):
        a = by_id.get(app_id)
        if not a:
            failed.append({"id": app_id, "detail": "Заявка не найдена"})
            continue
        if a.status != ApplicationStatus.PENDING:
            failed.append({"id": app_id, "detail": "Решение уже принято"})
            continue
        a.status, a.decided_at, a.comment = data.status, datetime.now(), data.comment
        touched.add(a.competition_id)
        store.log(data.actor, "Модерация заявки", f"#{a.id} {a.athlete_name} → {data.status} (пакетно)")
        _announce_approval(a)
        done.append(store.present_application(a))
    for cid in touched:
        if (proto := store.protocols.get(cid)) and not proto.published:
            store.reparse(proto)
    return {"done": done, "failed": failed}


@app.post("/api/v1/applications/{app_id}/decision")
async def decide_application(app_id: int, data: DecisionIn):
    a = next((x for x in store.applications if x.id == app_id), None)
    if not a:
        raise HTTPException(404, "Заявка не найдена")
    if a.status != ApplicationStatus.PENDING or data.status == ApplicationStatus.PENDING:
        raise HTTPException(422, "Решение по заявке уже принято")
    a.status, a.decided_at, a.comment = data.status, datetime.now(), data.comment
    if (proto := store.protocols.get(a.competition_id)) and not proto.published:
        store.reparse(proto)
    store.log(data.actor, "Модерация заявки", f"#{a.id} {a.athlete_name} → {data.status}")
    _announce_approval(a)
    return store.present_application(a)


# ---------------------------------------------------------------- judges
@app.get("/api/v1/judges/{fsp_id}/assignments")
async def judge_assignments(fsp_id: str):
    today = config.today()
    upcoming, past = [], []
    for c in sorted(store.competitions.values(), key=lambda x: x.start_date):
        for j in c.judges:
            if j.fsp_id == fsp_id:
                item = {**store.present_competition(c, today), "position": j.position}
                (past if c.end_date < today and c.status == CompetitionStatus.FINISHED else upcoming).append(item)
    return {"upcoming": upcoming, "past": list(reversed(past))}


_SINGLE_POSITIONS = {"Главный судья", "Главный секретарь"}


@app.post("/api/v1/competitions/{cid}/judges", status_code=201)
async def assign_judge(cid: int, data: JudgeAssignIn):
    c = _competition(cid)
    if c.status == CompetitionStatus.FINISHED:
        raise HTTPException(422, "Соревнование завершено — состав судейской коллегии зафиксирован в протоколе")
    if data.position not in JUDGE_POSITIONS:
        raise HTTPException(422, "Неизвестная судейская должность")
    if any(j.fsp_id == data.fsp_id for j in c.judges):
        raise HTTPException(409, "Судья уже входит в коллегию этого соревнования")
    if data.position in _SINGLE_POSITIONS and any(j.position == data.position for j in c.judges):
        raise HTTPException(409, f"Должность «{data.position}» уже занята")
    try:
        judge = await clients["sportsman"].get(f"/api/v1/persons/{data.fsp_id}")
    except ServiceError as exc:
        raise HTTPException(exc.status if exc.status < 500 else 503, exc.detail) from exc
    if "judge" not in judge["roles"]:
        raise HTTPException(422, "У участника нет роли «Судья»")
    cat = judge["judge_category"]
    if not cat or cat["expires_at"] < c.end_date.isoformat():
        raise HTTPException(422, "Судейская категория истекает до окончания соревнования — сначала подтвердите её")
    c.judges.append(JudgeAssignment(fsp_id=data.fsp_id, full_name=judge["full_name"], position=data.position))
    store.log(data.actor, "Назначен судья", f"{c.code}: {judge['full_name']} — {data.position}")
    return store.present_competition(c)


@app.delete("/api/v1/competitions/{cid}/judges/{fsp_id}")
async def remove_judge(cid: int, fsp_id: str, actor: str):
    c = _competition(cid)
    if c.status == CompetitionStatus.FINISHED:
        raise HTTPException(422, "Соревнование завершено — состав судейской коллегии зафиксирован в протоколе")
    j = next((x for x in c.judges if x.fsp_id == fsp_id), None)
    if not j:
        raise HTTPException(404, "Судья не входит в коллегию")
    c.judges.remove(j)
    store.log(actor, "Снят судья", f"{c.code}: {j.full_name} — {j.position}")
    return store.present_competition(c)


# ---------------------------------------------------------------- dashboard KPIs
@app.get("/api/v1/dashboard")
async def dashboard():
    today = config.today()
    seasons = _seasons()
    season = seasons[0] if seasons else today.year
    comps = [store.present_competition(c, today) for c in store.competitions.values()]
    season_results = store.published_results(season)
    return {
        "season": season,
        "competitions_season": sum(c["season"] == season for c in comps),
        "finished": sum(c["status"] == CompetitionStatus.FINISHED and c["season"] == season for c in comps),
        "participants_unique": len({r["fsp_id"] for r in season_results if r["fsp_id"].split("-")[1] == REGION_CODE}),
        "starts": sum(r["fsp_id"].split("-")[1] == REGION_CODE for r in season_results),
        "live": [c for c in comps if c["status"] == CompetitionStatus.LIVE],
        "upcoming": sorted([c for c in comps if c["days_to_start"] > 0], key=lambda c: c["start_date"])[:6],
        "deadlines": sorted([c for c in comps if c["days_to_deadline"] is not None and 0 <= c["days_to_deadline"] <= 45],
                            key=lambda c: c["days_to_deadline"]),
        "applications_pending": [store.present_application(a) for a in store.applications if a.status == ApplicationStatus.PENDING],
        "documents_issued": len(store.documents),
        "governor_award_deadline": f"{today.year + 1 if today.month > 1 else today.year}-02-01",
    }


# ---------------------------------------------------------------- documents (participation certificate, case Д10)
def _verification_code(number: str, fsp_id: str, cid: int) -> str:
    msg = f"{number}|{fsp_id}|{cid}".encode()
    return hmac.new(config.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:12].upper()


@app.post("/api/v1/documents/participation", status_code=201)
async def generate_participation(data: DocumentIn):
    c = _competition(data.competition_id)
    proto = store.protocols.get(c.id)
    if not proto or not proto.published:
        raise HTTPException(422, "Справка формируется только по опубликованному протоколу")
    entry = next((e for e in proto.entries if e["fsp_id"] == data.fsp_id), None)
    if not entry:
        raise HTTPException(404, "Участник отсутствует в протоколе соревнования")
    person = {}
    try:
        person = await clients["sportsman"].get(f"/api/v1/persons/{data.fsp_id}")
    except ServiceError:
        pass
    today = config.today()
    store.doc_seq += 1
    number = f"{REGION_CODE}-{today.year}-{store.doc_seq:05d}"
    code = _verification_code(number, data.fsp_id, c.id)
    doc = {
        "number": number, "verification_code": code, "issued_at": datetime.now().isoformat(timespec="seconds"),
        "issued_by": data.issued_by, "purpose": data.purpose,
        "person": {"fsp_id": data.fsp_id, "full_name": entry["full_name"], "birth_date": person.get("birth_date"),
                   "gender": person.get("gender"),
                   "municipality": person.get("municipality"), "organization": person.get("organization"),
                   "rank": (person.get("rank") or {}).get("title")},
        "competition": store.present_competition(c, today),
        "result": {"place": entry["place"], "status": entry["status"], "dsq_reason": entry["dsq_reason"],
                   "solved": entry["solved"], "penalty": entry["penalty"], "score": entry["score"],
                   "mode": proto.mode, "participants": len(proto.entries), "base_points": entry["base_points"]},
        "verify_path": f"/verify/{number}?c={code}",
    }
    store.documents[number] = doc
    store.log(data.issued_by, "Сформирована справка", f"№ {number} · {entry['full_name']} · {c.code}")
    return doc


@app.get("/api/v1/documents/{number}")
async def get_document(number: str):
    doc = store.documents.get(number)
    if not doc:
        raise HTTPException(404, "Документ не найден")
    return doc


@app.get("/api/v1/documents/{number}/verify")
async def verify_document(number: str, code: str = ""):
    doc = store.documents.get(number)
    valid = bool(doc) and hmac.compare_digest(doc["verification_code"], code.upper())
    return {"valid": valid, "number": number, "document": doc if valid else None}

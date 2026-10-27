"""market_service (port 8003): биржа программистов — анкеты с портфолио и навыками, отклики с предложениями проектов."""
import hashlib
import hmac
import secrets
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from market_service.models import (
    ActorIn, ApiToken, DecisionIn, Offer, OfferIn, Profile, ProfileIn, StatusIn, TokenIn, TokenVerifyIn,
)
from market_service.seed import seed
from market_service.store import DDL, OFFER_STATUS_LABELS, restore, snapshot, store
from shared import service_auth
from shared.db import Persistence
from shared.listing import AUDIT_LIMIT, audit_page, facets, matches, page, tokens

db = Persistence(DDL)
PROFILE_SORTS = ("new", "name", "offers")
TOKEN_PREFIX = "fspm_"
MAX_ACTIVE_TOKENS = 5
LAST_USED_GRANULARITY = timedelta(minutes=1)  # «последнее использование» пишется не чаще — иначе каждый запрос меняет БД


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if db.enabled:
        db.init()
        rows = db.load()
        if rows["market_profiles"]:
            restore(store, rows)
            db.mark_loaded(snapshot(store))
        else:
            seed(store)
            db.save(snapshot(store))
    else:
        seed(store)
    yield


app = FastAPI(title="ФСП Оренбург · Market Service", version="1.0.0", lifespan=lifespan)
service_auth.install(app)


@app.middleware("http")
async def persist_writes(request: Request, call_next):
    response = await call_next(request)
    if db.enabled and request.method != "GET":
        await run_in_threadpool(lambda: db.save(snapshot(store)))
    return response


def _profile(pid: int) -> Profile:
    p = store.profiles.get(pid)
    if not p:
        raise HTTPException(404, "Анкета не найдена")
    return p


def _offer(oid: int) -> Offer:
    o = store.offers.get(oid)
    if not o:
        raise HTTPException(404, "Отклик не найден")
    return o


def _owned(p: Profile, actor: str) -> None:
    if p.owner_fsp_id != actor:
        raise HTTPException(403, "Анкета принадлежит другому участнику")


@app.get("/health")
async def health():
    return {"service": "market_service", "status": "ok", "profiles": len(store.profiles), "offers": len(store.offers)}


@app.get("/api/v1/stats")
async def stats():
    active = [p for p in store.profiles.values() if p.status == "ACTIVE"]
    return {"profiles": len(active), "offers": len(store.offers),
            "pending": sum(o.status == "PENDING" for o in store.offers.values())}


@app.get("/api/v1/audit/search")
async def audit_search(q: str | None = None, actor: str | None = None, order: str = Query("desc", pattern="^(asc|desc)$"),
                       offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=AUDIT_LIMIT)):
    return audit_page(store.audit, q, actor, None, None, order, offset, limit)


# ---------------------------------------------------------------- анкеты
@app.get("/api/v1/profiles")
async def list_profiles(q: str | None = None, skill: str | None = None, availability: str | None = None,
                        owner: str | None = None, status: str = Query("ACTIVE", pattern="^(ACTIVE|ARCHIVED|all)$"),
                        viewer: str | None = None, sort: str = Query("new"), order: str = Query("desc", pattern="^(asc|desc)$"),
                        offset: int = Query(0, ge=0), limit: int = Query(24, ge=1, le=100)):
    """Лента анкет. `q` — слова в заголовке, описании, навыках и портфолио. Ответ — страница с фасетами навыков."""
    if sort not in PROFILE_SORTS:
        raise HTTPException(422, "Неизвестная сортировка")
    words = tokens(q)
    base = [p for p in store.profiles.values() if (status == "all" or p.status == status)
            and (not owner or p.owner_fsp_id == owner)
            and matches(words, p.headline, p.about, p.owner_name, " ".join(p.skills),
                        " ".join(f"{i.title} {i.description}" for i in p.portfolio))]
    has_skill = lambda p: not skill or skill.lower() in (s.lower() for s in p.skills)  # noqa: E731
    has_availability = lambda p: not availability or p.availability == availability  # noqa: E731
    shown = [p for p in base if has_skill(p) and has_availability(p)]
    shown.sort(key={"new": lambda p: p.updated_at, "name": lambda p: p.owner_name,
                    "offers": lambda p: len(store.offers_of(p.id))}[sort], reverse=order == "desc")
    # Фасет считается без собственного фильтра: сколько анкет найдётся при выборе значения.
    names = {s.lower(): s for p in base for s in p.skills}
    skill_counts = facets([p for p in base if has_availability(p)], {"skills": lambda p: [s.lower() for s in p.skills]})["skills"]
    top = Counter(skill_counts).most_common(30)
    return page([store.present_profile(p, viewer) for p in shown], offset, limit, max_limit=100, facets={
        "skills": {names[k]: n for k, n in top},
        "availability": facets([p for p in base if has_skill(p)], {"availability": lambda p: p.availability})["availability"]})


@app.get("/api/v1/profiles/{pid}")
async def get_profile(pid: int, viewer: str | None = None):
    return store.present_profile(_profile(pid), viewer)


@app.post("/api/v1/profiles", status_code=201)
async def create_profile(body: ProfileIn):
    if store.active_profile_of(body.actor):
        raise HTTPException(409, "У вас уже есть активная анкета — отредактируйте её или архивируйте перед созданием новой")
    now = datetime.now()
    store.profile_seq += 1
    p = Profile(id=store.profile_seq, owner_fsp_id=body.actor, owner_name=body.owner_name, headline=body.headline.strip(),
                about=body.about.strip(), skills=body.skills, portfolio=body.portfolio, rate=body.rate.strip(),
                contact=body.contact.strip(), availability=body.availability, created_at=now, updated_at=now)
    store.profiles[p.id] = p
    store.log(body.actor, "Опубликована анкета", f"анкета #{p.id}: {p.headline}")
    return store.present_profile(p, body.actor)


@app.put("/api/v1/profiles/{pid}")
async def update_profile(pid: int, body: ProfileIn):
    p = _profile(pid)
    _owned(p, body.actor)
    if p.status != "ACTIVE":
        raise HTTPException(409, "Архивную анкету нельзя редактировать — верните её в ленту")
    p.headline, p.about, p.skills = body.headline.strip(), body.about.strip(), body.skills
    p.portfolio, p.rate, p.contact = body.portfolio, body.rate.strip(), body.contact.strip()
    p.availability, p.updated_at = body.availability, datetime.now()
    store.log(body.actor, "Изменена анкета", f"анкета #{p.id}: {p.headline}")
    return store.present_profile(p, body.actor)


@app.post("/api/v1/profiles/{pid}/status")
async def set_profile_status(pid: int, body: StatusIn):
    p = _profile(pid)
    _owned(p, body.actor)
    if p.status == body.status:
        return store.present_profile(p, body.actor)
    if body.status == "ACTIVE" and store.active_profile_of(body.actor):
        raise HTTPException(409, "У вас уже есть другая активная анкета")
    p.status, p.updated_at = body.status, datetime.now()
    store.log(body.actor, "Анкета возвращена в ленту" if body.status == "ACTIVE" else "Анкета архивирована", f"анкета #{p.id}")
    return store.present_profile(p, body.actor)


# ---------------------------------------------------------------- токены доступа к API / MCP
@app.get("/api/v1/tokens")
async def list_tokens(owner: str):
    rows = sorted((t for t in store.tokens.values() if t.owner_fsp_id == owner), key=lambda t: t.created_at, reverse=True)
    return [store.present_token(t) for t in rows]


@app.post("/api/v1/tokens", status_code=201)
async def create_token(body: TokenIn):
    """Выпускает токен. Открытое значение есть только в этом ответе — дальше хранится его хэш."""
    if sum(t.owner_fsp_id == body.actor and t.revoked_at is None for t in store.tokens.values()) >= MAX_ACTIVE_TOKENS:
        raise HTTPException(409, f"Не больше {MAX_ACTIVE_TOKENS} активных токенов — отзовите ненужный")
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    store.token_seq += 1
    t = ApiToken(id=store.token_seq, owner_fsp_id=body.actor, name=body.name.strip(), prefix=secret[:len(TOKEN_PREFIX) + 4],
                 digest=_digest(secret), created_at=datetime.now())
    store.tokens[t.id] = t
    store.log(body.actor, "Выпущен токен API", f"токен #{t.id}: {t.name}")
    return {**store.present_token(t), "token": secret}


@app.post("/api/v1/tokens/{tid}/revoke")
async def revoke_token(tid: int, body: ActorIn):
    t = store.tokens.get(tid)
    if not t:
        raise HTTPException(404, "Токен не найден")
    if t.owner_fsp_id != body.actor:
        raise HTTPException(403, "Токен принадлежит другому участнику")
    if t.revoked_at is None:
        t.revoked_at = datetime.now()
        store.log(body.actor, "Отозван токен API", f"токен #{t.id}: {t.name}")
    return store.present_token(t)


@app.post("/api/v1/tokens/verify")
async def verify_token(body: TokenVerifyIn):
    """Шлюз узнаёт владельца по токену; чужой, неизвестный и отозванный токены неотличимы (`401`)."""
    digest = _digest(body.token)
    found = next((t for t in store.tokens.values() if hmac.compare_digest(t.digest, digest)), None)
    if not found or found.revoked_at is not None:
        raise HTTPException(401, "Токен недействителен или отозван")
    now = datetime.now()
    if found.last_used_at is None or now - found.last_used_at > LAST_USED_GRANULARITY:
        found.last_used_at = now
    return {"fsp_id": found.owner_fsp_id, "token_id": found.id, "name": found.name}


# ---------------------------------------------------------------- отклики
@app.get("/api/v1/offers")
async def list_offers(profile_id: int | None = None, from_fsp_id: str | None = None, owner_fsp_id: str | None = None,
                      status: str | None = None):
    """Отклики (новые сверху). Видны только участникам обмена — доступ ограничивает шлюз."""
    if status and status not in OFFER_STATUS_LABELS:
        raise HTTPException(422, "Неизвестный статус отклика")
    rows = [o for o in store.offers.values()
            if (profile_id is None or o.profile_id == profile_id) and (not from_fsp_id or o.from_fsp_id == from_fsp_id)
            and (not owner_fsp_id or store.profiles[o.profile_id].owner_fsp_id == owner_fsp_id)
            and (not status or o.status == status)]
    rows.sort(key=lambda o: o.created_at, reverse=True)
    return [store.present_offer(o) for o in rows]


@app.post("/api/v1/offers", status_code=201)
async def create_offer(body: OfferIn):
    p = _profile(body.profile_id)
    if p.status != "ACTIVE":
        raise HTTPException(409, "Анкета снята с публикации — отклик невозможен")
    if p.owner_fsp_id == body.from_fsp_id:
        raise HTTPException(422, "Нельзя откликнуться на собственную анкету")
    if any(o.from_fsp_id == body.from_fsp_id and o.status == "PENDING" for o in store.offers_of(p.id)):
        raise HTTPException(409, "Ваш отклик на эту анкету уже на рассмотрении — отзовите его, чтобы отправить новый")
    store.offer_seq += 1
    o = Offer(id=store.offer_seq, profile_id=p.id, from_fsp_id=body.from_fsp_id, from_name=body.from_name,
              project_title=body.project_title.strip(), description=body.description.strip(), budget=body.budget.strip(),
              contact=body.contact.strip(), created_at=datetime.now())
    store.offers[o.id] = o
    store.log(body.from_fsp_id, "Отправлен отклик", f"отклик #{o.id} на анкету #{p.id}: {o.project_title}")
    return store.present_offer(o)


@app.post("/api/v1/offers/{oid}/decision")
async def decide_offer(oid: int, body: DecisionIn):
    o = _offer(oid)
    _owned(store.profiles[o.profile_id], body.actor)
    if o.status != "PENDING":
        raise HTTPException(409, f"Отклик уже обработан: {OFFER_STATUS_LABELS[o.status].lower()}")
    o.status, o.note, o.decided_at = "ACCEPTED" if body.accept else "DECLINED", body.note.strip(), datetime.now()
    store.log(body.actor, "Принят отклик" if body.accept else "Отклонён отклик", f"отклик #{o.id}: {o.project_title}")
    return store.present_offer(o)


@app.post("/api/v1/offers/{oid}/withdraw")
async def withdraw_offer(oid: int, body: ActorIn):
    o = _offer(oid)
    if o.from_fsp_id != body.actor:
        raise HTTPException(403, "Отозвать отклик может только его автор")
    if o.status != "PENDING":
        raise HTTPException(409, f"Отклик уже обработан: {OFFER_STATUS_LABELS[o.status].lower()}")
    o.status, o.decided_at = "WITHDRAWN", datetime.now()
    store.log(body.actor, "Отозван отклик", f"отклик #{o.id}: {o.project_title}")
    return store.present_offer(o)

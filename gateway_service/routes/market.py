"""Биржа программистов: лента анкет, анкета с портфолио, отклики с предложением проекта, «моя биржа»."""
import asyncio
from itertools import zip_longest

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gateway_service import notify
from gateway_service.listing_ui import chips, list_args, pages
from gateway_service.web import back, clients, humanize, render, require, safe
from shared import config
from shared.http import ServiceError

router = APIRouter()

MARKET_SORTS = ("new", "name", "offers")
PORTFOLIO_ROWS = 4  # пустых строк портфолио в форме; заполненные сохраняются
AVAILABILITY = {"OPEN": "Открыт к проектам", "BUSY": "Занят, но рассмотрю предложения"}
FILTER_LABELS = {"q": lambda v: f"«{v}»", "skill": lambda v: f"навык {v}", "availability": lambda v: AVAILABILITY.get(v, v)}


def _me(user: dict) -> tuple[str, str]:
    p = user["person"]
    return p["fsp_id"], p["full_name"]


def _skills(raw: str) -> list[str]:
    return [s for s in (part.strip() for part in raw.replace(";", ",").replace("\n", ",").split(",")) if s]


def _profile_payload(form, user: dict) -> dict:
    fid, name = _me(user)
    rows = zip_longest(form.getlist("pf_title"), form.getlist("pf_url"), form.getlist("pf_desc"), fillvalue="")
    portfolio = [{"title": t.strip(), "url": u.strip(), "description": d.strip()} for t, u, d in rows if t.strip() or u.strip()]
    return {"actor": fid, "owner_name": name, "headline": str(form.get("headline", "")), "about": str(form.get("about", "")),
            "skills": _skills(str(form.get("skills", ""))), "portfolio": portfolio, "rate": str(form.get("rate", "")),
            "contact": str(form.get("contact", "")), "availability": str(form.get("availability", "OPEN"))}


def _form_values(payload: dict) -> dict:
    """Введённое пользователем — обратно в форму после ошибки проверки."""
    return {**payload, "skills": ", ".join(payload["skills"])}


@router.get("/market", response_class=HTMLResponse)
async def market_page(request: Request, q: str = "", skill: str = "", availability: str = ""):
    user = await require(request)
    fid, _ = _me(user)
    args = list_args(request, MARKET_SORTS, "new", "asc" if request.query_params.get("sort") == "name" else "desc")
    degraded: list[str] = []
    filters = {"q": q.strip(), "skill": skill.strip(), "availability": availability}
    feed, mine = await asyncio.gather(
        safe(clients["market"].get("/api/v1/profiles", **{k: v for k, v in filters.items() if v}, viewer=fid,
                                   sort=args["sort"], order=args["order"], offset=args["offset"], limit=min(args["limit"], 100)),
             {"items": [], "total": 0, "offset": 0, "limit": args["limit"], "facets": {"skills": {}, "availability": {}}}, degraded),
        safe(clients["market"].get("/api/v1/profiles", owner=fid, viewer=fid, limit=1), {"items": []}, degraded))
    filter_chips, reset_href = chips(request, filters, FILTER_LABELS)
    return render(request, "market.html", user=user, items=feed["items"], pg=pages(feed), facets=feed["facets"], filters=filters,
                  sort=args["sort"], order=args["order"], chips=filter_chips, reset_href=reset_href, availability_labels=AVAILABILITY,
                  my_profile=mine["items"][0] if mine["items"] else None, degraded=degraded)


async def _my_page(request: Request, user: dict, new_token: dict | None = None) -> HTMLResponse:
    fid, _ = _me(user)
    degraded: list[str] = []
    profiles, incoming, outgoing, tokens = await asyncio.gather(
        safe(clients["market"].get("/api/v1/profiles", owner=fid, viewer=fid, status="all", limit=100), {"items": []}, degraded),
        safe(clients["market"].get("/api/v1/offers", owner_fsp_id=fid), [], degraded),
        safe(clients["market"].get("/api/v1/offers", from_fsp_id=fid), [], degraded),
        safe(clients["market"].get("/api/v1/tokens", owner=fid), [], degraded))
    items = profiles["items"]
    active = next((p for p in items if p["status"] == "ACTIVE"), None)
    return render(request, "market_my.html", user=user, active=active, archived=[p for p in items if p["status"] != "ACTIVE"],
                  incoming=incoming, outgoing=outgoing, pending=sum(o["status"] == "PENDING" for o in incoming), degraded=degraded,
                  tokens=[t for t in tokens if t["active"]], new_token=new_token, api_base=f"{config.PUBLIC_BASE_URL}/api/market/v1")


@router.get("/market/my", response_class=HTMLResponse)
async def my_market(request: Request):
    return await _my_page(request, await require(request))


@router.post("/market/tokens", response_class=HTMLResponse)
async def create_token(request: Request):
    """Токен показывается один раз прямо в ответе (без редиректа и cookie), дальше хранится только хэш."""
    user = await require(request)
    form = await request.form()
    try:
        token = await clients["market"].post("/api/v1/tokens", json={"actor": _me(user)[0], "name": str(form.get("name", ""))})
    except ServiceError as exc:
        return back("/market/my#api", "error", humanize(exc.detail))
    resp = await _my_page(request, user, new_token=token)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/market/tokens/{tid}/revoke")
async def revoke_token(request: Request, tid: int):
    user = await require(request)
    try:
        await clients["market"].post(f"/api/v1/tokens/{tid}/revoke", json={"actor": _me(user)[0]})
    except ServiceError as exc:
        return back("/market/my#api", "error", humanize(exc.detail))
    return back("/market/my#api", "ok", "Токен отозван — доступ по нему закрыт")


def _form_page(request: Request, user: dict, profile: dict | None, values: dict | None = None, error: str = ""):
    base = values or (profile and {**profile, "skills": ", ".join(profile["skills"])}) or {"availability": "OPEN", "portfolio": []}
    rows = list(base.get("portfolio") or [])
    rows += [{"title": "", "url": "", "description": ""}] * max(PORTFOLIO_ROWS - len(rows), 1)
    return render(request, "market_form.html", user=user, profile=profile, v=base, rows=rows, error=error,
                  availability_labels=AVAILABILITY)


@router.get("/market/new", response_class=HTMLResponse)
async def new_profile_page(request: Request):
    user = await require(request)
    fid, _ = _me(user)
    mine = await clients["market"].get("/api/v1/profiles", owner=fid, viewer=fid, limit=1)
    if mine["items"]:
        return back("/market/my", "error", "У вас уже есть активная анкета — отредактируйте её")
    return _form_page(request, user, None)


@router.get("/market/{pid:int}/edit", response_class=HTMLResponse)
async def edit_profile_page(request: Request, pid: int):
    user = await require(request)
    fid, _ = _me(user)
    profile = await clients["market"].get(f"/api/v1/profiles/{pid}", viewer=fid)
    if profile["owner_fsp_id"] != fid or profile["status"] != "ACTIVE":
        return back("/market/my", "error", "Редактировать можно только свою активную анкету")
    return _form_page(request, user, profile)


@router.post("/market/save")
async def save_profile(request: Request):
    user = await require(request)
    form = await request.form()
    payload = _profile_payload(form, user)
    pid = str(form.get("id", ""))
    try:
        if pid.isdigit():
            profile = await clients["market"].put(f"/api/v1/profiles/{pid}", json=payload)
        else:
            profile = await clients["market"].post("/api/v1/profiles", json=payload)
    except ServiceError as exc:
        if exc.unavailable:
            raise
        current = await safe(clients["market"].get(f"/api/v1/profiles/{pid}", viewer=payload["actor"]), None, []) if pid.isdigit() else None
        return _form_page(request, user, current, _form_values(payload), humanize(exc.detail))
    return back(f"/market/{profile['id']}", "ok", "Анкета опубликована" if not pid.isdigit() else "Анкета обновлена")


@router.get("/market/{pid:int}", response_class=HTMLResponse)
async def profile_page(request: Request, pid: int):
    user = await require(request)
    fid, _ = _me(user)
    profile = await clients["market"].get(f"/api/v1/profiles/{pid}", viewer=fid)
    is_owner = profile["owner_fsp_id"] == fid
    if profile["status"] != "ACTIVE" and not is_owner:
        return back("/market", "error", "Анкета снята с публикации")
    offers = await safe(clients["market"].get("/api/v1/offers", profile_id=pid), [], []) if is_owner else []
    mine = [] if is_owner else await safe(clients["market"].get("/api/v1/offers", profile_id=pid, from_fsp_id=fid), [], [])
    return render(request, "market_profile.html", user=user, p=profile, is_owner=is_owner, offers=offers, mine=mine,
                  pending_mine=any(o["status"] == "PENDING" for o in mine))


@router.post("/market/{pid:int}/status")
async def profile_status(request: Request, pid: int):
    user = await require(request)
    form = await request.form()
    status = "ACTIVE" if form.get("status") == "ACTIVE" else "ARCHIVED"
    try:
        await clients["market"].post(f"/api/v1/profiles/{pid}/status", json={"actor": _me(user)[0], "status": status})
    except ServiceError as exc:
        return back("/market/my", "error", humanize(exc.detail))
    return back("/market/my", "ok", "Анкета возвращена в ленту" if status == "ACTIVE" else "Анкета архивирована и скрыта из ленты")


@router.post("/market/{pid:int}/offer")
async def send_offer(request: Request, pid: int):
    user = await require(request)
    fid, name = _me(user)
    form = await request.form()
    try:
        offer = await clients["market"].post("/api/v1/offers", json={
            "profile_id": pid, "from_fsp_id": fid, "from_name": name, "project_title": str(form.get("project_title", "")),
            "description": str(form.get("description", "")), "budget": str(form.get("budget", "")),
            "contact": str(form.get("contact", ""))})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(f"/market/{pid}#offer", "error", humanize(exc.detail))
    await notify.notify(offer["owner_fsp_id"], "Новый отклик на вашу анкету", f"{name}: {offer['project_title']}", "/market/my#incoming")
    return back("/market/my#outgoing", "ok", f"Отклик отправлен: {offer['project_title']}")


@router.post("/market/offers/{oid}/decision")
async def decide_offer(request: Request, oid: int):
    user = await require(request)
    form = await request.form()
    accept = form.get("decision") == "accept"
    try:
        offer = await clients["market"].post(f"/api/v1/offers/{oid}/decision", json={
            "actor": _me(user)[0], "accept": accept, "note": str(form.get("note", ""))})
    except ServiceError as exc:
        return back("/market/my#incoming", "error", humanize(exc.detail))
    await notify.notify(offer["from_fsp_id"], "Отклик принят" if accept else "Отклик отклонён",
                        f"{offer['project_title']} · {offer['owner_name']}", "/market/my#outgoing")
    return back("/market/my#incoming", "ok", f"Отклик «{offer['project_title']}»: {offer['status_label'].lower()}")


@router.post("/market/offers/{oid}/withdraw")
async def withdraw_offer(request: Request, oid: int):
    user = await require(request)
    try:
        offer = await clients["market"].post(f"/api/v1/offers/{oid}/withdraw", json={"actor": _me(user)[0]})
    except ServiceError as exc:
        return back("/market/my#outgoing", "error", humanize(exc.detail))
    return back("/market/my#outgoing", "ok", f"Отклик «{offer['project_title']}» отозван")

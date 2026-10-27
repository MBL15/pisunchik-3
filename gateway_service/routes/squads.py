"""Команды в продуктовом программировании и CTF: страница «Команды» (список, создание, приглашения) и выбор дисциплин спортсменом."""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from gateway_service.web import back, clients, humanize, render, require, safe, safe_back
from shared import domain
from shared.domain import TEAM_DISCIPLINES, Role
from shared.http import ServiceError

router = APIRouter()
VIEW_ROLES = (Role.ATHLETE, Role.TRAINER, Role.ADMIN, Role.MINISTRY)
ACTIONS = {"invite": "Приглашение отправлено", "respond": None, "revoke": "Приглашение отозвано", "kick": "Участник исключён из команды",
           "leave": "Вы вышли из команды", "disband": "Команда распущена"}


@router.get("/squads", response_class=HTMLResponse)
async def squads_page(request: Request, discipline: str = "", q: str = ""):
    user = await require(request, *VIEW_ROLES)
    if discipline and discipline not in TEAM_DISCIPLINES:
        raise HTTPException(422, "Команды есть в продуктовом программировании и кибербезопасности (CTF)")
    degraded: list[str] = []
    fid = user["person"]["fsp_id"]
    athlete = user["role"] == Role.ATHLETE
    squads = await safe(clients["sportsman"].get("/api/v1/squads", discipline=discipline or None, q=q or None), [], degraded)
    mine = await safe(clients["sportsman"].get(f"/api/v1/athletes/{fid}/squads"), None, degraded) if athlete else None
    return render(request, "squads.html", user=user, squads=squads, mine=mine, filters={"discipline": discipline, "q": q},
                  team_disciplines=[domain.DISCIPLINE_BY_CODE[d] for d in TEAM_DISCIPLINES], max_size=domain.TEAM_MAX_SIZE,
                  my_ids={s["id"] for s in mine["squads"]} if mine else set(), degraded=degraded)


@router.post("/squads")
async def create_squad(request: Request):
    user = await require(request, Role.ATHLETE)
    form = await request.form()
    try:
        s = await clients["sportsman"].post("/api/v1/squads", json={
            "title": str(form.get("title", "")), "discipline": str(form.get("discipline", "")), "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/squads", "error", humanize(exc.detail))
    return back(f"/squads#squad-{s['id']}", "ok", f"Команда «{s['title']}» создана — вы капитан. Пригласите участников по ФСП ID.", celebrate=True)


@router.post("/squads/{sid}/{action}")
async def squad_action(request: Request, sid: int, action: str):
    user = await require(request, Role.ATHLETE, Role.ADMIN)
    if action not in ACTIONS:
        raise HTTPException(404, "Неизвестное действие")
    form = await request.form()
    me = user["person"]["fsp_id"]
    payload: dict = {"actor": me}
    if action in ("invite", "revoke", "kick"):
        payload["fsp_id"] = str(form.get("fsp_id", "")).strip()
    if action == "respond":
        payload["accept"] = form.get("decision") == "accept"
    nxt = safe_back(str(form.get("next", "")), "/squads")
    try:
        await clients["sportsman"].post(f"/api/v1/squads/{sid}/{action}", json=payload)
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(nxt, "error", humanize(exc.detail))
    text = ACTIONS[action] or ("Вы в команде" if payload["accept"] else "Приглашение отклонено")
    return back(nxt, "ok", text, celebrate=action == "respond" and payload["accept"])


@router.post("/cabinet/disciplines")
async def save_disciplines(request: Request):
    user = await require(request, Role.ATHLETE)
    form = await request.form()
    me = user["person"]["fsp_id"]
    chosen = [d for d in form.getlist("disciplines") if isinstance(d, str)]
    try:
        await clients["sportsman"].put(f"/api/v1/persons/{me}/disciplines", json={"disciplines": chosen, "actor": me})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/cabinet#disciplines", "error", humanize(exc.detail))
    added = [d for d in chosen if d in TEAM_DISCIPLINES and d not in user["person"]["disciplines"]]
    hint = " В командных дисциплинах можно создать команду или принять приглашение — раздел «Команды»." if added else ""
    return back("/cabinet#disciplines", "ok", "Дисциплины сохранены." + hint)

"""Д3. Организации спортивной подготовки: карточка (лицензия, программы), учебно-тренировочные группы по этапам, зачисление и перевод, журнал занятий."""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from gateway_service import notify
from gateway_service.web import back, clients, humanize, render, require, safe
from shared import domain
from shared.domain import Role
from shared.http import ServiceError

router = APIRouter()
VIEW = (Role.ADMIN, Role.TRAINER, Role.MINISTRY)
CONFIRMATIONS = {"RECORDING": "Запись занятия (ссылка)", "PLATFORM_LOG": "Лог платформы (номер)", "SIGN_SHEET": "Лист посещаемости"}


@router.get("/organizations", response_class=HTMLResponse)
async def organizations_page(request: Request):
    user = await require(request, *VIEW)
    degraded: list[str] = []
    orgs = await safe(clients["sportsman"].get("/api/v1/organizations"), [], degraded)
    named = [o["name"] for o in orgs if o["name"] in domain.ORGANIZATION_INFO]
    cards = await asyncio.gather(*(safe(clients["sportsman"].get("/api/v1/organizations/" + n.replace("/", "%2F") + "/card"), None, degraded) for n in named))
    return render(request, "organizations.html", user=user, cards=[c for c in cards if c], degraded=sorted(set(degraded)))


@router.get("/organizations/card", response_class=HTMLResponse)
async def organization_page(request: Request, name: str):
    user = await require(request, *VIEW)
    card = await clients["sportsman"].get("/api/v1/organizations/" + name.replace("/", "%2F") + "/card")
    chosen = {(p["stage"], p["discipline"]) for p in card["programs"]}
    return render(request, "organization.html", user=user, c=card, stages=domain.TRAINING_STAGES, disciplines=domain.DISCIPLINES,
                  chosen=chosen, can_edit=user["role"] == Role.ADMIN, can_group=user["role"] in (Role.ADMIN, Role.TRAINER))


@router.post("/organizations/card")
async def save_card(request: Request):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    name = str(form.get("name", ""))
    number = str(form.get("license_number", "")).strip()
    programs = [{"stage": s, "discipline": d} for s, _, d in (v.partition("|") for v in form.getlist("program"))]
    payload = {"actor": user["person"]["fsp_id"], "description": str(form.get("description", "")), "programs": programs,
               "license": {"number": number, "issued_at": str(form.get("license_issued", "")), "issuer": str(form.get("license_issuer", "")),
                           "valid_until": str(form.get("license_until", "")) or None} if number else None}
    try:
        await clients["sportsman"].put("/api/v1/organizations/" + name.replace("/", "%2F") + "/card", json=payload)
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(f"/organizations/card?name={name}", "error", humanize(exc.detail))
    return back(f"/organizations/card?name={name}", "ok", "Карточка организации сохранена")


@router.post("/organizations/groups")
async def create_group(request: Request):
    user = await require(request, Role.ADMIN, Role.TRAINER)
    form = await request.form()
    org = str(form.get("org", ""))
    trainer = str(form.get("trainer_fsp_id", "")) if user["role"] == Role.ADMIN else user["person"]["fsp_id"]
    try:
        g = await clients["sportsman"].post("/api/v1/groups", json={
            "org": org, "name": str(form.get("name", "")), "stage": str(form.get("stage", "")), "discipline": str(form.get("discipline", "")),
            "trainer_fsp_id": trainer, "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(f"/organizations/card?name={org}", "error", humanize(exc.detail))
    return back(f"/groups/{g['id']}", "ok", f"Группа «{g['name']}» создана — зачислите спортсменов")


async def _group_access(user: dict, g: dict) -> None:
    role, me = user["role"], user["person"]["fsp_id"]
    if role in (Role.ADMIN, Role.MINISTRY, Role.TRAINER):
        return
    if role == Role.ATHLETE and any(m["fsp_id"] == me for m in g["members"]):
        return
    raise HTTPException(403, "Группа доступна её тренеру, участникам, федерации и министерству")


@router.get("/groups/{gid}", response_class=HTMLResponse)
async def group_page(request: Request, gid: int):
    user = await require(request, *VIEW, Role.ATHLETE)
    g = await clients["sportsman"].get(f"/api/v1/groups/{gid}")
    await _group_access(user, g)
    me = user["person"]["fsp_id"]
    can_run = user["role"] == Role.ADMIN or (user["role"] == Role.TRAINER and g["trainer_fsp_id"] == me)
    degraded: list[str] = []
    sessions, candidates, siblings = await asyncio.gather(
        safe(clients["sportsman"].get(f"/api/v1/groups/{gid}/sessions"), [], degraded),
        safe(clients["sportsman"].get("/api/v1/persons", role="athlete", organization=g["org"], discipline=g["discipline"]), [], degraded) if can_run else asyncio.sleep(0, []),
        safe(clients["sportsman"].get("/api/v1/groups", org=g["org"], discipline=g["discipline"]), [], degraded) if can_run else asyncio.sleep(0, []))
    active = [m for m in g["members"] if m["active"]]
    busy = {m["fsp_id"] for m in active}
    eligible = [p for p in candidates if p["fsp_id"] not in busy and p["training_stage"] in (None, g["stage"])]
    return render(request, "group.html", user=user, g=g, active=active, former=[m for m in g["members"] if not m["active"]], sessions=sessions,
                  eligible=eligible, targets=[s for s in siblings if s["id"] != gid], can_run=can_run, confirmations=CONFIRMATIONS,
                  degraded=sorted(set(degraded)))


async def _act(request: Request, gid: int, path: str, payload: dict, ok: str, notify_fid: str | None = None, notify_title: str = ""):
    user = await require(request, Role.ADMIN, Role.TRAINER)
    payload = {**payload, "actor": user["person"]["fsp_id"]}
    try:
        res = await clients["sportsman"].post(path, json=payload)
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(f"/groups/{gid}", "error", humanize(exc.detail))
    if notify_fid:
        await notify.notify(notify_fid, notify_title, "", f"/groups/{gid}")
    return res


@router.post("/groups/{gid}/members")
async def enroll(request: Request, gid: int):
    fid = str((await request.form()).get("fsp_id", ""))
    res = await _act(request, gid, f"/api/v1/groups/{gid}/members", {"fsp_id": fid}, "", fid, "Вас зачислили в группу подготовки")
    return res if not isinstance(res, dict) else back(f"/groups/{gid}", "ok", "Спортсмен зачислен в группу: этап подготовки обновлён")


@router.post("/groups/{gid}/members/{fid}/leave")
async def leave(request: Request, gid: int, fid: str):
    reason = str((await request.form()).get("reason", ""))
    res = await _act(request, gid, f"/api/v1/groups/{gid}/members/{fid}/leave", {"reason": reason}, "")
    return res if not isinstance(res, dict) else back(f"/groups/{gid}", "ok", "Спортсмен отчислен из группы")


@router.post("/groups/{gid}/transfer")
async def transfer(request: Request, gid: int):
    form = await request.form()
    fid, to = str(form.get("fsp_id", "")), int(form.get("to_group_id", 0) or 0)
    res = await _act(request, gid, f"/api/v1/groups/{gid}/transfer", {"fsp_id": fid, "to_group_id": to, "note": str(form.get("note", ""))}, "",
                     fid, "Вас перевели в другую группу подготовки")
    if not isinstance(res, dict):
        return res
    msg = "Спортсмен переведён в другую группу" + (f" и аттестован: {res['attestation']['from_stage']} → {res['attestation']['to_stage']}" if res["attestation"] else "")
    return back(f"/groups/{to}", "ok", msg, celebrate=bool(res["attestation"]))


@router.post("/groups/{gid}/sessions")
async def add_session(request: Request, gid: int):
    form = await request.form()
    payload = {"on": str(form.get("on", "")), "topic": str(form.get("topic", "")), "format": str(form.get("format", "")),
               "duration_min": int(form.get("duration_min", 0) or 0), "confirmation_kind": str(form.get("confirmation_kind", "")) or None,
               "confirmation_ref": str(form.get("confirmation_ref", "")), "attendees": form.getlist("attendees")}
    res = await _act(request, gid, f"/api/v1/groups/{gid}/sessions", payload, "")
    return res if not isinstance(res, dict) else back(f"/groups/{gid}#journal", "ok", f"Занятие занесено в журнал: {res['format_label'].lower()}, {len(res['attendees'])} чел.")

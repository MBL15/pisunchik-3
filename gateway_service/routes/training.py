"""Д4. Нормативы и аттестация по ФССП: обзор по ролям, профиль подготовки спортсмена, ввод результатов, перевод на этап, справочник нормативов."""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from gateway_service import notify
from gateway_service.web import Redirect, back, clients, humanize, render, require, safe
from shared import domain
from shared.domain import Role
from shared.http import ServiceError
from shared.training import STAGE_ORDER

router = APIRouter()
ALL_ROLES = (Role.ADMIN, Role.TRAINER, Role.ATHLETE, Role.MINISTRY)


async def _can_view(user: dict, fid: str) -> None:
    role, me = user["role"], user["person"]["fsp_id"]
    if role in (Role.ADMIN, Role.MINISTRY) or (role == Role.ATHLETE and fid == me):
        return
    if role == Role.TRAINER:
        links = await clients["sportsman"].get(f"/api/v1/athletes/{fid}/links")
        if any(lk["trainer_fsp_id"] == me and lk["status"] == "ACTIVE" for lk in links):
            return
    raise HTTPException(403, "Профиль подготовки доступен спортсмену, его тренеру и федерации")


@router.get("/training", response_class=HTMLResponse)
async def training_page(request: Request, stage: str = "", ready: str = "", q: str = ""):
    user = await require(request, *ALL_ROLES)
    role, me, degraded = user["role"], user["person"]["fsp_id"], []
    if role == Role.ATHLETE:
        raise Redirect(f"/training/athletes/{me}")
    params = {"stage": stage or None, "ready": True if ready == "1" else None, "q": q or None, "limit": 200}
    if role == Role.TRAINER:
        params["trainer_fsp_id"] = me
    data = await safe(clients["sportsman"].get("/api/v1/training/overview", **params), {"total": 0, "ready": 0, "items": []}, degraded)
    return render(request, "training.html", user=user, data=data, stages=domain.TRAINING_STAGES, filters={"stage": stage, "ready": ready, "q": q},
                  degraded=degraded)


@router.get("/training/athletes/{fid}", response_class=HTMLResponse)
async def athlete_training(request: Request, fid: str):
    user = await require(request, *ALL_ROLES)
    await _can_view(user, fid)
    degraded: list[str] = []
    info, person, norms = await asyncio.gather(
        clients["sportsman"].get(f"/api/v1/athletes/{fid}/training"), clients["sportsman"].get(f"/api/v1/persons/{fid}"),
        safe(clients["sportsman"].get("/api/v1/norms"), [], degraded))
    can_edit = user["role"] in (Role.ADMIN, Role.TRAINER)
    entry_norms = [r["norm"] for r in info["norms"]]
    return render(request, "training_athlete.html", user=user, t=info, person=person, can_edit=can_edit, entry_norms=entry_norms,
                  stages=domain.TRAINING_STAGES, degraded=degraded, all_norms=norms)


@router.post("/training/athletes/{fid}/results")
async def add_result(request: Request, fid: str):
    user = await require(request, Role.ADMIN, Role.TRAINER)
    form = await request.form()
    try:
        r = await clients["sportsman"].post("/api/v1/norm-results", json={
            "norm_id": int(form.get("norm_id", 0) or 0), "athlete_fsp_id": fid, "value": float(str(form.get("value", "")).replace(",", ".")),
            "performed_on": str(form.get("performed_on", "")) or None, "actor": user["person"]["fsp_id"]})
    except (ServiceError, ValueError) as exc:
        if isinstance(exc, ServiceError) and exc.unavailable:
            raise
        return back(f"/training/athletes/{fid}", "error", humanize(exc.detail) if isinstance(exc, ServiceError) else "Введите число")
    verdict = "норматив выполнен" if r["passed"] else "норматив не выполнен"
    await notify.notify(fid, f"Норматив: {r['norm']['title']}", f"{r['value']:g} {r['unit']} — {verdict}", "/training")
    msg = f"{r['norm']['title']}: {r['value']:g} {r['unit']} — {verdict}" + (". Спортсмен готов к переводу на следующий этап!" if r["ready"] else "")
    return back(f"/training/athletes/{fid}", "ok" if r["passed"] else "error", msg, celebrate=bool(r["ready"]))


@router.post("/training/results/{rid}/delete")
async def delete_result(request: Request, rid: int):
    user = await require(request, Role.ADMIN, Role.TRAINER)
    form = await request.form()
    fid = str(form.get("athlete", ""))
    try:
        await clients["sportsman"].request("DELETE", f"/api/v1/norm-results/{rid}", params={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(f"/training/athletes/{fid}", "error", humanize(exc.detail))
    return back(f"/training/athletes/{fid}", "ok", "Результат удалён")


@router.post("/training/athletes/{fid}/attest")
async def attest(request: Request, fid: str):
    user = await require(request, Role.ADMIN, Role.TRAINER)
    form = await request.form()
    try:
        a = await clients["sportsman"].post(f"/api/v1/athletes/{fid}/attestation", json={"actor": user["person"]["fsp_id"], "note": str(form.get("note", ""))})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(f"/training/athletes/{fid}", "error", humanize(exc.detail))
    await notify.notify(fid, "Перевод на следующий этап подготовки", f"{a['from_label']} → {a['to_label']}", "/training")
    return back(f"/training/athletes/{fid}", "ok", f"Оформлен перевод: {a['from_stage']} → {a['to_stage']}", celebrate=True)


@router.post("/training/athletes/{fid}/stage")
async def set_stage(request: Request, fid: str):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        await clients["sportsman"].post(f"/api/v1/athletes/{fid}/stage", json={"stage": str(form.get("stage", "")), "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(f"/training/athletes/{fid}", "error", humanize(exc.detail))
    return back(f"/training/athletes/{fid}", "ok", "Этап подготовки назначен")


# ---------------------------------------------------------------- справочник нормативов
@router.get("/training/norms", response_class=HTMLResponse)
async def norms_page(request: Request, stage: str = "", discipline: str = ""):
    user = await require(request, *ALL_ROLES)
    degraded: list[str] = []
    norms = await safe(clients["sportsman"].get("/api/v1/norms", stage=stage or None, discipline=discipline or None, include_inactive=True), [], degraded)
    return render(request, "training_norms.html", user=user, norms=norms, stages=domain.TRAINING_STAGES, stage_order=STAGE_ORDER,
                  disciplines=domain.DISCIPLINES, filters={"stage": stage, "discipline": discipline}, can_edit=user["role"] == Role.ADMIN, degraded=degraded)


def _norm_payload(form, actor: str) -> dict:
    return {"title": str(form.get("title", "")), "kind": str(form.get("kind", "SPECIAL")), "stage": str(form.get("stage", "")),
            "discipline": str(form.get("discipline", "ALL")), "gender": str(form.get("gender", "ALL")), "unit": str(form.get("unit", "")),
            "direction": str(form.get("direction", "MIN")), "threshold": float(str(form.get("threshold", "")).replace(",", ".")),
            "required": form.get("required") == "1", "active": form.get("active", "1") == "1", "note": str(form.get("note", "")), "actor": actor}


@router.post("/training/norms")
async def save_norm(request: Request):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    nid = str(form.get("id", ""))
    try:
        payload = _norm_payload(form, user["person"]["fsp_id"])
        if nid.isdigit():
            await clients["sportsman"].put(f"/api/v1/norms/{nid}", json=payload)
        else:
            await clients["sportsman"].post("/api/v1/norms", json=payload)
    except (ServiceError, ValueError) as exc:
        if isinstance(exc, ServiceError) and exc.unavailable:
            raise
        return back("/training/norms", "error", humanize(exc.detail) if isinstance(exc, ServiceError) else "Порог — число")
    return back(f"/training/norms?stage={payload['stage']}", "ok", "Норматив сохранён: новые результаты оцениваются по этому порогу (прежние попытки сохраняют порог своего времени)")

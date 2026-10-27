"""Д9 кабинет министерства спорта (только чтение) и Д6 показатели развития вида спорта в регионе.

Показатели собираются из трёх источников платформы — реестр (численность, организации), календарь и протоколы (соревнования) —
и к каждому можно приложить подтверждающий документ (файл или ссылку).
"""
import asyncio
import base64
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from gateway_service.web import back, clients, humanize, render, require, safe
from shared import config
from shared.domain import Role
from shared.http import ServiceError

router = APIRouter()

SOURCES = {
    "headcount": "Реестр занимающихся (sportsman_service) · отчёт о численности",
    "organizations": "Реестр организаций (sportsman_service) · состав по ролям и дисциплинам",
    "competitions": "Календарь и протоколы (competition_service) · календарные планы ЕКП / области",
}
MAX_UPLOAD = 2 * 1024 * 1024


async def cabinet_ministry(request: Request, user: dict):
    degraded: list[str] = []
    sp, co = clients["sportsman"], clients["competition"]
    stats, dash, upcoming, teams, orgs = await asyncio.gather(
        safe(sp.get("/api/v1/stats"), None, degraded), safe(co.get("/api/v1/dashboard"), None, degraded),
        safe(co.get("/api/v1/competitions", upcoming=True), [], degraded), safe(co.get("/api/v1/teams"), [], degraded),
        safe(sp.get("/api/v1/organizations"), [], degraded))
    return render(request, "cabinet_ministry.html", user=user, stats=stats, dash=dash, upcoming=sorted(
        upcoming, key=lambda c: c["start_date"])[:8], teams=teams, orgs=[o for o in orgs if o["athletes"]], degraded=sorted(set(degraded)))


@router.get("/development", response_class=HTMLResponse)
async def development_page(request: Request, year: int | None = None):
    user = await require(request, Role.ADMIN, Role.MINISTRY)
    degraded: list[str] = []
    year = year or config.today().year
    headcount, orgs, comps, files = await asyncio.gather(
        safe(clients["sportsman"].get("/api/v1/reports/headcount", years=3), None, degraded),
        safe(clients["sportsman"].get("/api/v1/organizations"), [], degraded),
        safe(clients["competition"].get("/api/v1/development/competitions", year=year), None, degraded),
        safe(clients["competition"].get("/api/v1/attachments", year=year), [], degraded))
    attachments = {k: [a for a in files if a["indicator"] == k] for k in SOURCES}
    return render(request, "development.html", user=user, year=year, headcount=headcount, orgs=[o for o in orgs if o["athletes"] or o["trainers"]],
                  comps=comps, attachments=attachments, sources=SOURCES, can_edit=user["role"] == Role.ADMIN,
                  years=(comps or {}).get("years") or [year], degraded=degraded)


@router.post("/development/attachments")
async def add_attachment(request: Request):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    year = int(form.get("year", 0) or 0)
    upload = form.get("file")
    payload = {"indicator": str(form.get("indicator", "")), "year": year, "title": str(form.get("title", "")),
               "url": str(form.get("url", "")).strip() or None, "actor": user["person"]["fsp_id"]}
    if upload is not None and getattr(upload, "filename", ""):
        data = await upload.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            return back(f"/development?year={year}", "error", f"Файл больше {MAX_UPLOAD // 1024 // 1024} МБ")
        payload.update(filename=upload.filename, content_b64=base64.b64encode(data).decode())
    try:
        await clients["competition"].post("/api/v1/attachments", json=payload)
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(f"/development?year={year}", "error", humanize(exc.detail))
    return back(f"/development?year={year}", "ok", "Документ приложен к показателю")


@router.get("/development/attachments/{aid}")
async def download_attachment(request: Request, aid: int):
    await require(request, Role.ADMIN, Role.MINISTRY)
    f = await clients["competition"].get(f"/api/v1/attachments/{aid}/file")
    safe_name = f["filename"].replace('"', "").replace("\r", "").replace("\n", "")
    return Response(base64.b64decode(f["content_b64"]), media_type=f["media_type"],
                    headers={"Content-Disposition": f'attachment; filename="{safe_name.encode("ascii", "ignore").decode() or "document"}"; '
                                                    f"filename*=UTF-8''{quote(safe_name)}",
                             "X-Content-Type-Options": "nosniff"})


@router.post("/development/attachments/{aid}/delete")
async def delete_attachment(request: Request, aid: int):
    user = await require(request, Role.ADMIN)
    year = (await request.form()).get("year", "")
    try:
        await clients["competition"].request("DELETE", f"/api/v1/attachments/{aid}", params={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        raise HTTPException(exc.status if exc.status < 500 else 503, humanize(exc.detail)) from exc
    return back(f"/development?year={year}", "ok", "Документ удалён")

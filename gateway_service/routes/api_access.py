"""«API и MCP»: личные токены роли для моделей и скриптов (`/api/me/v1`, MCP-сервер `mcp_fsp`).

Токен выпускается в активной роли сессии и показывается один раз — прямо в ответе, без редиректа и cookie; дальше хранится только хэш.
Выпустить токен можно только из браузерной сессии: по самому токену новый получить нельзя.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gateway_service import personal_api
from gateway_service.web import back, clients, humanize, render, require, safe
from shared import config
from shared.http import ServiceError

router = APIRouter()

CAPABILITY_LABELS = {
    "calendar": "календарь соревнований", "protocols": "опубликованные протоколы", "rating": "рейтинг сезона по дисциплинам",
    "my_results": "мои результаты и история выступлений", "my_applications": "мои заявки",
    "submit_application": "подача заявки на соревнование",
    "my_athletes": "подопечные и их результаты", "applications_submitted": "заявки, поданные тренером",
    "my_assignments": "мои судейские назначения",
    "headcount_report": "отчёт о численности (динамика и разрезы)", "development": "показатели развития вида спорта",
    "dashboard": "ключевые показатели сезона и реестра", "teams": "сборные региона", "awards": "претенденты на премии Губернатора",
    "applications_inbox": "заявки, ждущие решения", "decide_application": "одобрение и отказ по заявкам",
    "enter_results": "ввод результатов и черновик протокола", "publish_protocol": "публикация протокола",
}
WRITE_CAPABILITIES = {"submit_application", "decide_application", "enter_results", "publish_protocol"}


async def _page(request: Request, user: dict, new_token: dict | None = None) -> HTMLResponse:
    degraded: list[str] = []
    tokens = await safe(clients["sportsman"].get("/api/v1/user-tokens", owner=user["person"]["fsp_id"]), [], degraded)
    role = user["role"]
    can = [{"label": CAPABILITY_LABELS[c], "write": c in WRITE_CAPABILITIES} for c in personal_api.CAPABILITIES[role]]
    return render(request, "api_access.html", user=user, tokens=[t for t in tokens if t["active"]], new_token=new_token, can=can,
                  api_base=f"{config.PUBLIC_BASE_URL}/api/me/v1", degraded=degraded)


@router.get("/api-access", response_class=HTMLResponse)
async def api_access(request: Request):
    return await _page(request, await require(request))


@router.post("/api-access/tokens", response_class=HTMLResponse)
async def create_token(request: Request):
    user = await require(request)
    form = await request.form()
    try:
        token = await clients["sportsman"].post("/api/v1/user-tokens", json={
            "actor": user["person"]["fsp_id"], "role": user["role"], "name": str(form.get("name", ""))})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/api-access", "error", humanize(exc.detail))
    resp = await _page(request, user, new_token=token)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/api-access/tokens/{tid}/revoke")
async def revoke_token(request: Request, tid: int):
    user = await require(request)
    try:
        await clients["sportsman"].post(f"/api/v1/user-tokens/{tid}/revoke", json={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/api-access", "error", humanize(exc.detail))
    return back("/api-access", "ok", "Токен отозван — доступ по нему закрыт")


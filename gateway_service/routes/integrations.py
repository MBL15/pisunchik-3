"""Д2. Интеграции: токены внешних систем с ограничением прав, вебхуки, журнал доставок и демо-приёмник событий."""
import asyncio
from collections import deque
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from gateway_service.web import back, clients, humanize, render, require, safe
from shared import config
from shared.domain import Role
from shared.http import ServiceError

router = APIRouter()
RECEIVED_LIMIT, MAX_BODY = 20, 16 * 1024
_received: deque[dict] = deque(maxlen=RECEIVED_LIMIT)  # что принял демо-приёмник: события платформы, пришедшие вебхуком


async def _page(request: Request, user: dict, created: dict | None = None, hook_secret: dict | None = None) -> HTMLResponse:
    degraded: list[str] = []
    co = clients["competition"]
    meta, systems, hooks, deliveries = await asyncio.gather(
        safe(co.get("/api/v1/integrations/meta"), {"scopes": {}, "events": {}}, degraded),
        safe(co.get("/api/v1/integrations/clients"), [], degraded), safe(co.get("/api/v1/integrations/webhooks"), [], degraded),
        safe(co.get("/api/v1/integrations/deliveries", limit=15), [], degraded))
    by_client: dict[int, list[dict]] = {}
    for h in hooks:
        by_client.setdefault(h["client_id"], []).append(h)
    resp = render(request, "integrations.html", user=user, meta=meta, systems=systems, by_client=by_client, deliveries=deliveries,
                  received=list(reversed(_received)), created=created, hook_secret=hook_secret, base=config.PUBLIC_BASE_URL.rstrip("/"),
                  receiver_url=f"{config.GATEWAY_URL.rstrip('/')}/integrations/demo-receiver", degraded=degraded)
    if created or hook_secret:
        resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/integrations", response_class=HTMLResponse)
async def integrations_page(request: Request):
    return await _page(request, await require(request, Role.ADMIN))


@router.post("/integrations/clients", response_class=HTMLResponse)
async def create_client(request: Request):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        created = await clients["competition"].post("/api/v1/integrations/clients", json={
            "name": str(form.get("name", "")), "scopes": form.getlist("scope"), "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/integrations", "error", humanize(exc.detail))
    return await _page(request, user, created=created)


@router.post("/integrations/clients/{cid}/revoke")
async def revoke_client(request: Request, cid: int):
    user = await require(request, Role.ADMIN)
    try:
        await clients["competition"].request("POST", f"/api/v1/integrations/clients/{cid}/revoke", params={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/integrations", "error", humanize(exc.detail))
    return back("/integrations", "ok", "Токен отозван, вебхуки системы отключены")


@router.post("/integrations/webhooks", response_class=HTMLResponse)
async def create_hook(request: Request):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        hook = await clients["competition"].post("/api/v1/integrations/webhooks", json={
            "client_id": int(form.get("client_id", 0) or 0), "url": str(form.get("url", "")), "events": form.getlist("event"),
            "actor": user["person"]["fsp_id"]})
    except (ServiceError, ValueError) as exc:
        if isinstance(exc, ServiceError) and exc.unavailable:
            raise
        return back("/integrations", "error", humanize(exc.detail) if isinstance(exc, ServiceError) else "Выберите систему")
    return await _page(request, user, hook_secret=hook)


@router.post("/integrations/webhooks/{hid}/delete")
async def delete_hook(request: Request, hid: int):
    user = await require(request, Role.ADMIN)
    try:
        await clients["competition"].request("DELETE", f"/api/v1/integrations/webhooks/{hid}", params={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/integrations", "error", humanize(exc.detail))
    return back("/integrations", "ok", "Вебхук удалён")


@router.post("/integrations/webhooks/{hid}/test")
async def test_hook(request: Request, hid: int):
    user = await require(request, Role.ADMIN)
    try:
        d = await clients["competition"].request("POST", f"/api/v1/integrations/webhooks/{hid}/test", params={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/integrations", "error", humanize(exc.detail))
    return back("/integrations#deliveries", "ok" if d["ok"] else "error",
                f"Тестовое событие доставлено: ответ {d['status_code']}" if d["ok"] else f"Доставка не удалась: {d['error']}")


@router.post("/integrations/demo-receiver")
async def demo_receiver(request: Request):
    """Приёмник-пример: принимает вебхуки платформы и показывает их на странице интеграций. Тело ограничено, внешних данных не исполняет."""
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "Слишком большое тело запроса")
    _received.append({"at": datetime.now().isoformat(timespec="seconds"), "event": request.headers.get("x-fsp-event", ""),
                      "delivery": request.headers.get("x-fsp-delivery", ""), "signature": request.headers.get("x-fsp-signature", ""),
                      "body": body.decode("utf-8", "replace")})
    return JSONResponse({"received": True})

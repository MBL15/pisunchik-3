"""Д2. Интеграции с внешними системами: токены доступа с ограничением прав (области), вебхуки о событиях платформы и импорт результатов.

Токен `fspi_…` выдаёт федерация (показывается один раз, хранится SHA-256) и ограничивает областями: чтение календаря, чтение опубликованных
протоколов и рейтинга, импорт результатов. Вебхуки: `competition.published`, `application.approved`, `protocol.published` — POST с JSON и подписью
`X-FSP-Signature: sha256=HMAC(secret, тело)`; каждая доставка попадает в журнал. Импорт результатов создаёт только черновик протокола:
публикует его федерация. Публичный интерфейс для внешних систем — `gateway_service/integrations_api.py`.
"""
import asyncio
import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from competition_service.models import ResultIn
from competition_service.store import store

router = APIRouter(prefix="/api/v1/integrations")
CLIENTS, HOOKS, DELIVERIES = "ext_clients", "ext_webhooks", "ext_deliveries"
TOKEN_PREFIX = "fspi_"
SCOPES = {"competitions:read": "Чтение календаря соревнований", "protocols:read": "Чтение опубликованных протоколов и рейтинга",
          "results:write": "Импорт результатов соревнования (черновик протокола)"}
EVENTS = {"competition.published": "Опубликовано соревнование (внесено в календарь)", "application.approved": "Одобрена заявка на соревнование",
          "protocol.published": "Опубликован протокол соревнования"}
DELIVERY_LIMIT = 200
LAST_USED_GRANULARITY = timedelta(minutes=1)
TIMEOUT = 5.0

# Подмена транспорта доставки в тестах (httpx.MockTransport); в работе — обычная сеть.
_transport: httpx.AsyncBaseTransport | None = None
_tasks: set[asyncio.Task] = set()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class ClientIn(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    scopes: list[str] = Field(min_length=1)
    actor: str

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        if set(v) - set(SCOPES):
            raise ValueError("Допустимые области: " + ", ".join(SCOPES))
        return list(dict.fromkeys(v))


class HookIn(BaseModel):
    client_id: int
    url: str = Field(max_length=300)
    events: list[str] = Field(min_length=1)
    actor: str

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        u = urlsplit(v.strip())
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ValueError("Адрес вебхука — http:// или https://")
        if u.hostname.startswith("169.254.") or u.hostname.endswith(".internal"):
            raise ValueError("Адреса служебных сетей (метаданные облака) не допускаются")
        return v.strip()

    @field_validator("events")
    @classmethod
    def _events(cls, v: list[str]) -> list[str]:
        if set(v) - set(EVENTS):
            raise ValueError("Допустимые события: " + ", ".join(EVENTS))
        return list(dict.fromkeys(v))


class VerifyIn(BaseModel):
    token: str = Field(max_length=200)


class ImportIn(BaseModel):
    client_id: int
    system: str = Field(default="", max_length=80)
    results: list[ResultIn] = Field(min_length=1, max_length=500)


def _admin(fid: str) -> None:
    """Токены и вебхуки ведёт федерация; роль уточняет реестр через шлюз, здесь — защита от пустого `actor`."""
    if not fid.strip():
        raise HTTPException(403, "Не указан исполнитель")


def present_client(c: dict) -> dict:
    hooks = [h for h in store.ext.table(HOOKS).values() if h["client_id"] == c["id"]]
    return {k: v for k, v in c.items() if k != "digest"} | {"hooks": len(hooks), "scope_labels": [SCOPES[s] for s in c["scopes"]]}


def present_hook(h: dict) -> dict:
    return {k: v for k, v in h.items() if k != "secret"} | {"event_labels": [EVENTS[e] for e in h["events"]]}


@router.get("/meta")
async def meta():
    return {"scopes": SCOPES, "events": EVENTS}


@router.get("/clients")
async def list_clients():
    return [present_client(c) for c in sorted(store.ext.table(CLIENTS).values(), key=lambda c: c["id"], reverse=True)]


@router.post("/clients", status_code=201)
async def create_client(data: ClientIn):
    _admin(data.actor)
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    item = store.ext.add(CLIENTS, {"name": data.name.strip(), "scopes": data.scopes, "digest": _digest(token), "prefix": token[:len(TOKEN_PREFIX) + 4],
                                   "active": True, "created_by": data.actor, "created_at": datetime.now().isoformat(timespec="seconds"),
                                   "last_used_at": None})
    store.log(data.actor, "Выдан токен внешней системе", f"{item['name']}: {', '.join(item['scopes'])}")
    return present_client(item) | {"token": token}


@router.post("/clients/{cid}/revoke")
async def revoke_client(cid: int, actor: str = Query(...)):
    c = store.ext.table(CLIENTS).get(cid)
    if not c:
        raise HTTPException(404, "Система не найдена")
    c["active"] = False
    for h in store.ext.table(HOOKS).values():
        if h["client_id"] == cid:
            h["active"] = False
    store.log(actor, "Отозван токен внешней системы", c["name"])
    return present_client(c)


@router.post("/verify")
async def verify(data: VerifyIn):
    """Шлюз узнаёт систему по токену; неизвестный и отозванный токены неотличимы (`401`)."""
    digest = _digest(data.token)
    c = next((c for c in store.ext.table(CLIENTS).values() if hmac.compare_digest(c["digest"], digest)), None)
    if not c or not c["active"]:
        raise HTTPException(401, "Токен недействителен или отозван")
    now = datetime.now()
    if not c["last_used_at"] or now - datetime.fromisoformat(c["last_used_at"]) > LAST_USED_GRANULARITY:
        c["last_used_at"] = now.isoformat(timespec="seconds")
    return {"client_id": c["id"], "name": c["name"], "scopes": c["scopes"]}


@router.get("/webhooks")
async def list_hooks(client_id: int | None = None):
    return [present_hook(h) for h in sorted(store.ext.table(HOOKS).values(), key=lambda h: h["id"])
            if client_id is None or h["client_id"] == client_id]


@router.post("/webhooks", status_code=201)
async def create_hook(data: HookIn):
    _admin(data.actor)
    c = store.ext.table(CLIENTS).get(data.client_id)
    if not c or not c["active"]:
        raise HTTPException(404, "Система не найдена или отключена")
    secret = secrets.token_hex(24)
    item = store.ext.add(HOOKS, {"client_id": c["id"], "url": data.url, "events": data.events, "secret": secret, "active": True,
                                 "created_at": datetime.now().isoformat(timespec="seconds")})
    store.log(data.actor, "Добавлен вебхук", f"{c['name']}: {data.url} ({', '.join(data.events)})")
    return present_hook(item) | {"secret": secret}


@router.delete("/webhooks/{hid}")
async def delete_hook(hid: int, actor: str = Query(...)):
    h = store.ext.table(HOOKS).pop(hid, None)
    if not h:
        raise HTTPException(404, "Вебхук не найден")
    store.log(actor, "Удалён вебхук", h["url"])
    return {"deleted": hid}


@router.post("/webhooks/{hid}/test")
async def test_hook(hid: int, actor: str = Query(...)):
    h = store.ext.table(HOOKS).get(hid)
    if not h or not h["active"]:
        raise HTTPException(404, "Вебхук не найден или отключён")
    delivery = await deliver(h, "ping", {"message": "Тестовое событие ФСП Оренбург", "webhook_id": hid})
    store.log(actor, "Проверка вебхука", f"{h['url']} → {delivery['status_code'] or delivery['error']}")
    return delivery


@router.get("/deliveries")
async def list_deliveries(limit: int = Query(50, ge=1, le=DELIVERY_LIMIT), client_id: int | None = None):
    hooks = {h["id"]: h for h in store.ext.table(HOOKS).values()}
    rows = [d for d in store.ext.table(DELIVERIES).values() if client_id is None or hooks.get(d["webhook_id"], {}).get("client_id") == client_id]
    return sorted(rows, key=lambda d: d["id"], reverse=True)[:limit]


def sign_body(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def deliver(hook: dict, event: str, data: dict) -> dict:
    """Одна доставка: POST с подписью, ответ или ошибка пишутся в журнал (последние `DELIVERY_LIMIT`)."""
    delivery_id = str(uuid.uuid4())
    body = json.dumps({"id": delivery_id, "event": event, "occurred_at": datetime.now().isoformat(timespec="seconds"), "data": data},
                      ensure_ascii=False).encode()
    status, error = None, ""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=_transport) as http:
            resp = await http.post(hook["url"], content=body, headers={
                "Content-Type": "application/json", "X-FSP-Event": event, "X-FSP-Delivery": delivery_id,
                "X-FSP-Signature": sign_body(hook["secret"], body)})
        status = resp.status_code
        if status >= 300:
            error = f"ответ {status}"
    except httpx.HTTPError as exc:
        error = exc.__class__.__name__
    table = store.ext.table(DELIVERIES)
    item = store.ext.add(DELIVERIES, {"webhook_id": hook["id"], "url": hook["url"], "event": event, "delivery_id": delivery_id,
                                      "at": datetime.now().isoformat(timespec="seconds"), "status_code": status, "ok": bool(status and status < 300),
                                      "error": error})
    for old in sorted(table)[:-DELIVERY_LIMIT]:
        del table[old]
    return item


def emit(event: str, data: dict) -> None:
    """Событие платформы → все активные вебхуки, подписанные на него. Доставка идёт в фоне и не задерживает ответ пользователю."""
    hooks = [h for h in store.ext.table(HOOKS).values() if h["active"] and event in h["events"]]
    if not hooks:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for h in hooks:
        task = loop.create_task(deliver(h, event, data))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


def competition_payload(p: dict) -> dict:
    return {k: p[k] for k in ("id", "code", "title", "discipline", "level", "start_date", "end_date", "venue", "status", "plan_number",
                              "registration_deadline")} | {"calendar_plan": p["calendar_plan"]}

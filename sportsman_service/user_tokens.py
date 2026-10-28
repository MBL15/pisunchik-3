"""Личные токены роли для API и MCP (`fspu_…`): человек выпускает токен в кабинете, а API работает от его имени в выбранной роли.

Токен привязан к ФСП ID и к одной роли из тех, что у человека есть. В базе лежит только SHA-256 токена; открытое значение
показывается один раз при выпуске. Роль проверяется при каждом обращении: если её у человека отобрали — токен перестаёт работать.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from shared.domain import ROLE_LABELS
from sportsman_service.store import store

router = APIRouter(prefix="/api/v1/user-tokens")

TABLE = "user_tokens"
TOKEN_PREFIX = "fspu_"
MAX_ACTIVE_TOKENS = 5
LAST_USED_GRANULARITY = timedelta(minutes=1)  # «последнее использование» пишется не чаще — иначе каждый запрос меняет БД


class TokenIn(BaseModel):
    actor: str
    role: str
    name: str = Field(min_length=2, max_length=60)


class RevokeIn(BaseModel):
    actor: str


class VerifyIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def present(t: dict) -> dict:
    out = {k: v for k, v in t.items() if k != "digest"}
    return out | {"role_label": ROLE_LABELS.get(t["role"], t["role"]), "active": t["revoked_at"] is None}


@router.get("")
async def list_tokens(owner: str = Query(...)):
    rows = sorted((t for t in store.ext.table(TABLE).values() if t["owner"] == owner), key=lambda t: t["created_at"], reverse=True)
    return [present(t) for t in rows]


@router.post("", status_code=201)
async def create_token(body: TokenIn):
    """Выпускает токен в одной из ролей владельца. Открытое значение есть только в этом ответе."""
    person = store.persons.get(body.actor)
    if not person:
        raise HTTPException(404, "Участник не найден")
    if body.role not in person.roles:
        raise HTTPException(403, "Эта роль не назначена вашему аккаунту")
    if sum(t["owner"] == body.actor and t["revoked_at"] is None for t in store.ext.table(TABLE).values()) >= MAX_ACTIVE_TOKENS:
        raise HTTPException(409, f"Не больше {MAX_ACTIVE_TOKENS} активных токенов — отзовите ненужный")
    secret = TOKEN_PREFIX + secrets.token_urlsafe(32)
    item = store.ext.add(TABLE, {"owner": body.actor, "role": body.role, "name": body.name.strip(), "prefix": secret[:len(TOKEN_PREFIX) + 4],
                                 "digest": _digest(secret), "created_at": datetime.now().isoformat(timespec="seconds"),
                                 "last_used_at": None, "revoked_at": None})
    store.log(body.actor, "Выпущен токен API", f"токен #{item['id']} ({ROLE_LABELS[body.role]}): {item['name']}")
    return present(item) | {"token": secret}


@router.post("/{tid}/revoke")
async def revoke_token(tid: int, body: RevokeIn):
    t = store.ext.table(TABLE).get(tid)
    if not t:
        raise HTTPException(404, "Токен не найден")
    if t["owner"] != body.actor:
        raise HTTPException(403, "Токен принадлежит другому участнику")
    if t["revoked_at"] is None:
        t["revoked_at"] = datetime.now().isoformat(timespec="seconds")
        store.log(body.actor, "Отозван токен API", f"токен #{t['id']}: {t['name']}")
    return present(t)


@router.post("/verify")
async def verify_token(body: VerifyIn):
    """Шлюз узнаёт человека и роль по токену. Неизвестный, отозванный токен и токен с отобранной ролью неотличимы (`401`)."""
    digest = _digest(body.token)
    found = next((t for t in store.ext.table(TABLE).values() if hmac.compare_digest(t["digest"], digest)), None)
    person = store.persons.get(found["owner"]) if found else None
    if not found or found["revoked_at"] is not None or not person or found["role"] not in person.roles:
        raise HTTPException(401, "Токен недействителен или отозван")
    now = datetime.now()
    if found["last_used_at"] is None or now - datetime.fromisoformat(found["last_used_at"]) > LAST_USED_GRANULARITY:
        found["last_used_at"] = now.isoformat(timespec="seconds")
    return {"fsp_id": found["owner"], "role": found["role"], "token_id": found["id"], "name": found["name"]}

"""Д1. Региональный ФСП ID как провайдер OpenID Connect: реестр клиентских приложений (регистрирует администратор).

Здесь только хранение клиентов и проверка их секретов; поток authorization code + PKCE, страница согласия, discovery и userinfo —
в шлюзе (`gateway_service/routes/oidc.py`), потому что он владеет сессией. Секрет клиента хранится как SHA-256 и показывается один раз.
"""
import hashlib
import hmac
import secrets
from datetime import datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from shared import config
from shared.domain import Role
from sportsman_service.store import store

router = APIRouter(prefix="/api/v1/oidc")
CLIENTS = "oidc_clients"
SCOPES = {"openid": "Идентификатор ФСП ID", "profile": "ФИО", "roles": "Роли в платформе", "disciplines": "Дисциплины",
          "rank": "Спортивный разряд и срок его действия"}
DEMO_CLIENT_ID, DEMO_CLIENT_SECRET = "fsp-demo-client", "fsp-demo-secret"  # демонстрационный клиент: секрет открыт намеренно


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class ClientIn(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    redirect_uris: list[str] = Field(min_length=1, max_length=5)
    public: bool = False  # публичный клиент без секрета (SPA, мобильное): защищён только PKCE
    scopes: list[str] = list(SCOPES)
    actor: str

    @field_validator("redirect_uris")
    @classmethod
    def _uris(cls, v: list[str]) -> list[str]:
        out = []
        for raw in v:
            u = urlsplit(raw.strip())
            local = u.hostname in ("localhost", "127.0.0.1", "::1")
            if u.scheme not in ("https", "http") or (u.scheme == "http" and not local) or not u.netloc or u.fragment:
                raise ValueError("Адрес возврата — https:// (для разработки допустим http://localhost) без фрагмента #")
            out.append(raw.strip())
        return list(dict.fromkeys(out))

    @field_validator("scopes")
    @classmethod
    def _scopes(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(SCOPES)
        if bad or "openid" not in v:
            raise ValueError("Допустимые области: " + ", ".join(SCOPES) + "; область openid обязательна")
        return list(dict.fromkeys(v))


class VerifyIn(BaseModel):
    client_id: str
    secret: str | None = None


def _public(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "secret_digest"} | {"public": c["secret_digest"] is None}


def _find(client_id: str) -> dict | None:
    return next((c for c in store.ext.table(CLIENTS).values() if c["client_id"] == client_id), None)


def seed_demo_client(s) -> None:
    s.ext.add(CLIENTS, {"client_id": DEMO_CLIENT_ID, "name": "Демо-приложение «Партнёр»", "secret_digest": _digest(DEMO_CLIENT_SECRET),
                        "redirect_uris": [f"{config.PUBLIC_BASE_URL}/oidc/demo/callback"], "scopes": list(SCOPES), "active": True,
                        "created_by": "system", "created_at": datetime.now().isoformat(timespec="seconds")})


@router.get("/clients")
async def list_clients():
    return [_public(c) for c in sorted(store.ext.table(CLIENTS).values(), key=lambda c: c["id"])]


@router.post("/clients", status_code=201)
async def register_client(data: ClientIn):
    admin = store.persons.get(data.actor)
    if not admin or Role.ADMIN not in admin.roles:
        raise HTTPException(403, "Клиентские приложения регистрирует федерация")
    secret = None if data.public else "fspcs_" + secrets.token_urlsafe(32)
    item = store.ext.add(CLIENTS, {"client_id": "fsp-" + secrets.token_hex(6), "name": data.name.strip(),
                                   "secret_digest": _digest(secret) if secret else None, "redirect_uris": data.redirect_uris,
                                   "scopes": data.scopes, "active": True, "created_by": data.actor,
                                   "created_at": datetime.now().isoformat(timespec="seconds")})
    store.log(data.actor, "Зарегистрировано приложение ФСП ID", f"{item['name']} ({item['client_id']})")
    return _public(item) | {"client_secret": secret}


@router.post("/clients/{cid}/revoke")
async def revoke_client(cid: int, actor: str):
    admin = store.persons.get(actor)
    if not admin or Role.ADMIN not in admin.roles:
        raise HTTPException(403, "Клиентские приложения ведёт федерация")
    c = store.ext.table(CLIENTS).get(cid)
    if not c:
        raise HTTPException(404, "Приложение не найдено")
    c["active"] = False
    store.log(actor, "Отключено приложение ФСП ID", f"{c['name']} ({c['client_id']})")
    return _public(c)


@router.get("/clients/by-id/{client_id}")
async def client_info(client_id: str):
    c = _find(client_id)
    if not c or not c["active"]:
        raise HTTPException(404, "Приложение не найдено или отключено")
    return _public(c)


@router.post("/clients/verify")
async def verify_client(data: VerifyIn):
    """Проверка клиента на token endpoint: конфиденциальный — по секрету, публичный — без секрета (только PKCE)."""
    c = _find(data.client_id)
    if not c or not c["active"]:
        raise HTTPException(401, "invalid_client")
    if c["secret_digest"] is not None and not (data.secret and hmac.compare_digest(c["secret_digest"], _digest(data.secret))):
        raise HTTPException(401, "invalid_client")
    return _public(c)

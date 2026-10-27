"""Д2. API для внешних систем: `/api/ext/v1`, документация — `/api/ext/v1/docs`.

Доступ — токен системы (`Authorization: Bearer fspi_…`), его выдаёт федерация и ограничивает областями:
`competitions:read`, `protocols:read`, `results:write`. Импорт результатов создаёт только черновик протокола — публикует федерация.
О событиях платформы система узнаёт по вебхукам (`competition.published`, `application.approved`, `protocol.published`).
"""
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from gateway_service.web import clients
from shared.http import ServiceError

api = FastAPI(
    title="ФСП Оренбург · API для внешних систем", version="1.0.0",
    description="Календарь, опубликованные протоколы, рейтинг и импорт результатов соревнований для соревновательных платформ, сайтов организаций "
                "и сервисов партнёров.\n\n**Вебхуки.** Федерация подписывает систему на события: `competition.published`, `application.approved`, "
                "`protocol.published`. Каждая доставка — `POST` с JSON `{id, event, occurred_at, data}` и заголовками `X-FSP-Event`, `X-FSP-Delivery`, "
                "`X-FSP-Signature: sha256=<HMAC-SHA256(secret, тело запроса)>` — проверяйте подпись секретом вебхука.\n\n"
                "**Пример.** `curl -H 'Authorization: Bearer fspi_…' /api/ext/v1/competitions?status=REGISTRATION_OPEN`")
bearer = HTTPBearer(auto_error=False, description="Токен внешней системы `fspi_…`, выдаёт федерация")


@api.exception_handler(ServiceError)
async def _service_error(_, exc: ServiceError):
    return JSONResponse({"detail": exc.detail}, status_code=502 if exc.unavailable else exc.status)


async def system(creds: HTTPAuthorizationCredentials | None = Security(bearer)) -> dict:
    denied = HTTPException(401, "Нужен действующий токен: заголовок Authorization: Bearer fspi_…", headers={"WWW-Authenticate": "Bearer"})
    if not creds:
        raise denied
    try:
        return await clients["competition"].post("/api/v1/integrations/verify", json={"token": creds.credentials})
    except ServiceError as exc:
        if exc.status == 401:
            raise denied from exc
        raise


def need(scope: str):
    async def check(who: dict = Depends(system)) -> dict:
        if scope not in who["scopes"]:
            raise HTTPException(403, f"У токена нет области {scope}")
        return who
    return check


class ResultRow(BaseModel):
    fsp_id: str = Field(description="ФСП ID участника; у него должна быть одобренная заявка на соревнование")
    solved: int = Field(default=0, ge=0, le=26, description="Решено задач (режим ICPC: дисциплины ALG и SEC)")
    penalty: int = Field(default=0, ge=0, description="Штрафное время, мин (ICPC)")
    score: float = Field(default=0, ge=0, description="Сумма баллов жюри (остальные дисциплины)")
    dsq: bool = False
    dsq_reason: str = ""


class ImportBody(BaseModel):
    system: str = Field(default="", max_length=80, description="Название системы-источника для журнала действий")
    results: list[ResultRow] = Field(min_length=1, max_length=500)


@api.get("/me", summary="Кто я: система и её области")
async def me(who: dict = Depends(system)):
    return who


@api.get("/competitions", summary="Календарь соревнований")
async def competitions(discipline: str | None = None, status: str | None = None, season: int | None = None, q: str | None = None,
                       who: dict = Depends(need("competitions:read"))):
    return await clients["competition"].get("/api/v1/competitions", discipline=discipline, status=status, season=season, q=q)


@api.get("/competitions/{cid}", summary="Соревнование")
async def competition(cid: int, who: dict = Depends(need("competitions:read"))):
    return await clients["competition"].get(f"/api/v1/competitions/{cid}")


@api.get("/competitions/{cid}/protocol", summary="Опубликованный протокол")
async def protocol(cid: int, who: dict = Depends(need("protocols:read"))):
    """Только опубликованный протокол; черновик и live-таблица внешним системам не отдаются."""
    p = await clients["competition"].get(f"/api/v1/competitions/{cid}/protocol")
    if not p["published"]:
        raise HTTPException(404, "Протокол не опубликован")
    return {k: p[k] for k in ("competition", "mode", "entries", "published_at", "tasks")}


@api.get("/rating", summary="Рейтинг сезона по дисциплине")
async def rating(discipline: Literal["ALG", "PRD", "SEC", "AI", "ROB", "UAV"] = "ALG", season: int | None = None,
                 who: dict = Depends(need("protocols:read"))):
    return await clients["competition"].get("/api/v1/rating", discipline=discipline, season=season)


@api.post("/competitions/{cid}/results", status_code=202, summary="Импорт результатов → черновик протокола")
async def import_results(cid: int, body: ImportBody, who: dict = Depends(need("results:write"))):
    """Протокол формируется из участников с одобренными заявками и ранжируется автоматически. Создаётся **черновик**: публикацию решает федерация."""
    draft = await clients["competition"].post(f"/api/v1/integrations/import/{cid}", json={
        "client_id": who["client_id"], "system": body.system, "results": [r.model_dump() for r in body.results]})
    return {"status": "draft", "participants": len(draft["entries"]), "warnings": draft["warnings"], "published": draft["published"]}

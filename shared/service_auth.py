"""Служебный токен между шлюзом и внутренними сервисами.

Сервисы доверяют шлюзу: роли проверяет он. Чтобы до сервиса нельзя было достучаться в обход шлюза, при заданном
`FSP_SERVICE_TOKEN` каждый сервис принимает только запросы с заголовком `X-FSP-Service-Token` (`/health` открыт для оркестратора).
Без токена проверка выключена — так работают локальный запуск и тесты; в compose порты сервисов наружу не публикуются.
"""
import hmac

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from shared import config

HEADER = "X-FSP-Service-Token"
OPEN_PATHS = {"/health"}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def service_token_guard(request: Request, call_next):
        token = config.SERVICE_TOKEN  # читается на каждый запрос — значение можно менять в тестах
        if token and request.url.path not in OPEN_PATHS \
                and not hmac.compare_digest(request.headers.get(HEADER, ""), token):
            return JSONResponse({"detail": "Служебный токен шлюза обязателен"}, status_code=401)
        return await call_next(request)

"""Thin async inter-service HTTP client on top of httpx with uniform error semantics."""
from typing import Any

import httpx

from shared import config
from shared.config import SERVICE_TIMEOUT


class ServiceError(Exception):
    def __init__(self, service: str, status: int, detail: Any):
        self.service, self.status, self.detail = service, status, detail
        super().__init__(f"[{service}] {status}: {detail}")

    @property
    def unavailable(self) -> bool:
        return self.status == 503


class ServiceClient:
    """One pooled AsyncClient per downstream service; open in lifespan, close on shutdown."""

    def __init__(self, name: str, base_url: str, caller: str):
        self.name = name
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=SERVICE_TIMEOUT,
            headers={"X-FSP-Caller": caller, **({"X-FSP-Service-Token": config.SERVICE_TOKEN} if config.SERVICE_TOKEN else {})},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(self, method: str, path: str, **kwargs) -> Any:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise ServiceError(self.name, 503, f"Сервис недоступен: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise ServiceError(self.name, resp.status_code, detail)
        return resp.json() if resp.content else None

    async def get(self, path: str, **params) -> Any:
        return await self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def post(self, path: str, json: Any = None) -> Any:
        return await self.request("POST", path, json=json)

    async def put(self, path: str, json: Any = None) -> Any:
        return await self.request("PUT", path, json=json)

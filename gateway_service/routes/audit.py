"""Журнал действий обоих сервисов: поиск, фильтры, страницы и выгрузка."""
import asyncio
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gateway_service import reports
from gateway_service.listing_ui import chips, list_args, pages
from gateway_service.web import clients, download, known_format, render, require, reraise_for_page, ru_date
from shared import config, listing
from shared.domain import Role
from shared.http import ServiceError

router = APIRouter()

AUDIT_FILTERS = ("q", "service", "actor", "date_from", "date_to")
SERVICES = ("sportsman", "competition")
LABELS = {"q": lambda v: f"«{v}»", "actor": lambda v: f"автор {v}",
          "date_from": lambda v: f"с {ru_date(v)}", "date_to": lambda v: f"по {ru_date(v)}"}


def _filters(request: Request) -> tuple[dict, dict, list[str]]:
    """Фильтры из адреса, параметры запроса к сервисам и сервисы, журналы которых нужны."""
    f = {k: request.query_params.get(k, "").strip() for k in AUDIT_FILTERS}
    query = {k: f[k] for k in ("q", "actor", "date_from", "date_to") if f[k]}
    return f, query, [s for s in SERVICES if not f["service"] or f["service"] == s]


async def _audit_page(query: dict, services: list[str], args: dict) -> dict:
    """Страница общего журнала: из каждого сервиса — только первые offset+limit записей в нужном порядке, затем слияние."""
    want = min(args["offset"] + args["limit"], listing.AUDIT_LIMIT)
    try:
        parts = await asyncio.gather(*(clients[s].get("/api/v1/audit/search", **query, order=args["order"], offset=0, limit=want)
                                       for s in services))
    except ServiceError as exc:
        reraise_for_page(exc)
    total = sum(p["total"] for p in parts)
    offset = listing.clamp_offset(total, args["offset"], args["limit"])
    merged = sorted((a for p in parts for a in p["items"]), key=lambda a: a["at"], reverse=args["order"] == "desc")
    return {"total": total, "offset": offset, "limit": args["limit"], "items": merged[offset:offset + args["limit"]]}


@router.get("/audit", response_class=HTMLResponse)
async def audit_page(request: Request):
    user = await require(request, Role.ADMIN)
    f, query, services = _filters(request)
    args = list_args(request, ("at",), "at", "desc")
    res = await _audit_page(query, services, args)
    filter_chips, reset_href = chips(request, f, LABELS)
    return render(request, "audit.html", user=user, rows=res["items"], pg=pages(res), f=f, order=args["order"],
                  chips=filter_chips, reset_href=reset_href, export_qs=urlencode({k: v for k, v in f.items() if v}))


@router.get("/audit/export.{fmt}")
async def export_audit(request: Request, fmt: str):
    known_format(fmt)
    await require(request, Role.ADMIN)
    f, query, services = _filters(request)
    try:
        chunks = await asyncio.gather(*(clients[s].get("/api/v1/audit", limit=listing.AUDIT_LIMIT, **query) for s in services))
    except ServiceError as exc:
        reraise_for_page(exc)
    rows = sorted((a for chunk in chunks for a in chunk), key=lambda a: a["at"], reverse=True)
    chosen = "; ".join(f"{k}: {v}" for k, v in f.items() if v)
    return await download(reports.audit_doc(rows, chosen or "без фильтров", config.today()), fmt)

"""Реестр федерации: спортсмены, тренеры, судьи и организации — поиск, фильтры, страницы, выгрузка."""
import asyncio
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from gateway_service import reports
from gateway_service.listing_ui import chips, list_args, pages
from gateway_service.web import clients, download, known_format, render, require, reraise_for_page
from shared import config, domain, listing
from shared.domain import Role
from shared.http import ServiceError

router = APIRouter()

REGISTRY_TABS = {"athlete": "Спортсмены", "trainer": "Тренеры", "judge": "Судьи", "org": "Организации"}
_PEOPLE_FILTERS = ("q", "discipline", "organization", "age_group", "municipality")
# Фильтры, у которых на вкладке есть поле: остальные не переносятся при смене вкладки и не применяются скрыто.
TAB_FILTERS = {"athlete": _PEOPLE_FILTERS + ("rank", "stage"), "trainer": _PEOPLE_FILTERS, "judge": _PEOPLE_FILTERS,
               "org": ("q", "discipline", "municipality")}
REGISTRY_SORTS = ("name", "age", "rank", "organization", "municipality", "start_year", "created")
EXPORT_MAX_ROWS = 20000
FILTER_LABELS = {
    "q": lambda v: f"«{v}»", "discipline": lambda v: domain.DISCIPLINE_BY_CODE.get(v, {}).get("short", v),
    "rank": lambda v: "без разряда" if v == "none" else f"разряд {domain.RANK_BY_CODE.get(v, {}).get('short', v)}",
    "stage": lambda v: "этап не указан" if v == "none" else f"этап {v}",
}
EXPORT_LABELS = {"discipline": "дисциплина", "organization": "организация", "rank": "разряд", "age_group": "возрастная группа",
                 "municipality": "муниципалитет", "stage": "этап подготовки", "q": "поиск"}


def _registry_args(request: Request) -> tuple[str, dict]:
    tab = request.query_params.get("tab", "athlete")
    tab = tab if tab in REGISTRY_TABS else "athlete"
    return tab, {k: request.query_params.get(k, "").strip() for k in TAB_FILTERS[tab]}


def _tab_links(filters: dict) -> dict[str, str]:
    """Ссылки на вкладки с теми фильтрами текущей, которые на целевой вкладке видны и применимы."""
    return {tab: "/registry?" + urlencode({"tab": tab, **{k: v for k, v in filters.items() if v and k in keys}})
            for tab, keys in TAB_FILTERS.items()}


async def _organizations(filters: dict) -> list[dict]:
    return await clients["sportsman"].get("/api/v1/organizations", **{k: v for k, v in filters.items() if v})


async def _people(tab: str, filters: dict, args: dict) -> dict:
    try:
        return await clients["sportsman"].get("/api/v1/registry/persons", role=tab,
                                              **{k: v for k, v in filters.items() if v}, **args)
    except ServiceError as exc:
        reraise_for_page(exc)


@router.get("/registry", response_class=HTMLResponse)
async def registry_page(request: Request):
    user = await require(request, Role.ADMIN)
    tab, filters = _registry_args(request)
    args = list_args(request, REGISTRY_SORTS, "name")
    if tab == "org":
        res, orgs = None, await _organizations(filters)
        rows = orgs
    else:
        res, orgs = await asyncio.gather(_people(tab, filters, args), _organizations({}))
        rows = res["items"]
    filter_chips, reset_href = chips(request, filters, FILTER_LABELS)
    export_qs = urlencode({"tab": tab, **{k: v for k, v in filters.items() if v}, "sort": args["sort"], "order": args["order"]})
    return render(request, "registry.html", user=user, tab=tab, tabs=REGISTRY_TABS, tab_links=_tab_links(filters),
                  rows=rows, orgs=orgs, filters=filters, export_qs=export_qs, facets=res["facets"] if res else {},
                  pg=pages(res) if res else None, sort=args["sort"], order=args["order"],
                  chips=filter_chips, reset_href=reset_href)


@router.get("/registry/export.{fmt}")
async def export_registry(request: Request, fmt: str):
    known_format(fmt)
    await require(request, Role.ADMIN)
    tab, filters = _registry_args(request)
    if tab == "org":
        rows = await _organizations(filters)
    else:  # выгружается весь отфильтрованный список, а не текущая страница
        args = {**list_args(request, REGISTRY_SORTS, "name"), "offset": 0, "limit": listing.MAX_LIMIT}
        first = await _people(tab, filters, args)
        if first["total"] > EXPORT_MAX_ROWS:
            raise HTTPException(422, f"Слишком много записей для выгрузки ({first['total']}) — сузьте фильтр до {EXPORT_MAX_ROWS}")
        rest = await asyncio.gather(*(_people(tab, filters, {**args, "offset": off})
                                      for off in range(listing.MAX_LIMIT, first["total"], listing.MAX_LIMIT)))
        rows = first["items"] + [p for chunk in rest for p in chunk["items"]]
    shown = {**filters, "discipline": domain.DISCIPLINE_BY_CODE.get(filters["discipline"], {}).get("short", ""),
             "rank": "без разряда" if filters.get("rank") == "none" else filters.get("rank", ""),
             "stage": "не указан" if filters.get("stage") == "none" else filters.get("stage", "")}
    chosen = "; ".join(f"{EXPORT_LABELS[k]}: {v}" for k, v in shown.items() if v)
    return await download(reports.registry_doc(tab, REGISTRY_TABS[tab], rows, chosen or "без фильтров", config.today()), fmt)

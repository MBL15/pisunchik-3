"""Модерация заявок на соревнования любого объёма: поиск, фильтры со счётчиками, массовое решение."""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gateway_service import notify
from gateway_service.listing_ui import chips, list_args, pages
from gateway_service.web import back, clients, humanize, render, require, safe_back
from shared import domain
from shared.domain import Role
from shared.http import ServiceError

router = APIRouter()

APP_SORTS = ("created", "athlete", "start", "deadline")
APP_FILTERS = ("q", "status", "competition_id", "discipline", "by")
STATUS_ALL = "all"  # значение фильтра статуса «все»; без параметра показываются заявки на модерации


def _app_filters(request: Request) -> dict:
    p = request.query_params
    f = {k: p.get(k, "").strip() for k in APP_FILTERS}
    if "status" not in p:
        f["status"] = "PENDING"
    return f


@router.get("/applications", response_class=HTMLResponse)
async def applications_page(request: Request):
    user = await require(request, Role.ADMIN)
    f = _app_filters(request)
    args = list_args(request, APP_SORTS, "created", "desc")
    query = {k: v for k, v in f.items() if v and v != STATUS_ALL}
    co = clients["competition"]
    res, comps = await asyncio.gather(co.get("/api/v1/applications/search", **query, **args), co.get("/api/v1/competitions"))
    comp_by_id = {str(c["id"]): c for c in comps}
    labels = {"status": lambda v: domain.APPLICATION_STATUS_LABELS.get(v, v).lower(),
              "competition_id": lambda v: comp_by_id[v]["code"] if v in comp_by_id else f"#{v}",
              "discipline": lambda v: domain.DISCIPLINE_BY_CODE.get(v, {}).get("short", v),
              "by": lambda v: "подал тренер" if v == "trainer" else "подал сам", "q": lambda v: f"«{v}»"}
    filter_chips, reset_href = chips(request, f, labels, defaults={"status": STATUS_ALL})
    with_apps = sorted((c for c in comps if str(c["id"]) in res["facets"]["competition"] or str(c["id"]) == f["competition_id"]),
                       key=lambda c: c["start_date"], reverse=True)
    back_url = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return render(request, "applications.html", user=user, res=res, pg=pages(res), f=f, sort=args["sort"], order=args["order"],
                  chips=filter_chips, reset_href=reset_href, comps=with_apps, back_url=back_url)


@router.post("/applications/decide")
async def decide_applications(request: Request):
    """Решение по отмеченным заявкам или по одной (кнопка в строке: `single=СТАТУС:id`)."""
    user = await require(request, Role.ADMIN)
    form = await request.form()
    back_url = safe_back(str(form.get("back", "")), "/applications")
    if single := str(form.get("single", "")):
        status, _, one = single.partition(":")
        ids = [int(one)] if one.isdigit() else []
    else:
        status, ids = str(form.get("status", "")), [int(x) for x in form.getlist("ids") if str(x).isdigit()]
    if not ids:
        return back(back_url, "error", "Отметьте хотя бы одну заявку")
    try:
        res = await clients["competition"].post("/api/v1/applications/decisions", json={
            "ids": ids, "status": status, "actor": user["person"]["fsp_id"], "comment": str(form.get("comment", "")).strip()})
    except ServiceError as exc:
        return back(back_url, "error", humanize(exc.detail))
    groups: dict[tuple[str, str], set[str]] = {}  # одно уведомление на решение и соревнование
    for a in res["done"]:
        text = a["competition_title"] + (f" · {a['comment']}" if a.get("comment") else "")
        groups.setdefault((a["status_label"], text), set()).update({a["athlete_fsp_id"], a["submitted_by"]})
    await asyncio.gather(*(notify.notify_many(people, f"Заявка: {label}", text, "/cabinet")
                           for (label, text), people in groups.items()))
    verdict = "одобрено" if status == "APPROVED" else "отклонено"
    msg = f"{verdict.capitalize()}: {len(res['done'])}"
    if res["failed"]:
        msg += f"; пропущено {len(res['failed'])} (решение уже принято или заявка не найдена)"
    return back(back_url, "ok" if res["done"] else "error", msg)

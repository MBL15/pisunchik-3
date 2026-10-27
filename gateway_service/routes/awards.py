"""Д8. Премии Губернатора: справочник размеров, автоотбор претендентов по протоколам года, ходатайство и его статусы, пакет документов."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gateway_service import notify, reports
from gateway_service.web import back, clients, download, humanize, known_format, render, require
from shared import config, domain
from shared.domain import Role
from shared.http import ServiceError

router = APIRouter()
LEVELS = [k for k in domain.DEFAULT_POINTS_TABLE]  # CHAMP_RU, FIRST_RU, INTERREG, REGIONAL — те же уровни, что и в рейтинге


@router.get("/awards", response_class=HTMLResponse)
async def awards_page(request: Request, year: int | None = None):
    user = await require(request, Role.ADMIN, Role.MINISTRY)
    data = await clients["competition"].get("/api/v1/awards/candidates", year=year)
    return render(request, "awards.html", user=user, a=data, year=data["year"], levels=LEVELS, can_edit=user["role"] == Role.ADMIN,
                  level_labels=domain.LEVEL_LABELS, today=config.today())


@router.post("/awards/rates")
async def save_rates(request: Request):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        rates = {lv: [int(form.get(f"{lv}_{i}", 0) or 0) for i in range(3)] for lv in LEVELS}
        await clients["competition"].put("/api/v1/awards/rates", json={"rates": rates, "actor": user["person"]["fsp_id"]})
    except ValueError:
        return back("/awards", "error", "Размеры премий — целые числа")
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/awards", "error", humanize(exc.detail))
    return back("/awards", "ok", "Справочник размеров премий сохранён: суммы пересчитаны")


@router.post("/awards/petitions")
async def create_petition(request: Request):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    year = int(form.get("year", 0) or 0)
    try:
        p = await clients["competition"].post("/api/v1/awards/petitions", json={
            "year": year, "exclude": form.getlist("exclude"), "note": str(form.get("note", "")), "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(f"/awards?year={year}", "error", humanize(exc.detail))
    return back(f"/awards?year={year}", "ok", f"Ходатайство подготовлено: {len(p['items'])} чел., {p['total']:,} ₽".replace(",", " "))


@router.post("/awards/petitions/{pid}/status")
async def petition_status(request: Request, pid: int):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        p = await clients["competition"].post(f"/api/v1/awards/petitions/{pid}/status", json={
            "status": str(form.get("status", "")), "order_no": str(form.get("order_no", "")), "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/awards", "error", humanize(exc.detail))
    if p["status"] == "AWARDED":
        await notify.notify_many([i["fsp_id"] for i in p["items"]], "Вам назначена премия Губернатора",
                                 f"За результаты {p['year']} года; акт {p['order_no']}", "/cabinet")
    return back(f"/awards?year={p['year']}", "ok", f"Ходатайство: {p['status_label'].lower()}")


@router.get("/awards/petitions/{pid}/package.{fmt}")
async def petition_package(request: Request, pid: int, fmt: str):
    known_format(fmt)
    await require(request, Role.ADMIN, Role.MINISTRY)
    p = await clients["competition"].get(f"/api/v1/awards/petitions/{pid}")
    return await download(reports.awards_petition_doc(p), fmt)

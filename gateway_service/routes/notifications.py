"""Уведомления: лента в кабинете, запись в ленту для `notify` и источник сроков для напоминаний."""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gateway_service.listing_ui import list_args, pages
from gateway_service.web import clients, render, require, ru_date, safe
from shared import config, domain

router = APIRouter()

REG_REMIND_DAYS = 7  # за сколько дней до конца приёма заявок напоминать спортсменам, ещё не подавшим заявку


async def record_notifications(fsp_ids: list[str], title: str, text: str, link: str, key: str) -> list[str]:
    res = await clients["sportsman"].post("/api/v1/notifications", json={
        "fsp_ids": fsp_ids, "title": title, "text": text, "link": link, "key": key})
    return res["delivered"]


async def deadline_reminders() -> list[tuple]:
    """Сроки для напоминаний → (fsp_id, ключ, заголовок, текст, дней до срока, ссылка).

    Спортсмену — конец приёма заявок по его дисциплинам и срок разряда; судье — срок категории; федерации — отчёты.
    """
    sp, co = clients["sportsman"], clients["competition"]
    ranks, judges, admins, open_comps = await asyncio.gather(
        sp.get("/api/v1/alerts/ranks", within_days=30), sp.get("/api/v1/alerts/judge-categories", within_days=30),
        sp.get("/api/v1/persons", role="admin"), co.get("/api/v1/competitions", status="REGISTRATION_OPEN"))
    out = []
    for p in ranks:
        out.append((p["fsp_id"], f"rank:{p['rank']['expires_at']}", "Истекает срок разряда",
                    f"{p['rank']['title']}: до {ru_date(p['rank']['expires_at'])}", p["rank"]["days_left"], "/cabinet#rank"))
    for p in judges:
        out.append((p["fsp_id"], f"judge:{p['judge_category']['expires_at']}", "Истекает судейская категория",
                    f"до {ru_date(p['judge_category']['expires_at'])}", p["judge_category"]["days_left"], "/cabinet#category"))
    for d in domain.report_deadlines(config.today()):
        for a in admins:
            out.append((a["fsp_id"], f"report:{d['title']}:{d['date']}", f"Срок отчёта: {d['title']}",
                        f"до {ru_date(d['date'])} · {d['note']}", d["days_left"], "/cabinet#inbox"))
    soon = [c for c in open_comps if c["days_to_deadline"] is not None and 0 <= c["days_to_deadline"] <= REG_REMIND_DAYS]
    lists = await asyncio.gather(*(asyncio.gather(sp.get("/api/v1/persons", role="athlete", discipline=c["discipline"]),
                                                  co.get("/api/v1/applications", competition_id=c["id"])) for c in soon))
    for c, (athletes, apps) in zip(soon, lists):
        applied = {a["athlete_fsp_id"] for a in apps if a["status"] != "REJECTED"}
        lo, hi = domain.AGE_GROUPS[c["age_group"]]
        for p in athletes:
            if p["fsp_id"] not in applied and lo <= p["age"] <= hi and not p["is_sanctioned"]:
                out.append((p["fsp_id"], f"registration:{c['id']}:{c['registration_deadline']}", "Заканчивается приём заявок",
                            f"{c['title']} · до {ru_date(c['registration_deadline'])}", c["days_to_deadline"], "/cabinet#starts"))
    return out


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request, q: str = "", unread: bool = False):
    user = await require(request)
    fid = user["person"]["fsp_id"]
    args = list_args(request, ("at",), "at", "desc")
    feed = await clients["sportsman"].get("/api/v1/notifications", fsp_id=fid, limit=args["limit"], offset=args["offset"],
                                          unread_only=unread, q=q or None)
    shown = [n["id"] for n in feed["items"] if not n["read"]]
    if shown:  # прочитано то, что показано; на этом экране новые ещё подсвечены, скрытые фильтром остаются новыми
        res = await safe(clients["sportsman"].request("POST", "/api/v1/notifications/read", params={"fsp_id": fid},
                                                      json={"ids": shown}), None, [])
        if res:
            user["unread"] = res["unread"]
    return render(request, "notifications.html", user=user, items=feed["items"], pg=pages(feed), feed=feed,
                  filters={"q": q, "unread": unread})

"""Уведомления: лента в кабинете, запись в ленту для `notify` и источник сроков для напоминаний."""
import asyncio
import hashlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gateway_service import notify
from gateway_service.listing_ui import list_args, pages
from gateway_service.web import clients, render, require, ru_date, safe
from shared import config, domain

router = APIRouter()

REG_REMIND_DAYS = 7  # за сколько дней до конца приёма заявок напоминать спортсменам, ещё не подавшим заявку


async def record_notifications(fsp_ids: list[str], title: str, text: str, link: str, key: str) -> list[str]:
    res = await clients["sportsman"].post("/api/v1/notifications", json={
        "fsp_ids": fsp_ids, "title": title, "text": text, "link": link, "key": key})
    return res["delivered"]


async def _rank_reminders_for_trainers(ranks: list[dict]) -> list[tuple]:
    """Тренеру — по каждому подопечному с истекающим разрядом (привязка активна и ведёт не сам спортсмен)."""
    links = await asyncio.gather(*(clients["sportsman"].get(f"/api/v1/athletes/{p['fsp_id']}/links") for p in ranks))
    out = []
    for p, athlete_links in zip(ranks, links):
        rank = p["rank"]
        text = f"{p['full_name']} · {rank['title']}: до {ru_date(rank['expires_at'])}"
        for trainer in {lk["trainer_fsp_id"] for lk in athlete_links if lk["status"] == "ACTIVE"} - {p["fsp_id"]}:
            out.append((trainer, f"rank:{p['fsp_id']}:{rank['expires_at']}", "Истекает срок разряда подопечного", text,
                        rank["days_left"], "/cabinet#ranks"))
    return out


def _rank_digest_for_federation(ranks: list[dict], admins: list[dict]) -> list[tuple]:
    """Федерации — одна сводка на порог (истёк, ≤1, ≤7, ≤30 дн.) и состав спортсменов, а не по уведомлению на каждого."""
    buckets: dict[str, list[dict]] = {}
    for p in ranks:
        if bucket := notify.remind_bucket(p["rank"]["days_left"]):
            buckets.setdefault(bucket, []).append(p)
    out = []
    for bucket, people in buckets.items():
        names = ", ".join(p["full_name"] for p in people[:3]) + (f" и ещё {len(people) - 3}" if len(people) > 3 else "")
        who = hashlib.sha1(",".join(sorted(p["fsp_id"] for p in people)).encode()).hexdigest()[:8]
        title = "Срок разряда истёк" if bucket == "expired" else "Скоро истекает срок разряда"
        for a in admins:
            out.append((a["fsp_id"], f"rank-digest:{who}", title, f"{len(people)} чел.: {names}",
                        people[0]["rank"]["days_left"], "/registry?tab=athlete&sort=rank"))
    return out


async def deadline_reminders() -> list[tuple]:
    """Сроки для напоминаний → (fsp_id, ключ, заголовок, текст, дней до срока, ссылка).

    Спортсмену — конец приёма заявок по его дисциплинам и срок разряда; его тренерам (активные привязки) — срок разряда
    подопечного; судье — срок категории; федерации — отчёты и сводка по истекающим разрядам.
    """
    sp, co = clients["sportsman"], clients["competition"]
    ranks, judges, admins, open_comps = await asyncio.gather(
        sp.get("/api/v1/alerts/ranks", within_days=30), sp.get("/api/v1/alerts/judge-categories", within_days=30),
        sp.get("/api/v1/persons", role="admin"), co.get("/api/v1/competitions", status="REGISTRATION_OPEN"))
    out = []
    for p in ranks:
        out.append((p["fsp_id"], f"rank:{p['rank']['expires_at']}", "Истекает срок разряда",
                    f"{p['rank']['title']}: до {ru_date(p['rank']['expires_at'])}", p["rank"]["days_left"], "/cabinet#rank"))
    out += await _rank_reminders_for_trainers(ranks)
    out += _rank_digest_for_federation(ranks, admins)
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
        for p in athletes:
            if p["fsp_id"] not in applied and domain.age_group_fit(p["birth_date"], c["age_group"], c["start_date"])[1] \
                    and not p["is_sanctioned"]:
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

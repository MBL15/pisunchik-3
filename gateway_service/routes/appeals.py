"""Д5. Апелляции на результат и рапорты о нарушениях: подача с доказательствами, рассмотрение федерацией, решение, корректировка протокола."""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from gateway_service import notify
from gateway_service.web import admin_ids, back, clients, humanize, render, require, safe
from shared.domain import Role
from shared.http import ServiceError

router = APIRouter()
EVIDENCE_ROWS = 3
KINDS = {"LOG": "Лог", "SCREENSHOT": "Скриншот", "VIDEO": "Видео", "OTHER": "Другое"}


def _evidence(form) -> list[dict]:
    rows = zip(form.getlist("ev_kind"), form.getlist("ev_title"), form.getlist("ev_url"))
    return [{"kind": k if k in KINDS else "OTHER", "title": t.strip(), "url": u.strip()} for k, t, u in rows if t.strip() or u.strip()]


async def _own_results(user: dict) -> list[dict]:
    """Результаты, которые можно оспорить: свои (спортсмен) или подопечных (тренер) — из опубликованных протоколов."""
    sp, fid = clients["sportsman"], user["person"]["fsp_id"]
    if user["role"] == Role.ATHLETE:
        perf = await safe(sp.get(f"/api/v1/persons/{fid}/performance"), None, [])
        return [{"fsp_id": fid, "name": user["person"]["full_name"], **r} for r in (perf["results"] if perf and perf.get("available") else [])]
    wards = await safe(sp.get(f"/api/v1/trainers/{fid}/athletes", with_performance=True), [], [])
    return [{"fsp_id": lk["athlete_fsp_id"], "name": lk["athlete"]["full_name"], **r} for lk in wards if lk["status"] == "ACTIVE"
            for r in ((lk.get("performance") or {}).get("results") or [])]


@router.get("/appeals", response_class=HTMLResponse)
async def appeals_page(request: Request, violation_cid: int | None = None, status: str = ""):
    user = await require(request, Role.ADMIN, Role.ATHLETE, Role.TRAINER, Role.JUDGE)
    co, role, me = clients["competition"], user["role"], user["person"]["fsp_id"]
    degraded: list[str] = []
    appeals, violations, results, report_comps, entries = [], [], [], [], []
    if role == Role.ADMIN:
        appeals, violations, comps = await asyncio.gather(
            safe(co.get("/api/v1/appeals", status=status or None), [], degraded),
            safe(co.get("/api/v1/violations", status=status or None), [], degraded),
            safe(co.get("/api/v1/competitions"), [], degraded))
        report_comps = [c for c in comps if c["has_protocol"]]
    elif role == Role.ATHLETE:
        appeals, violations, results = await asyncio.gather(
            safe(co.get("/api/v1/appeals", athlete_fsp_id=me), [], degraded),
            safe(co.get("/api/v1/violations", subject_fsp_id=me), [], degraded), _own_results(user))
    elif role == Role.TRAINER:
        results = await _own_results(user)
        wards = sorted({r["fsp_id"] for r in results})
        own, *by_ward = await asyncio.gather(safe(co.get("/api/v1/appeals", submitted_by=me), [], degraded),
                                             *(safe(co.get("/api/v1/appeals", athlete_fsp_id=w), [], degraded) for w in wards))
        appeals = list({a["id"]: a for a in own + [x for ws in by_ward for x in ws]}.values())
    else:
        violations, assignments = await asyncio.gather(
            safe(co.get("/api/v1/violations", reported_by=me), [], degraded),
            safe(co.get(f"/api/v1/judges/{me}/assignments"), {"upcoming": [], "past": []}, degraded))
        report_comps = [c for c in assignments["upcoming"] + assignments["past"] if c["has_protocol"]]
    if violation_cid and role in (Role.ADMIN, Role.JUDGE) and any(c["id"] == violation_cid for c in report_comps):
        proto = await safe(co.get(f"/api/v1/competitions/{violation_cid}/protocol"), None, degraded)
        entries = proto["entries"] if proto else []
    appeals.sort(key=lambda a: a["created_at"], reverse=True)
    return render(request, "appeals.html", user=user, appeals=appeals, violations=violations, results=results, report_comps=report_comps,
                  entries=entries, violation_cid=violation_cid, status=status, evidence_rows=range(EVIDENCE_ROWS), kinds=KINDS,
                  degraded=sorted(set(degraded)))


@router.post("/appeals")
async def submit_appeal(request: Request):
    user = await require(request, Role.ATHLETE, Role.TRAINER)
    form = await request.form()
    fid, _, cid = str(form.get("result", "")).partition("|")
    try:
        a = await clients["competition"].post("/api/v1/appeals", json={
            "competition_id": int(cid or 0), "athlete_fsp_id": fid or user["person"]["fsp_id"], "subject": str(form.get("subject", "")),
            "description": str(form.get("description", "")), "evidence": _evidence(form), "actor": user["person"]["fsp_id"]})
    except (ServiceError, ValueError) as exc:
        if isinstance(exc, ServiceError) and exc.unavailable:
            raise
        return back("/appeals", "error", humanize(exc.detail) if isinstance(exc, ServiceError) else "Выберите результат")
    await notify.notify_many(await admin_ids(), "Новая апелляция", f"{a['athlete_name']} · {a['competition_title']}", "/appeals")
    if a["athlete_fsp_id"] != user["person"]["fsp_id"]:
        await notify.notify(a["athlete_fsp_id"], "Тренер подал апелляцию на ваш результат", a["competition_title"], "/appeals")
    return back("/appeals", "ok", f"Апелляция №{a['id']} подана — федерация рассмотрит её и сообщит решение", celebrate=True)


@router.post("/appeals/{aid}/action")
async def appeal_action(request: Request, aid: int):
    user = await require(request, Role.ADMIN, Role.ATHLETE, Role.TRAINER)
    action = str((await request.form()).get("action", ""))
    if action == "review" and user["role"] != Role.ADMIN:
        raise HTTPException(403, "Взять апелляцию в рассмотрение может только федерация")
    try:
        a = await clients["competition"].post(f"/api/v1/appeals/{aid}/action", json={"actor": user["person"]["fsp_id"], "action": action})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/appeals", "error", humanize(exc.detail))
    if action == "review":
        await notify.notify_many({a["athlete_fsp_id"], a["submitted_by"]}, "Апелляция взята в рассмотрение", a["competition_title"], "/appeals")
    return back("/appeals", "ok", f"Апелляция №{aid}: {a['status_label'].lower()}")


@router.post("/appeals/{aid}/decision")
async def appeal_decision(request: Request, aid: int):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    payload = {"actor": user["person"]["fsp_id"], "accept": form.get("decision") == "accept", "rationale": str(form.get("rationale", ""))}
    action = str(form.get("correction", ""))
    if action:
        payload["correction"] = {"action": action, "solved": int(form.get("solved") or 0), "penalty": int(form.get("penalty") or 0),
                                 "score": float(form.get("score") or 0), "reason": str(form.get("reason", ""))}
    try:
        a = await clients["competition"].post(f"/api/v1/appeals/{aid}/decision", json=payload)
    except (ServiceError, ValueError) as exc:
        if isinstance(exc, ServiceError) and exc.unavailable:
            raise
        return back("/appeals", "error", humanize(exc.detail) if isinstance(exc, ServiceError) else "Числа в корректировке заполнены неверно")
    corrected = " Протокол скорректирован." if a["decision"]["correction"] else ""
    await notify.notify_many({a["athlete_fsp_id"], a["submitted_by"]}, f"Апелляция {a['status_label'].lower()}", f"{a['competition_title']}.{corrected}",
                             "/appeals")
    return back("/appeals", "ok", f"Апелляция №{aid}: {a['status_label'].lower()}.{corrected}")


@router.post("/violations")
async def report_violation(request: Request):
    user = await require(request, Role.ADMIN, Role.JUDGE)
    form = await request.form()
    try:
        cid = int(form.get("competition_id", 0) or 0)
        if user["role"] == Role.JUDGE:
            c = await clients["competition"].get(f"/api/v1/competitions/{cid}")
            if not any(j["fsp_id"] == user["person"]["fsp_id"] for j in c["judges"]):
                raise HTTPException(403, "Рапорт подаёт судья из коллегии этого соревнования или федерация")
        v = await clients["competition"].post("/api/v1/violations", json={
            "competition_id": cid, "subject_fsp_id": str(form.get("subject_fsp_id", "")), "description": str(form.get("description", "")),
            "evidence": _evidence(form), "actor": user["person"]["fsp_id"]})
    except (ServiceError, ValueError) as exc:
        if isinstance(exc, ServiceError) and exc.unavailable:
            raise
        return back("/appeals", "error", humanize(exc.detail) if isinstance(exc, ServiceError) else "Выберите соревнование")
    await notify.notify_many(await admin_ids(), "Рапорт о нарушении", f"{v['subject_name']} · {v['competition_title']}", "/appeals")
    return back("/appeals", "ok", f"Рапорт №{v['id']} подан — федерация рассмотрит его", celebrate=True)


@router.post("/violations/{vid}/action")
async def violation_action(request: Request, vid: int):
    user = await require(request, Role.ADMIN)
    try:
        v = await clients["competition"].post(f"/api/v1/violations/{vid}/action", json={"actor": user["person"]["fsp_id"], "action": "review"})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/appeals", "error", humanize(exc.detail))
    return back("/appeals", "ok", f"Рапорт №{vid}: {v['status_label'].lower()}")


@router.post("/violations/{vid}/decision")
async def violation_decision(request: Request, vid: int):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        v = await clients["competition"].post(f"/api/v1/violations/{vid}/decision", json={
            "actor": user["person"]["fsp_id"], "decision": str(form.get("decision", "")), "rationale": str(form.get("rationale", "")),
            "sanction_days": int(form.get("sanction_days") or 0), "reason": str(form.get("reason", ""))})
    except (ServiceError, ValueError) as exc:
        if isinstance(exc, ServiceError) and exc.unavailable:
            raise
        return back("/appeals", "error", humanize(exc.detail) if isinstance(exc, ServiceError) else "Срок санкции — целое число дней")
    await notify.notify_many({v["subject_fsp_id"], v["reported_by"]}, f"Решение по рапорту: {v['decision_label'].lower()}", v["competition_title"], "/appeals")
    return back("/appeals", "ok", f"Рапорт №{vid}: {v['decision_label'].lower()}")

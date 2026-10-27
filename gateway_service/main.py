"""gateway_service (port 8000): API gateway + server-rendered UI.

* Renders Jinja2/Tailwind pages; all data comes from internal services over async httpx.
* Owns the session: signed cookie {fsp_id, active_role}; the role is re-validated against the registry on
  every request, so switching roles never requires re-login and permissions follow the *active* role.
* Degrades gracefully: if a downstream service is down, pages still render with a terminal-style warning.
"""
import asyncio
import json
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gateway_service import exports, notify, reports, tours
from gateway_service.qr import qr_svg
from gateway_service.session import (
    FLASH_COOKIE, clear_session, pop_flash, read_session, set_flash, write_session,
)
from shared import config, domain
from shared.demo_roster import DEMO_ACCOUNTS, DEMO_PASSWORD
from shared.domain import Role
from shared.http import ServiceClient, ServiceError

BASE_DIR = Path(__file__).parent
clients: dict[str, ServiceClient] = {}
JUDGE_CATEGORY_DEFAULT_YEARS = 2  # подсказка в форме решения; федерация вправе изменить срок


@asynccontextmanager
async def lifespan(_: FastAPI):
    clients["sportsman"] = ServiceClient("sportsman_service", config.SPORTSMAN_URL, caller="gateway")
    clients["competition"] = ServiceClient("competition_service", config.COMPETITION_URL, caller="gateway")
    tg_tasks = await notify.start(_deadline_reminders)
    yield
    await notify.stop(tg_tasks)
    for c in clients.values():
        await c.aclose()


async def _deadline_reminders() -> list[tuple]:
    """Истекающие разряды и судейские категории → (fsp_id, ключ, заголовок, текст, дней до окончания)."""
    out = []
    for p in await clients["sportsman"].get("/api/v1/alerts/ranks", within_days=30):
        out.append((p["fsp_id"], "rank", "Истекает срок разряда",
                    f"{p['rank']['title']}: до {ru_date(p['rank']['expires_at'])}", p["rank"]["days_left"]))
    for p in await clients["sportsman"].get("/api/v1/alerts/judge-categories", within_days=30):
        out.append((p["fsp_id"], "judge", "Истекает судейская категория",
                    f"до {ru_date(p['judge_category']['expires_at'])}", p["judge_category"]["days_left"]))
    return out


app = FastAPI(title="ФСП Оренбург · Gateway", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.globals["tg"] = notify

_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)).date() if "T" in str(value) else date.fromisoformat(str(value)[:10])


def ru_date(value: Any, long: bool = False) -> str:
    d = _to_date(value)
    if not d:
        return "—"
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}" if long else d.strftime("%d.%m.%Y")


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def days_word(n: int) -> str:
    return f"{n} {plural(n, 'день', 'дня', 'дней')}"


def ru_mon(value: Any) -> str:
    d = _to_date(value)
    return _MONTHS[d.month - 1][:3] if d else ""


templates.env.filters.update(ru_date=ru_date, ru_mon=ru_mon, days_word=days_word,
                             tojson_ru=lambda v: json.dumps(v, ensure_ascii=False),
                             tojson_script=lambda v: json.dumps(v, ensure_ascii=False).replace("<", "\\u003c"))
templates.env.globals.update(D=domain, plural=plural, DEMO_PASSWORD=DEMO_PASSWORD)


# ---------------------------------------------------------------- plumbing
class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


@app.exception_handler(Redirect)
async def _redirect_handler(_: Request, exc: Redirect):
    return RedirectResponse(exc.url, status_code=303)


@app.exception_handler(ServiceError)
async def _service_error_handler(request: Request, exc: ServiceError):
    return templates.TemplateResponse(request, "error.html", {
        "user": None, "flash": None, "code": exc.status, "service": exc.service, "message": exc.detail,
    }, status_code=502 if exc.unavailable else exc.status)


@app.exception_handler(HTTPException)
async def _http_error_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return templates.TemplateResponse(request, "error.html", {
        "user": None, "flash": None, "code": exc.status_code, "service": "gateway_service", "message": exc.detail,
    }, status_code=exc.status_code)


async def safe(coro, default: Any, degraded: list[str]):
    """Await a downstream call; on failure record a degradation notice and return `default`."""
    try:
        return await coro
    except ServiceError as exc:
        degraded.append(f"{exc.service}: {exc.detail}")
        return default


async def current_user(request: Request) -> dict | None:
    if hasattr(request.state, "user"):  # resolved once per request
        return request.state.user
    user = None
    if s := read_session(request):
        try:
            person = await clients["sportsman"].get(f"/api/v1/persons/{s['fid']}")
            role = s["role"] if s["role"] in person["roles"] else person["roles"][0]
            user = {"person": person, "role": role, "role_label": domain.ROLE_LABELS[role]}
        except ServiceError as exc:
            if exc.status != 404:
                raise
    request.state.user = user
    return user


async def require(request: Request, *roles: str) -> dict:
    user = await current_user(request)
    if not user:
        raise Redirect(f"/login?next={request.url.path}")
    if roles and user["role"] not in roles:
        raise HTTPException(403, f"Раздел доступен роли: {', '.join(domain.ROLE_LABELS[r] for r in roles)}. "
                                 f"Переключите активную роль в шапке.")
    return user


def render(request: Request, name: str, **context) -> HTMLResponse:
    flash = pop_flash(request)
    user = context.get("user")
    tour = tours.for_page(name, user, can_pick=context.get("can_pick"))
    tour_user = user["person"]["fsp_id"] if user else "guest"
    resp = templates.TemplateResponse(request, name, {"flash": flash, "today": config.today(), "tour": tour,
                                                      "tour_user": tour_user, **context})
    if flash:
        resp.delete_cookie(FLASH_COOKIE)
    return resp


def back(url: str, kind: str, text: str, **extra) -> RedirectResponse:
    resp = RedirectResponse(url, status_code=303)
    set_flash(resp, kind, text, extra or None)
    return resp


def humanize(detail: Any) -> str:
    """Turn FastAPI/pydantic error payloads into one readable Russian sentence."""
    if isinstance(detail, list):
        return "; ".join(re.sub(r"^Value error, ", "", d.get("msg", str(d))) for d in detail)
    if isinstance(detail, dict):
        return detail.get("message", str(detail))
    return str(detail)


async def form_dict(request: Request) -> dict:
    form = await request.form()
    return {k: (form.getlist(k) if len(form.getlist(k)) > 1 else form.get(k)) for k in form.keys()}


# ---------------------------------------------------------------- public pages
async def _health(name: str) -> dict:
    try:
        return {"ok": True, **(await clients[name].get("/health"))}
    except ServiceError as exc:
        return {"ok": False, "service": exc.service, "error": str(exc.detail)}


@app.get("/health")
async def health():
    sp, co = await asyncio.gather(_health("sportsman"), _health("competition"))
    return {"gateway_service": {"ok": True}, "sportsman_service": sp, "competition_service": co}


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    degraded: list[str] = []
    user = await safe(current_user(request), None, degraded)
    sp_h, co_h, upcoming, rating, stats, live = await asyncio.gather(
        _health("sportsman"), _health("competition"),
        safe(clients["competition"].get("/api/v1/competitions", upcoming=True), [], degraded),
        safe(clients["competition"].get("/api/v1/rating", discipline="ALG"), None, degraded),
        safe(clients["sportsman"].get("/api/v1/stats"), None, degraded),
        safe(clients["competition"].get("/api/v1/competitions", status="LIVE"), [], degraded),
    )
    nearest = {}
    for c in upcoming:
        nearest.setdefault(c["discipline"], c)
    return render(request, "landing.html", user=user, health={"sportsman": sp_h, "competition": co_h},
                  upcoming=[c for c in upcoming if c["status"] != "LIVE"][:4], rating=rating, stats=stats,
                  live=live[0] if live else None, nearest=nearest, degraded=sorted(set(degraded)))


@app.get("/rating", response_class=HTMLResponse)
async def rating_page(request: Request, discipline: str = "ALG", season: int | None = None,
                      age_group: str | None = None, min_rank: str | None = None):
    degraded: list[str] = []
    user = await safe(current_user(request), None, degraded)
    if discipline not in domain.DISCIPLINE_BY_CODE:
        discipline = "ALG"
    rating = await safe(clients["competition"].get("/api/v1/rating", discipline=discipline, season=season,
                                                   age_group=age_group or None, min_rank=min_rank or None), None, degraded)
    return render(request, "rating.html", user=user, rating=rating, discipline=discipline, degraded=degraded,
                  can_pick=bool(user and user["role"] == Role.ADMIN))


@app.get("/calendar", response_class=HTMLResponse)
async def calendar_page(request: Request, discipline: str = "", plan: str = "", format: str = ""):
    degraded: list[str] = []
    user = await safe(current_user(request), None, degraded)
    comps = await safe(clients["competition"].get("/api/v1/competitions", discipline=discipline or None,
                                                  plan=plan or None, format=format or None), [], degraded)
    today = config.today().isoformat()
    upcoming = sorted([c for c in comps if c["end_date"] >= today or c["status"] == "LIVE"], key=lambda c: c["start_date"])
    past = [c for c in comps if c not in upcoming]
    return render(request, "calendar.html", user=user, upcoming=upcoming, past=past,
                  filters={"discipline": discipline, "plan": plan, "format": format}, degraded=degraded)


@app.get("/competitions/{cid}/regulation", response_class=HTMLResponse)
async def regulation_page(request: Request, cid: int):
    degraded: list[str] = []
    user = await safe(current_user(request), None, degraded)
    c, settings = await asyncio.gather(clients["competition"].get(f"/api/v1/competitions/{cid}"),
                                       safe(clients["competition"].get("/api/v1/rating/settings"), None, degraded))
    return render(request, "regulation.html", user=user, c=c, mode=domain.protocol_mode(c["discipline"]),
                  points=settings["points"].get(c["level"]) if settings else None, degraded=degraded)


@app.get("/partials/live/{cid}", response_class=HTMLResponse)
async def live_partial(request: Request, cid: int, compact: bool = False):
    proto = await clients["competition"].post(f"/api/v1/competitions/{cid}/live/tick")
    return templates.TemplateResponse(request, "_live_protocol.html", {"p": proto, "compact": compact})


@app.get("/verify/{number}", response_class=HTMLResponse)
async def verify_page(request: Request, number: str, c: str = ""):
    degraded: list[str] = []
    user = await safe(current_user(request), None, degraded)
    result = await safe(clients["competition"].get(f"/api/v1/documents/{number}/verify", code=c),
                        {"valid": False, "number": number, "document": None}, degraded)
    return render(request, "verify.html", user=user, result=result, degraded=degraded)


# ---------------------------------------------------------------- auth & roles
@app.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    if await current_user(request):
        return RedirectResponse("/cabinet", status_code=303)
    return render(request, "register.html", user=None, values={}, errors=None)


@app.post("/register", response_class=HTMLResponse)
async def register_submit(request: Request):
    form = await request.form()
    values = {k: form.get(k, "") for k in form.keys()}
    roles, disciplines = form.getlist("roles"), form.getlist("disciplines")
    payload = {
        "last_name": values.get("last_name", ""), "first_name": values.get("first_name", ""),
        "middle_name": values.get("middle_name", ""), "gender": values.get("gender", ""),
        "birth_date": values.get("birth_date") or None, "municipality": values.get("municipality", ""),
        "organization": values.get("organization", ""), "roles": roles, "disciplines": disciplines,
        "email": values.get("email", ""), "password": values.get("password", ""),
        "consent_pd": form.get("consent_pd") == "on", "guardian_name": values.get("guardian_name", ""),
        "guardian_consent": form.get("guardian_consent") == "on",
    }
    try:
        person = await clients["sportsman"].post("/api/v1/persons", json=payload)
    except ServiceError as exc:
        if exc.unavailable:
            raise
        values.update(roles=roles, disciplines=disciplines)
        return render(request, "register.html", user=None, values=values, errors=humanize(exc.detail))
    resp = back("/cabinet", "fsp_issued", f"Аккаунт создан. Ваш региональный идентификатор: {person['fsp_id']}",
                fsp_id=person["fsp_id"], roles=person["role_labels"])
    write_session(resp, person["fsp_id"], person["roles"][0])
    return resp


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/cabinet"):
    if await current_user(request):
        return RedirectResponse("/cabinet", status_code=303)
    groups: dict[str, list[dict]] = {}
    for acc in DEMO_ACCOUNTS:
        groups.setdefault(acc["group"], []).append(acc)
    return render(request, "login.html", user=await current_user(request), next=next, error=None, demo_groups=groups)


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    nxt = str(form.get("next") or "/cabinet")
    nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else "/cabinet"
    try:
        person = await clients["sportsman"].post("/api/v1/auth/verify",
                                                 json={"email": form.get("email", ""), "password": form.get("password", "")})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(f"/login?next={nxt}", "error", humanize(exc.detail))
    role = form.get("role") if form.get("role") in person["roles"] else person["roles"][0]
    resp = back(nxt, "ok", f"Добро пожаловать, {person['first_name']}! Активная роль: {domain.ROLE_LABELS[role]}")
    write_session(resp, person["fsp_id"], role)
    return resp


@app.post("/logout")
async def logout():
    resp = back("/", "ok", "Вы вышли из аккаунта")
    clear_session(resp)
    return resp


@app.post("/role")
async def switch_role(request: Request):
    user = await require(request)
    role = (await request.form()).get("role")
    if role not in user["person"]["roles"]:
        raise HTTPException(403, "Эта роль не назначена вашему аккаунту")
    resp = back("/cabinet", "ok", f"Активная роль: {domain.ROLE_LABELS[role]} — права обновлены без повторного входа")
    write_session(resp, user["person"]["fsp_id"], role)
    return resp


# ---------------------------------------------------------------- cabinets
@app.get("/cabinet", response_class=HTMLResponse)
async def cabinet(request: Request):
    user = await require(request)
    return await {
        Role.ADMIN: _cabinet_admin, Role.TRAINER: _cabinet_trainer,
        Role.ATHLETE: _cabinet_athlete, Role.JUDGE: _cabinet_judge,
    }[user["role"]](request, user)


async def _cabinet_admin(request: Request, user: dict):
    degraded: list[str] = []
    sp, co = clients["sportsman"], clients["competition"]
    dash, stats, rank_alerts, judge_alerts, settings, comps, quals, judges, audit_sp, audit_co, comp_types, teams, evsk, \
        evsk_table = await asyncio.gather(
        safe(co.get("/api/v1/dashboard"), None, degraded),
        safe(sp.get("/api/v1/stats"), None, degraded),
        safe(sp.get("/api/v1/alerts/ranks", within_days=90), [], degraded),
        safe(sp.get("/api/v1/alerts/judge-categories", within_days=90), [], degraded),
        safe(co.get("/api/v1/rating/settings"), None, degraded),
        safe(co.get("/api/v1/competitions"), [], degraded),
        safe(sp.get("/api/v1/qualification-requests", status="PENDING"), [], degraded),
        safe(sp.get("/api/v1/persons", role="judge"), [], degraded),
        safe(sp.get("/api/v1/audit", limit=15), [], degraded),
        safe(co.get("/api/v1/audit", limit=15), [], degraded),
        safe(co.get("/api/v1/competition-types"), [], degraded),
        safe(co.get("/api/v1/teams"), [], degraded),
        safe(co.get("/api/v1/evsk/candidates"), {"rows": [], "ranks_checked": True}, degraded),
        safe(co.get("/api/v1/evsk/requirements"), [], degraded),
    )
    today = config.today()
    evsk_rows = [r for r in evsk["rows"] if r["state"] != "have"]
    evsk_map = {f"{r['competition_id']}:{r['fsp_id']}": r for r in evsk_rows}
    rank_pending = {q["person_fsp_id"] for q in quals if q["kind"] == "RANK"}
    for q in quals:
        years = domain.RANK_BY_CODE[q["code"]]["validity_years"] if q["kind"] == "RANK" else JUDGE_CATEGORY_DEFAULT_YEARS
        q["valid_until"] = _plus_years(today, years).isoformat() if years else None
    finished = [c for c in comps if c["status"] == "FINISHED"]
    panels = sorted([c for c in comps if c["status"] != "FINISHED"], key=lambda c: c["start_date"])
    for c in panels:
        c["has_chief"] = any(j["position"] == "Главный судья" for j in c["judges"])
    awaiting = sorted([c for c in comps if c["awaiting_results"]], key=lambda c: c["start_date"])
    registrations = sorted([c for c in comps if c["status"] in ("PLANNED", "REGISTRATION_OPEN", "REGISTRATION_CLOSED")
                            and c["days_to_start"] > 0], key=lambda c: c["start_date"])
    for c in registrations:
        keep = c["registration_deadline"] and not c["registration_expired"]
        c["suggest_deadline"] = c["registration_deadline"] if keep else (
            today + timedelta(days=min(14, max(c["days_to_start"] - 1, 0)))).isoformat()
    protocols, sheets = await asyncio.gather(
        asyncio.gather(*[safe(co.get(f"/api/v1/competitions/{c['id']}/protocol"), None, degraded) for c in finished]),
        asyncio.gather(*[safe(co.get(f"/api/v1/competitions/{c['id']}/protocol/entry-sheet"), None, degraded) for c in awaiting]),
    )
    doc_options = {
        p["competition"]["id"]: [{"fsp_id": e["fsp_id"], "name": e["full_name"], "place": e["place"], "status": e["status"]}
                                 for e in p["entries"] if e["fsp_id"].split("-")[1] == domain.REGION_CODE]
        for p in protocols if p
    }
    audit = sorted(audit_sp + audit_co, key=lambda a: a["at"], reverse=True)[:18]
    return render(request, "cabinet_admin.html", user=user, dash=dash, stats=stats, rank_alerts=rank_alerts,
                  judge_alerts=judge_alerts, settings=settings, finished=finished, doc_options=doc_options,
                  sheets=[s for s in sheets if s], audit=audit, sample_log=_sample_log(protocols),
                  quals=quals, panels=panels, judges=judges, registrations=registrations, comp_types=comp_types,
                  teams=teams, evsk_rows=evsk_rows, evsk_map=evsk_map, evsk_table=evsk_table,
                  evsk_checked=evsk["ranks_checked"], rank_pending=rank_pending, degraded=sorted(set(degraded)))


def _plus_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def _rank_basis_options(results: list[dict]) -> list[dict]:
    """Published results that can back a rank request (no DSQ): best place first, then newest."""
    ok = sorted((r for r in results if r["status"] == "OK" and r["place"]), key=lambda r: r["date"], reverse=True)
    return sorted(ok, key=lambda r: r["place"])


def _sample_log(protocols: list) -> str:
    """Pre-filled demo log for the 'upload protocol' form built from real registry IDs."""
    ids = [e["fsp_id"] for p in protocols if p and p["competition"]["discipline"] == "UAV"
           for e in p["entries"]][:4] or ["FSP-56-26-00023-0"]
    lines = ["#! FSP-LOG/1 competition=UPLOAD mode=score", "# судейские баллы за 3 миссии (сумма по участнику)"]
    for i, fid in enumerate(ids):
        for r in range(3):
            lines.append(f"0{r + 1}:{10 + i * 7:02d}:00 SCORE {fid} {31.5 - i * 2.75 - r * 0.5:.2f}")
    lines.append("01:00:00 RUN FSP-56-26-99999-1 A OK")
    return "\n".join(lines)


async def _cabinet_trainer(request: Request, user: dict):
    degraded: list[str] = []
    fid = user["person"]["fsp_id"]
    links, open_comps, apps, quals = await asyncio.gather(
        safe(clients["sportsman"].get(f"/api/v1/trainers/{fid}/athletes", with_performance=True), [], degraded),
        safe(clients["competition"].get("/api/v1/competitions", status="REGISTRATION_OPEN"), [], degraded),
        safe(clients["competition"].get("/api/v1/applications", submitted_by=fid), [], degraded),
        safe(clients["sportsman"].get("/api/v1/qualification-requests", submitted_by=fid, kind="RANK"), [], degraded),
    )
    active = [link for link in links if link["status"] == "ACTIVE"]
    for link in active:
        pf = link.get("performance") or {}
        link["rank_options"] = [r for r in _rank_basis_options(pf.get("results", [])) if r["discipline"] == link["discipline"]]
        link["rank_request"] = next((q for q in quals if q["person_fsp_id"] == link["athlete_fsp_id"]), None)
    alerts = sorted([link for link in active if link["athlete"]["rank"] and link["athlete"]["rank"]["state"] in ("expired", "critical", "warning")],
                    key=lambda link: link["athlete"]["rank"]["days_left"])
    alert_apps = await asyncio.gather(*(safe(clients["competition"].get("/api/v1/applications", athlete_fsp_id=link["athlete_fsp_id"]), [], degraded)
                                        for link in alerts))
    for link, athlete_apps in zip(alerts, alert_apps):
        link["active_apps"] = [a for a in athlete_apps if a["status"] != "REJECTED" and a["start_date"] >= config.today().isoformat()]
        rq = link.get("rank_request")
        link["handled"] = bool(rq and rq["status"] == "PENDING") or bool(link["active_apps"] and not link["rank_options"])
    open_comps = [c for c in open_comps if c["discipline"] in user["person"]["disciplines"]]
    return render(request, "cabinet_trainer.html", user=user, links=links, active=active, alerts=alerts,
                  open_comps=open_comps, apps=apps, quals=quals, degraded=sorted(set(degraded)))


async def _cabinet_athlete(request: Request, user: dict):
    degraded: list[str] = []
    fid = user["person"]["fsp_id"]
    perf, links, open_comps, apps, quals = await asyncio.gather(
        safe(clients["sportsman"].get(f"/api/v1/persons/{fid}/performance"), None, degraded),
        safe(clients["sportsman"].get(f"/api/v1/athletes/{fid}/links"), [], degraded),
        safe(clients["competition"].get("/api/v1/competitions", status="REGISTRATION_OPEN"), [], degraded),
        safe(clients["competition"].get("/api/v1/applications", athlete_fsp_id=fid), [], degraded),
        safe(clients["sportsman"].get("/api/v1/qualification-requests", person_fsp_id=fid, kind="RANK"), [], degraded),
    )
    applied = {a["competition_id"] for a in apps if a["status"] != "REJECTED"}
    age = user["person"]["age"]
    comps = []
    for c in open_comps:
        if c["discipline"] not in user["person"]["disciplines"]:
            continue
        lo, hi = domain.AGE_GROUPS[c["age_group"]]
        comps.append({**c, "age_ok": lo <= age <= hi, "applied": c["id"] in applied})
    # Journey stage per application: 1 подана → 2 допуск → 3 старт → 4 результат (then it lives in history)
    today = config.today().isoformat()
    with_result = {r["competition_id"] for r in perf["results"]} if perf and perf.get("available") else set()
    for a in apps:
        approved = a["status"] == "APPROVED"
        a["stage"] = 4 if a["competition_id"] in with_result else 3 if approved and a["start_date"] <= today else 2 if approved else 1
    in_progress = sorted([a for a in apps if a["stage"] < 4], key=lambda a: a["start_date"])
    return render(request, "cabinet_athlete.html", user=user, perf=perf, links=links, apps=apps, in_progress=in_progress,
                  can_apply=[c for c in comps if c["age_ok"] and not c["applied"]],
                  not_eligible=[c for c in comps if not c["age_ok"] and not c["applied"]],
                  quals=quals, rank_options=_rank_basis_options(perf["results"] if perf and perf.get("available") else []),
                  degraded=sorted(set(degraded)))


async def _cabinet_judge(request: Request, user: dict):
    degraded: list[str] = []
    fid = user["person"]["fsp_id"]
    assignments, quals = await asyncio.gather(
        safe(clients["competition"].get(f"/api/v1/judges/{fid}/assignments"), {"upcoming": [], "past": []}, degraded),
        safe(clients["sportsman"].get("/api/v1/qualification-requests", person_fsp_id=fid, kind="JUDGE_CATEGORY"), [], degraded),
    )
    return render(request, "cabinet_judge.html", user=user, assignments=assignments, quals=quals,
                  pending_qual=next((q for q in quals if q["status"] == "PENDING"), None), degraded=sorted(set(degraded)))


# ---------------------------------------------------------------- trainer / athlete actions
@app.post("/cabinet/links")
async def request_link(request: Request):
    user = await require(request, Role.TRAINER)
    form = await request.form()
    try:
        link = await clients["sportsman"].post("/api/v1/links", json={
            "trainer_fsp_id": user["person"]["fsp_id"], "athlete_fsp_id": str(form.get("athlete_fsp_id", "")).strip().upper(),
            "discipline": form.get("discipline", "")})
    except ServiceError as exc:
        return back("/cabinet", "error", humanize(exc.detail))
    await notify.notify(link["athlete_fsp_id"], "Запрос на привязку тренера",
                        f"{link['trainer_name']} · {link['discipline_label']}. Подтвердите в кабинете.", "/cabinet")
    return back("/cabinet", "ok", f"Запрос отправлен: {link['athlete']['full_name']} · {link['discipline_label']}. Ожидает подтверждения спортсменом.")


@app.post("/cabinet/links/{link_id}/status")
async def change_link(request: Request, link_id: int):
    user = await require(request, Role.TRAINER, Role.ATHLETE)
    form = await request.form()
    try:
        link = await clients["sportsman"].post(f"/api/v1/links/{link_id}/status", json={
            "status": form.get("status"), "actor_fsp_id": user["person"]["fsp_id"], "note": form.get("note", "")})
    except ServiceError as exc:
        return back("/cabinet", "error", humanize(exc.detail))
    other = link["trainer_fsp_id"] if user["role"] == Role.ATHLETE else link["athlete_fsp_id"]
    await notify.notify(other, "Связь тренер ↔ спортсмен", f"{link['discipline_label']}: {link['status_label']}", "/cabinet")
    return back("/cabinet", "ok", f"Связь #{link['id']}: {link['status_label']}", celebrate=link["status"] == "ACTIVE")


@app.post("/cabinet/applications")
async def submit_application(request: Request):
    user = await require(request, Role.TRAINER, Role.ATHLETE)
    form = await request.form()
    athlete = user["person"]["fsp_id"] if user["role"] == Role.ATHLETE else str(form.get("athlete_fsp_id", ""))
    try:
        app_ = await clients["competition"].post("/api/v1/applications", json={
            "competition_id": int(form.get("competition_id", 0)), "athlete_fsp_id": athlete,
            "submitted_by": user["person"]["fsp_id"]})
    except ServiceError as exc:
        return back("/cabinet", "error", humanize(exc.detail))
    if app_["athlete_fsp_id"] != user["person"]["fsp_id"]:
        await notify.notify(app_["athlete_fsp_id"], "Тренер подал заявку за вас", app_["competition_title"], "/cabinet")
    anchor = "#starts" if user["role"] == Role.ATHLETE else "#apps"
    return back(f"/cabinet{anchor}", "ok", f"Заявка #{app_['id']} подана: {app_['athlete_name']} → {app_['competition_title']}",
                celebrate=True, app_id=app_["id"])


_QUAL_ANCHOR = {Role.ATHLETE: "#todo", Role.TRAINER: "#ranks", Role.JUDGE: "#category"}


@app.post("/cabinet/qualification")
async def submit_qualification(request: Request):
    user = await require(request, Role.TRAINER, Role.ATHLETE, Role.JUDGE)
    form = await request.form()
    me, role = user["person"]["fsp_id"], user["role"]
    anchor = _QUAL_ANCHOR[role]
    payload = {"kind": "JUDGE_CATEGORY" if role == Role.JUDGE else "RANK", "person_fsp_id": me,
               "code": str(form.get("code", "")), "submitted_by": me, "note": str(form.get("note", ""))}
    if role != Role.JUDGE:
        fid, _, cid = str(form.get("basis", "")).partition("|")
        if role == Role.TRAINER:
            payload["person_fsp_id"] = fid
        payload["competition_id"] = int(cid) if cid.isdigit() else None
    try:
        q = await clients["sportsman"].post("/api/v1/qualification-requests", json=payload)
    except ServiceError as exc:
        return back(f"/cabinet{anchor}", "error", humanize(exc.detail))
    return back(f"/cabinet{anchor}", "ok", f"Заявка №{q['id']} на {q['purpose_label']}: {q['code_title']} — "
                                           f"{q['person']['short_name']}. Отправлена в федерацию, статус — в кабинете.",
                celebrate=True)


@app.post("/cabinet/telegram")
async def telegram_link(request: Request):
    user = await require(request)
    fid = user["person"]["fsp_id"]
    if not notify.enabled():
        return back("/cabinet", "error", "Telegram-уведомления не настроены")
    form = await request.form()
    if form.get("action") == "unlink":
        notify.unlink(fid)
        return back("/cabinet", "ok", "Telegram отключён")
    return RedirectResponse(notify.new_link(fid), status_code=303)


# ---------------------------------------------------------------- federation admin actions
@app.post("/cabinet/admin/qualification/{req_id}")
async def decide_qualification(request: Request, req_id: int):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        q = await clients["sportsman"].post(f"/api/v1/qualification-requests/{req_id}/decision", json={
            "status": form.get("status"), "actor": user["person"]["fsp_id"], "comment": form.get("comment", ""),
            "order_no": form.get("order_no", ""), "valid_until": form.get("valid_until") or None})
    except ServiceError as exc:
        return back("/cabinet#inbox", "error", humanize(exc.detail))
    done = "присвоено и внесено в профиль" if q["status"] == "APPROVED" else "заявка отклонена"
    await notify.notify_many({q["person"]["fsp_id"], q["submitted_by"]}, "Решение по заявке на разряд/категорию",
                             f"{q['code_title']}: {done}", "/cabinet")
    return back("/cabinet#inbox", "ok", f"{q['person']['short_name']}: {q['code_title']} — {done}")


@app.post("/cabinet/admin/competitions/{cid}/judges")
async def assign_judge(request: Request, cid: int):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        c = await clients["competition"].post(f"/api/v1/competitions/{cid}/judges", json={
            "fsp_id": form.get("fsp_id", ""), "position": form.get("position", ""), "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        return back(f"/cabinet#judges-{cid}", "error", humanize(exc.detail))
    j = c["judges"][-1]
    await notify.notify(j["fsp_id"], "Вас назначили судьёй", f"{c['title']} — {j['position'].lower()}", "/cabinet")
    return back(f"/cabinet#judges-{cid}", "ok", f"{j['full_name']} — {j['position'].lower()} на «{c['title']}». "
                                                 f"Назначение уже в кабинете судьи.")


@app.post("/cabinet/admin/competitions/{cid}/judges/{fsp_id}/remove")
async def remove_judge(request: Request, cid: int, fsp_id: str):
    user = await require(request, Role.ADMIN)
    try:
        await clients["competition"].request("DELETE", f"/api/v1/competitions/{cid}/judges/{fsp_id}",
                                             params={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        return back(f"/cabinet#judges-{cid}", "error", humanize(exc.detail))
    return back(f"/cabinet#judges-{cid}", "ok", "Судья снят с соревнования")



@app.post("/cabinet/admin/applications/{app_id}")
async def decide_application(request: Request, app_id: int):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        a = await clients["competition"].post(f"/api/v1/applications/{app_id}/decision", json={
            "status": form.get("status"), "actor": user["person"]["fsp_id"], "comment": form.get("comment", "")})
    except ServiceError as exc:
        return back("/cabinet", "error", humanize(exc.detail))
    await notify.notify_many({a["athlete_fsp_id"], a["submitted_by"]}, f"Заявка: {a['status_label']}",
                             a["competition_title"] + (f" · {a['comment']}" if a.get("comment") else ""), "/cabinet")
    return back("/cabinet", "ok", f"Заявка #{a['id']} ({a['athlete_name']}): {a['status_label']}")


@app.post("/cabinet/admin/rating-settings")
async def update_settings(request: Request):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        points = {lvl: [int(form.get(f"{lvl}_{i}", 0)) for i in range(3)] for lvl in domain.DEFAULT_POINTS_TABLE}
        await clients["competition"].put("/api/v1/rating/settings", json={
            "points": points, "coefficient": float(str(form.get("coefficient", "1.2")).replace(",", ".")),
            "min_podiums": int(form.get("min_podiums", 2)), "actor": user["person"]["fsp_id"]})
    except ValueError:
        return back("/cabinet#rating-settings", "error", "Баллы и коэффициент должны быть числами")
    except ServiceError as exc:
        return back("/cabinet#rating-settings", "error", humanize(exc.detail))
    return back("/cabinet#rating-settings", "ok", "Настройки рейтинга сохранены — рейтинг пересчитан автоматически")


@app.post("/cabinet/admin/protocol/{cid}/ingest")
async def ingest_protocol(request: Request, cid: int):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        p = await clients["competition"].post(f"/api/v1/competitions/{cid}/protocol/ingest",
                                              json={"log": form.get("log", ""), "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        return back("/cabinet#protocols", "error", humanize(exc.detail))
    warn = f" · предупреждений парсера: {len(p['warnings'])}" if p["warnings"] else ""
    return back("/cabinet#protocols", "ok", f"Протокол {p['competition']['code']} пересобран из лога: "
                                            f"{p['events']} событий, {len(p['entries'])} участников{warn}")


@app.post("/cabinet/admin/protocol/{cid}/results")
async def enter_results(request: Request, cid: int):
    user = await require(request, Role.ADMIN)
    form = await request.form()

    def num(key: str, fid: str) -> str:
        return str(form.get(f"{key}_{fid}") or "").strip().replace(",", ".") or "0"

    results = []
    try:
        for fid in form.getlist("fid"):
            if form.get(f"absent_{fid}"):
                continue
            results.append({"fsp_id": fid, "solved": int(num("solved", fid)), "penalty": int(num("penalty", fid)),
                            "score": float(num("score", fid)), "dsq": bool(form.get(f"dsq_{fid}")),
                            "dsq_reason": str(form.get(f"reason_{fid}", "")).strip()})
    except ValueError:
        return back("/cabinet#results-entry", "error", "Результаты должны быть числами: решено и штраф — целые, баллы — число")
    if not results:
        return back("/cabinet#results-entry", "error", "Внесите результат хотя бы одного участника")
    publish = form.get("action") == "publish"
    try:
        p = await clients["competition"].post(f"/api/v1/competitions/{cid}/protocol/results", json={
            "results": results, "actor": user["person"]["fsp_id"], "publish": publish})
    except ServiceError as exc:
        return back("/cabinet#results-entry", "error", humanize(exc.detail))
    if publish:
        return back(f"/protocols/{cid}", "ok", f"Протокол {p['competition']['code']} сформирован и опубликован: "
                                               f"{len(p['entries'])} участников → рейтинг и история спортсменов", celebrate=True)
    return back("/cabinet#results-entry", "ok", f"Черновик протокола {p['competition']['code']} пересчитан: "
                                                f"{len(p['entries'])} участников ранжированы автоматически")


@app.get("/protocols/{cid}", response_class=HTMLResponse)
async def protocol_page(request: Request, cid: int):
    user = await current_user(request)
    p = await clients["competition"].get(f"/api/v1/competitions/{cid}/protocol")
    if not p["published"] and not (user and user["role"] == Role.ADMIN):
        raise HTTPException(403, "Протокол ещё не опубликован — черновик доступен только федерации")
    c = p["competition"]
    judges = {j["position"]: j["full_name"] for j in reversed(c["judges"])}
    evsk: dict[str, dict] = {}
    if user and user["role"] == Role.ADMIN:  # подсветка выполнивших требования ЕВСК — только в экранной версии для федерации
        found = await safe(clients["competition"].get("/api/v1/evsk/candidates", competition_id=cid), {"rows": []}, [])
        evsk = {r["fsp_id"]: r for r in found["rows"] if r["state"] != "have"}
    return render(request, "protocol.html", user=user, p=p, c=c, judges=judges, evsk=evsk,
                  dsq_count=sum(e["status"] == "DSQ" for e in p["entries"]))


@app.post("/cabinet/admin/protocol/{cid}/publish")
async def publish_protocol(request: Request, cid: int):
    user = await require(request, Role.ADMIN)
    try:
        p = await clients["competition"].post(f"/api/v1/competitions/{cid}/protocol/publish", json={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        return back("/cabinet", "error", humanize(exc.detail))
    await notify.notify_many([e["fsp_id"] for e in p["entries"]], "Опубликован протокол",
                             p["competition"]["title"], f"/protocols/{cid}")
    return back("/cabinet", "ok", f"Протокол {p['competition']['code']} опубликован: результаты в рейтинге и истории спортсменов")


@app.post("/cabinet/admin/competitions")
async def create_competition(request: Request):
    user = await require(request, Role.ADMIN)
    data = await form_dict(request)
    data["actor"] = user["person"]["fsp_id"]
    try:
        c = await clients["competition"].post("/api/v1/competitions", json=data)
    except ServiceError as exc:
        return back("/cabinet#create", "error", humanize(exc.detail))
    if c["status"] == "REGISTRATION_OPEN":
        await notify.notify_many(notify.chats.keys(), "Открыта регистрация на соревнование", c["title"], "/calendar")
    return back("/cabinet", "ok", f"Соревнование {c['code']} создано · статус: {c['status_label']}")


@app.post("/cabinet/admin/competitions/{cid}/registration")
async def toggle_registration(request: Request, cid: int):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        c = await clients["competition"].post(f"/api/v1/competitions/{cid}/registration", json={
            "action": form.get("action", ""), "deadline": form.get("deadline") or None, "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        return back("/cabinet#registration", "error", humanize(exc.detail))
    if c["status"] == "REGISTRATION_OPEN":
        await notify.notify_many(notify.chats.keys(), "Открыт приём заявок",
                                 f"{c['title']} · до {ru_date(c['registration_deadline'])}", "/calendar")
        return back("/cabinet#registration", "ok", f"Приём заявок на {c['code']} открыт до {ru_date(c['registration_deadline'])}")
    return back("/cabinet#registration", "ok", f"Приём заявок на {c['code']} закрыт: заявок {c['applications_total']}, "
                                                f"на модерации {c['applications_pending']}")


@app.post("/cabinet/admin/competition-types")
async def add_competition_type(request: Request):
    user = await require(request, Role.ADMIN)
    title = " ".join(str((await request.form()).get("title", "")).split())
    try:
        await clients["competition"].post("/api/v1/competition-types", json={"title": title, "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        return back("/cabinet#create", "error", humanize(exc.detail))
    return back("/cabinet#create", "ok", f"Тип «{title}» добавлен в справочник — он доступен при создании соревнования")


@app.post("/cabinet/admin/competition-types/remove")
async def remove_competition_type(request: Request):
    user = await require(request, Role.ADMIN)
    title = str((await request.form()).get("title", ""))
    try:
        await clients["competition"].request("DELETE", "/api/v1/competition-types",
                                             params={"title": title, "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        return back("/cabinet#create", "error", humanize(exc.detail))
    return back("/cabinet#create", "ok", f"Тип «{title}» удалён из справочника")


# ---------------------------------------------------------------- выгрузки .xlsx / .docx / .pdf
def _known_format(fmt: str) -> None:
    if fmt not in exports.MEDIA_TYPES:
        raise HTTPException(404, "Формат выгрузки не поддерживается: доступны xlsx, docx и pdf")


async def download(doc: exports.Doc, fmt: str) -> Response:
    try:
        out = await asyncio.to_thread(exports.render, doc, fmt)
    except exports.ExportError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(out.content, media_type=out.media_type,
                    headers={"Content-Disposition": exports.content_disposition(out.filename)})


@app.get("/protocols/{cid}/export.{fmt}")
async def export_protocol(request: Request, cid: int, fmt: str):
    _known_format(fmt)
    user = await current_user(request)
    p = await clients["competition"].get(f"/api/v1/competitions/{cid}/protocol")
    if not p["published"] and not (user and user["role"] == Role.ADMIN):
        raise HTTPException(403, "Протокол ещё не опубликован — черновик доступен только федерации")
    judges = {j["position"]: j["full_name"] for j in reversed(p["competition"]["judges"])}
    return await download(reports.protocol_doc(p, judges), fmt)


@app.get("/athletes/{fid}/report.{fmt}")
async def export_athlete_report(request: Request, fid: str, fmt: str):
    _known_format(fmt)
    user = await require(request, Role.ADMIN, Role.ATHLETE, Role.TRAINER)
    me, role = user["person"]["fsp_id"], user["role"]
    if role == Role.ATHLETE and fid != me:
        raise HTTPException(403, "Отчёт о соревнованиях доступен самому спортсмену, его тренеру и федерации")
    if role == Role.TRAINER:
        links = await clients["sportsman"].get(f"/api/v1/athletes/{fid}/links")
        if not any(lk["trainer_fsp_id"] == me and lk["status"] == "ACTIVE" for lk in links):
            raise HTTPException(403, "Отчёт доступен тренеру только по подопечным с активной привязкой")
    person, perf = await asyncio.gather(clients["sportsman"].get(f"/api/v1/persons/{fid}"),
                                        clients["sportsman"].get(f"/api/v1/persons/{fid}/performance"))
    if "athlete" not in person["roles"]:
        raise HTTPException(422, "Отчёт о соревнованиях формируется только для спортсмена")
    if not perf.get("available"):
        raise HTTPException(503, "Сервис соревнований недоступен — история выступлений не получена")
    return await download(reports.athlete_report_doc(person, perf, config.today()), fmt)


def _headcount_query(years: int, discipline: str, municipality: str, organization: str) -> dict:
    return {"years": years, "discipline": discipline or None, "municipality": municipality or None,
            "organization": organization or None}


@app.get("/reports/headcount", response_class=HTMLResponse)
async def headcount_page(request: Request, years: int = 3, discipline: str = "", municipality: str = "", organization: str = ""):
    user = await require(request, Role.ADMIN)
    query = _headcount_query(years, discipline, municipality, organization)
    try:
        report = await clients["sportsman"].get("/api/v1/reports/headcount", **query)
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/reports/headcount", "error", humanize(exc.detail))
    qs = urlencode({k: v for k, v in query.items() if v})
    return render(request, "report_headcount.html", user=user, r=report, qs=qs, years=years,
                  filters={"discipline": discipline, "municipality": municipality, "organization": organization})


@app.get("/reports/headcount/export.{fmt}")
async def export_headcount(request: Request, fmt: str, years: int = 3, discipline: str = "", municipality: str = "",
                           organization: str = ""):
    _known_format(fmt)
    await require(request, Role.ADMIN)
    try:
        report = await clients["sportsman"].get("/api/v1/reports/headcount", **_headcount_query(years, discipline, municipality, organization))
    except ServiceError as exc:
        raise HTTPException(exc.status if exc.status < 500 else 503, humanize(exc.detail)) from exc
    chosen = [f"{label}: {value}" for label, value in (("дисциплина", domain.DISCIPLINE_BY_CODE.get(discipline, {}).get("short")),
                                                       ("муниципалитет", municipality), ("организация", organization)) if value]
    return await download(reports.headcount_doc(report, "; ".join(chosen) or "без фильтров"), fmt)


# ---------------------------------------------------------------- сборные команды и проект приказа
TEAMS_URL = "/teams"


@app.post("/cabinet/admin/teams")
async def create_team(request: Request):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    keep = {k: form.get(k) for k in ("discipline", "season", "age_group", "min_rank") if form.get(k)}
    fids = form.getlist("fids")
    if not fids:
        return back("/rating?" + urlencode(keep), "error", "Отметьте в таблице рейтинга хотя бы одного спортсмена для сборной")
    try:
        t = await clients["competition"].post("/api/v1/teams", json={
            "title": str(form.get("title", "")), "discipline": form.get("discipline", ""), "season": int(form.get("season", 0)),
            "purpose": str(form.get("purpose", "")), "age_group": form.get("age_group") or None,
            "min_rank": form.get("min_rank") or None, "fsp_ids": fids, "actor": user["person"]["fsp_id"]})
    except ValueError:
        return back("/rating?" + urlencode(keep), "error", "Сезон должен быть числом")
    except ServiceError as exc:
        return back("/rating?" + urlencode(keep), "error", humanize(exc.detail))
    return back(f"{TEAMS_URL}/{t['id']}", "ok", f"Сборная сформирована: {t['main_count']} спортсменов в основном составе. "
                                                  f"Проверьте состав, при необходимости измените его и скачайте документы.", celebrate=True)


@app.get("/teams", response_class=HTMLResponse)
async def teams_page(request: Request):
    user = await require(request, Role.ADMIN)
    degraded: list[str] = []
    teams = await safe(clients["competition"].get("/api/v1/teams"), [], degraded)
    return render(request, "teams.html", user=user, teams=teams, degraded=degraded)


@app.get("/teams/{tid}", response_class=HTMLResponse)
async def team_page(request: Request, tid: int):
    user = await require(request, Role.ADMIN)
    degraded: list[str] = []
    t = await clients["competition"].get(f"/api/v1/teams/{tid}")
    candidates: list[dict] = []
    if not t["locked"]:
        rating = await safe(clients["competition"].get("/api/v1/rating", discipline=t["discipline"], season=t["season"],
                                                       age_group=t["age_group"], min_rank=t["min_rank"]), None, degraded)
        taken = {m["fsp_id"] for m in t["members"]}
        candidates = [r for r in (rating["rows"] if rating else []) if r["fsp_id"] not in taken]
    return render(request, "team.html", user=user, t=t, candidates=candidates, degraded=degraded)


async def _team_action(request: Request, tid: int, call, ok: str):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    try:
        t = await call(user["person"]["fsp_id"], form)
    except ServiceError as exc:
        return back(f"{TEAMS_URL}/{tid}", "error", humanize(exc.detail))
    return back(f"{TEAMS_URL}/{tid}", "ok", ok.format(t=t))


@app.post("/cabinet/admin/teams/{tid}/update")
async def update_team(request: Request, tid: int):
    return await _team_action(request, tid, lambda actor, f: clients["competition"].put(f"/api/v1/teams/{tid}", json={
        "title": str(f.get("title", "")), "purpose": str(f.get("purpose", "")), "actor": actor}), "Название и цель сборной сохранены")


@app.post("/cabinet/admin/teams/{tid}/members")
async def add_team_member(request: Request, tid: int):
    return await _team_action(request, tid, lambda actor, f: clients["competition"].post(f"/api/v1/teams/{tid}/members", json={
        "fsp_id": str(f.get("fsp_id", "")), "actor": actor}), "Спортсмен добавлен в резерв — переведите его в основной состав при необходимости")


@app.post("/cabinet/admin/teams/{tid}/members/{fid}/remove")
async def remove_team_member(request: Request, tid: int, fid: str):
    return await _team_action(request, tid, lambda actor, f: clients["competition"].request(
        "DELETE", f"/api/v1/teams/{tid}/members/{fid}", params={"actor": actor}), "Спортсмен исключён из сборной")


@app.post("/cabinet/admin/teams/{tid}/members/{fid}/role")
async def set_team_member_role(request: Request, tid: int, fid: str):
    return await _team_action(request, tid, lambda actor, f: clients["competition"].post(
        f"/api/v1/teams/{tid}/members/{fid}/role", json={"role": str(f.get("role", "")), "actor": actor}), "Статус спортсмена в сборной изменён")


@app.post("/cabinet/admin/teams/{tid}/approve")
async def approve_team(request: Request, tid: int):
    return await _team_action(request, tid, lambda actor, f: clients["competition"].post(f"/api/v1/teams/{tid}/approve", json={
        "order_no": str(f.get("order_no", "")), "order_date": f.get("order_date") or None, "actor": actor}),
        "Состав утверждён приказом {t[order_no]} — изменения закрыты, документы содержат реквизиты приказа")


@app.post("/cabinet/admin/teams/{tid}/delete")
async def delete_team(request: Request, tid: int):
    user = await require(request, Role.ADMIN)
    try:
        await clients["competition"].request("DELETE", f"/api/v1/teams/{tid}", params={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        return back(f"{TEAMS_URL}/{tid}", "error", humanize(exc.detail))
    return back(TEAMS_URL, "ok", "Черновик сборной удалён")


@app.get("/teams/{tid}/{kind}.{fmt}")
async def export_team(request: Request, tid: int, kind: str, fmt: str):
    _known_format(fmt)
    if kind not in ("roster", "order"):
        raise HTTPException(404, "Документ не найден: доступны список сборной (roster) и проект приказа (order)")
    await require(request, Role.ADMIN)
    t = await clients["competition"].get(f"/api/v1/teams/{tid}")
    return await download(reports.team_roster_doc(t) if kind == "roster" else reports.team_order_doc(t), fmt)


# ---------------------------------------------------------------- documents (case Д10)
@app.post("/documents/participation")
async def generate_document(request: Request):
    user = await require(request, Role.ADMIN, Role.ATHLETE)
    form = await request.form()
    fsp_id = str(form.get("fsp_id", "")) if user["role"] == Role.ADMIN else user["person"]["fsp_id"]
    try:
        doc = await clients["competition"].post("/api/v1/documents/participation", json={
            "fsp_id": fsp_id, "competition_id": int(form.get("competition_id", 0)),
            "issued_by": user["person"]["fsp_id"], "purpose": form.get("purpose") or "для предъявления по месту требования"})
    except (ServiceError, ValueError) as exc:
        return back("/cabinet", "error", humanize(getattr(exc, "detail", "Выберите соревнование и участника")))
    return RedirectResponse(f"/documents/{doc['number']}", status_code=303)


@app.get("/documents/{number}", response_class=HTMLResponse)
async def document_page(request: Request, number: str):
    user = await require(request)
    doc = await clients["competition"].get(f"/api/v1/documents/{number}")
    if user["role"] != Role.ADMIN and doc["person"]["fsp_id"] != user["person"]["fsp_id"]:
        raise HTTPException(403, "Справка доступна владельцу и федерации")
    verify_url = config.PUBLIC_BASE_URL + doc["verify_path"]
    return render(request, "document.html", user=user, doc=doc, verify_url=verify_url, qr=qr_svg(verify_url))


# ---------------------------------------------------------------- allowlisted public API proxy
_PUBLIC_ROUTES = {
    "competition": re.compile(r"^api/v1/(competitions(/\d+(/protocol)?)?|rating(/settings)?|documents/[\w-]+/verify)$"),
    "sportsman": re.compile(r"^api/v1/stats$"),
}


@app.get("/api/public/{service}/{path:path}")
async def public_proxy(service: str, path: str, request: Request):
    pattern = _PUBLIC_ROUTES.get(service)
    if not pattern or not pattern.match(path):
        raise HTTPException(404, "Маршрут не опубликован в API-шлюзе")
    try:
        return await clients[service].get(f"/{path}", **dict(request.query_params))
    except ServiceError as exc:
        return JSONResponse({"detail": exc.detail, "service": exc.service}, status_code=exc.status)

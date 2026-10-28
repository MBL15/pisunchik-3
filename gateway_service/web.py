"""Общий слой страниц шлюза: клиенты сервисов, шаблоны и их фильтры, текущий пользователь, ответы, выгрузки.

Используется `main.py` и модулями маршрутов `gateway_service/routes/*`; сам маршрутов не объявляет.
"""
import asyncio
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, NoReturn

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from gateway_service import cabinet_search, exports, notify, tours
from gateway_service.listing_ui import qs
from gateway_service.session import FLASH_COOKIE, pop_flash, read_session, set_flash
from shared import config, domain
from shared.demo_roster import DEMO_PASSWORD
from shared.http import ServiceClient, ServiceError
from shared.listing import PAGE_SIZES

BASE_DIR = Path(__file__).parent
clients: dict[str, ServiceClient] = {}
templates = Jinja2Templates(directory=BASE_DIR / "templates")

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
templates.env.globals.update(D=domain, plural=plural, DEMO_PASSWORD=DEMO_PASSWORD, qs=qs, PAGE_SIZES=PAGE_SIZES, tg=notify,
                             finder_entries=cabinet_search.entries)


class Redirect(Exception):
    def __init__(self, url: str):
        self.url = url


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
            person, feed = await asyncio.gather(
                clients["sportsman"].get(f"/api/v1/persons/{s['fid']}"),
                safe(clients["sportsman"].get("/api/v1/notifications", fsp_id=s["fid"], limit=1), {"unread": 0}, []))
            role = s["role"] if s["role"] in person["roles"] else person["roles"][0]
            user = {"person": person, "role": role, "role_label": domain.ROLE_LABELS[role], "unread": feed["unread"]}
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


def safe_back(url: str, default: str) -> str:
    """Адрес возврата из формы — только относительный путь внутри сайта."""
    return url if url.startswith("/") and not url.startswith("//") else default


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


def reraise_for_page(exc: ServiceError) -> NoReturn:
    """Ошибка проверки в сервисе (неверный фильтр) — страницей 422 шлюза; недоступность сервиса пробрасывается как есть."""
    if exc.unavailable:
        raise exc
    raise HTTPException(422, humanize(exc.detail)) from exc


# ---------------------------------------------------------------- выгрузки .xlsx / .docx / .pdf
def known_format(fmt: str) -> None:
    if fmt not in exports.MEDIA_TYPES:
        raise HTTPException(404, "Формат выгрузки не поддерживается: доступны xlsx, docx и pdf")


async def download(doc: exports.Doc, fmt: str) -> Response:
    try:
        out = await asyncio.to_thread(exports.render, doc, fmt)
    except exports.ExportError as exc:
        raise HTTPException(503, str(exc)) from exc
    return Response(out.content, media_type=out.media_type,
                    headers={"Content-Disposition": exports.content_disposition(out.filename)})


# ---------------------------------------------------------------- адресаты уведомлений
async def organization_names() -> list[str]:
    """Все организации для списков выбора: справочник и клубы тренеров (их заводит сервис реестра, в константах шлюза их нет)."""
    return await safe(clients["sportsman"].get("/api/v1/organizations-names"), list(domain.ORGANIZATIONS), [])


async def admin_ids() -> list[str]:
    return [p["fsp_id"] for p in await safe(clients["sportsman"].get("/api/v1/persons", role="admin"), [], [])]


async def discipline_audience(discipline: str) -> list[str]:
    """Спортсмены и тренеры дисциплины — адресаты новостей об открытии приёма заявок."""
    athletes, trainers = await asyncio.gather(
        safe(clients["sportsman"].get("/api/v1/persons", role="athlete", discipline=discipline), [], []),
        safe(clients["sportsman"].get("/api/v1/persons", role="trainer", discipline=discipline), [], []))
    return [p["fsp_id"] for p in athletes + trainers]

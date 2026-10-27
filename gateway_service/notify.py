"""Уведомления в Telegram: привязка аккаунта через бота (/start <код>), отправка, напоминания о сроках.

Привязки (ФСП ID → chat_id) хранятся в `.telegram_links.json` в корне проекта, чтобы переживать live-reload.
Без `FSP_TELEGRAM_TOKEN` модуль ничего не делает.
"""
import asyncio
import html
import json
import logging
import secrets
import time
from pathlib import Path

import httpx

from shared import config

log = logging.getLogger("fsp.notify")

API = f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}"
STORE = Path(__file__).resolve().parent.parent / ".telegram_links.json"
CODE_TTL = 15 * 60
REMIND_DAYS = (30, 7, 1)

chats: dict[str, int] = {}  # fsp_id -> chat_id
_codes: dict[str, tuple[str, float]] = {}  # код -> (fsp_id, истекает)
_sent_reminders: set[str] = set()
_http: httpx.AsyncClient | None = None


def enabled() -> bool:
    return bool(config.TELEGRAM_TOKEN)


def is_linked(fsp_id: str) -> bool:
    return fsp_id in chats


def _save() -> None:
    STORE.write_text(json.dumps(chats, ensure_ascii=False), encoding="utf-8")


def _load() -> None:
    try:
        chats.update({k: int(v) for k, v in json.loads(STORE.read_text(encoding="utf-8")).items()})
    except (OSError, ValueError):
        pass


def new_link(fsp_id: str) -> str:
    """Одноразовая ссылка t.me/<бот>?start=<код> для привязки аккаунта."""
    now = time.time()
    for code in [c for c, (_, exp) in _codes.items() if exp < now]:
        del _codes[code]
    code = secrets.token_urlsafe(9)
    _codes[code] = (fsp_id, now + CODE_TTL)
    return f"https://t.me/{config.TELEGRAM_BOT}?start={code}"


def unlink(fsp_id: str) -> None:
    if chats.pop(fsp_id, None) is not None:
        _save()


async def _call(method: str, **params) -> dict | None:
    try:
        r = await _http.post(f"{API}/{method}", json=params, timeout=40)
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("telegram %s: %s", method, type(exc).__name__)
        return None
    if not data.get("ok"):
        log.warning("telegram %s: %s", method, data.get("description"))
        return None
    return data["result"]


async def _send(chat_id: int, text: str) -> bool:
    return await _call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                       disable_web_page_preview=True) is not None


async def notify(fsp_id: str, title: str, text: str = "", link: str = "") -> None:
    """Отправить уведомление человеку, если он привязал Telegram. Ошибки доставки не ломают основной сценарий."""
    chat = chats.get(fsp_id)
    if not (enabled() and chat and _http):
        return
    body = f"<b>{html.escape(title)}</b>" + (f"\n{html.escape(text)}" if text else "")
    if link:
        body += f"\n{config.PUBLIC_BASE_URL}{link}"
    await _send(chat, body)


async def notify_many(fsp_ids, title: str, text: str = "", link: str = "") -> None:
    await asyncio.gather(*(notify(f, title, text, link) for f in set(fsp_ids)))


async def _handle(message: dict) -> None:
    chat_id, text = message["chat"]["id"], (message.get("text") or "").strip()
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0]
    if cmd == "/start" and arg:
        fsp_id, exp = _codes.pop(arg.strip(), (None, 0))
        if fsp_id and exp > time.time():
            chats[fsp_id] = chat_id
            _save()
            await _send(chat_id, f"✅ Уведомления подключены к аккаунту <b>{html.escape(fsp_id)}</b>.\n"
                                 "Буду писать о заявках, протоколах, назначениях и сроках. /stop — отключить.")
        else:
            await _send(chat_id, "Ссылка устарела. Откройте кабинет на платформе и нажмите «Подключить Telegram» ещё раз.")
    elif cmd == "/stop":
        for fid in [f for f, c in chats.items() if c == chat_id]:
            unlink(fid)
        await _send(chat_id, "Уведомления отключены.")
    else:
        await _send(chat_id, "Это бот ФСП Оренбург. Чтобы получать уведомления, подключите Telegram в личном кабинете платформы.")


async def poll_updates() -> None:
    """Long polling getUpdates: бот принимает /start <код> и /stop."""
    await _call("deleteWebhook")
    offset = 0
    while True:
        updates = await _call("getUpdates", offset=offset, timeout=25, allowed_updates=["message"])
        if updates is None:
            await asyncio.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            if msg := u.get("message"):
                try:
                    await _handle(msg)
                except Exception:  # noqa: BLE001 — один плохой апдейт не должен останавливать бота
                    log.exception("telegram update failed")


async def remind_loop(fetch_deadlines) -> None:
    """Раз в час: напоминания за 30/7/1 день. `fetch_deadlines()` -> [(fsp_id, ключ, заголовок, текст, дней)]."""
    while True:
        try:
            for fsp_id, key, title, text, days in await fetch_deadlines():
                mark = f"{fsp_id}|{key}|{days}"
                if days in REMIND_DAYS and mark not in _sent_reminders and is_linked(fsp_id):
                    _sent_reminders.add(mark)
                    await notify(fsp_id, title, text, "/cabinet")
        except Exception:  # noqa: BLE001
            log.exception("reminder pass failed")
        await asyncio.sleep(3600)


async def start(fetch_deadlines) -> list[asyncio.Task]:
    global _http
    if not enabled():
        return []
    _load()
    _http = httpx.AsyncClient()
    return [asyncio.create_task(poll_updates()), asyncio.create_task(remind_loop(fetch_deadlines))]


async def stop(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        t.cancel()
    if _http:
        await _http.aclose()

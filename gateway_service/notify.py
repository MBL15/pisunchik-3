"""Уведомления: лента в личном кабинете (всегда) и Telegram (внешний канал), напоминания о сроках.

Лента хранится в sportsman_service (`/api/v1/notifications`); шлюз передаёт сюда функцию записи при старте.
Telegram: привязка аккаунта через бота (/start <код>); привязки (ФСП ID → chat_id) лежат в `.telegram_links.json`
в корне проекта, чтобы переживать live-reload. Без `FSP_TELEGRAM_TOKEN` работает только лента.
Если api.telegram.org недоступен с сервера, задайте `FSP_TELEGRAM_PROXY` (например, socks5://host.docker.internal:1080 — обратный SSH-туннель).
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
_http: httpx.AsyncClient | None = None
# async (fsp_ids, title, text, link, key) -> адресаты, кому запись в ленту добавлена (с key — без повторов)
_record = None


def enabled() -> bool:
    return bool(config.TELEGRAM_TOKEN)


def make_client() -> httpx.AsyncClient:
    """HTTP-клиент для Bot API; через `FSP_TELEGRAM_PROXY`, если задан. Учётные данные прокси в журнал не попадают."""
    proxy = config.TELEGRAM_PROXY.strip() or None
    if proxy:
        log.info("telegram: через прокси %s", httpx.URL(proxy).copy_with(username=None, password=None))
    return httpx.AsyncClient(proxy=proxy)


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
    except Exception as exc:  # noqa: BLE001 — любой сбой сети или прокси (в т. ч. не из httpx) не должен убивать опрос бота
        log.warning("telegram %s: %s", method, type(exc).__name__)
        return None
    if not data.get("ok"):
        log.warning("telegram %s: %s", method, data.get("description"))
        return None
    return data["result"]


async def _send(chat_id: int, text: str) -> bool:
    return await _call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                       disable_web_page_preview=True) is not None


async def _telegram(fsp_id: str, title: str, text: str, link: str) -> None:
    chat = chats.get(fsp_id)
    if not (enabled() and chat and _http):
        return
    body = f"<b>{html.escape(title)}</b>" + (f"\n{html.escape(text)}" if text else "")
    if link:
        body += f"\n{config.PUBLIC_BASE_URL}{link}"
    await _send(chat, body)


async def notify_many(fsp_ids, title: str, text: str = "", link: str = "", key: str = "") -> None:
    """Уведомление в ленту кабинета и в Telegram (кто подключил). Ошибки доставки не ломают основной сценарий.

    С `key` (напоминания) человек получает уведомление один раз: в Telegram уходит только то, что впервые попало в ленту.
    """
    ids = list(dict.fromkeys(f for f in fsp_ids if f))
    if not ids:
        return
    delivered = ids
    if _record:
        try:
            delivered = await _record(ids, title, text, link, key)
        except Exception as exc:  # noqa: BLE001 — лента недоступна, но действие пользователя уже выполнено
            log.warning("notification feed: %s", exc)
            delivered = [] if key else ids
    await asyncio.gather(*(_telegram(f, title, text, link) for f in delivered))


async def notify(fsp_id: str, title: str, text: str = "", link: str = "", key: str = "") -> None:
    await notify_many([fsp_id], title, text, link, key)


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
    offset, webhook_cleared = 0, False
    while True:
        try:
            if not webhook_cleared:  # пока Telegram недоступен, повторяем — без снятого вебхука getUpdates не работает
                webhook_cleared = await _call("deleteWebhook") is not None
            updates = await _call("getUpdates", offset=offset, timeout=25, allowed_updates=["message"])
        except Exception:  # noqa: BLE001 — опрос живёт, пока работает шлюз
            log.exception("telegram polling failed")
            updates = None
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


def remind_bucket(days: int) -> str:
    """Порог напоминания: срок прошёл, ≤1, ≤7 или ≤30 дней. На каждый порог — одно уведомление."""
    if days < 0:
        return "expired"
    return f"d{next(d for d in sorted(REMIND_DAYS) if days <= d)}" if days <= max(REMIND_DAYS) else ""


async def remind_pass(fetch_deadlines) -> None:
    """`fetch_deadlines()` -> [(fsp_id, ключ, заголовок, текст, дней, ссылка)]; напоминает при входе в каждый порог.

    Одинаковые напоминания (общий срок: отчёт, конец приёма заявок) уходят одной рассылкой на всех адресатов.
    """
    groups: dict[tuple[str, str, str, str], list[str]] = {}
    for fsp_id, key, title, text, days, link in await fetch_deadlines():
        if bucket := remind_bucket(days):
            groups.setdefault((f"{key}|{bucket}", title, text, link), []).append(fsp_id)
    for (key, title, text, link), fsp_ids in groups.items():
        await notify_many(fsp_ids, title, text, link, key=key)


async def remind_loop(fetch_deadlines) -> None:
    """Раз в час (при сбое — через минуту) проверяет сроки."""
    while True:
        try:
            await remind_pass(fetch_deadlines)
            pause = 3600
        except Exception:  # noqa: BLE001
            log.exception("reminder pass failed")
            pause = 60
        await asyncio.sleep(pause)


async def start(fetch_deadlines, record) -> list[asyncio.Task]:
    global _http, _record
    _record = record
    tasks = [asyncio.create_task(remind_loop(fetch_deadlines))]
    if enabled():
        _load()
        _http = make_client()
        tasks.append(asyncio.create_task(poll_updates()))
    return tasks


async def stop(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        t.cancel()
    if _http:
        await _http.aclose()

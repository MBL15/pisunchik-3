"""Ограничение частоты неудачных входов: перебор пароля упирается во временную блокировку пары «адрес + почта».

Счётчик хранится в памяти шлюза (как и остальное состояние демо); блокировка снимается сама по истечении окна,
успешный вход обнуляет счётчик. Почту нормализуем, чтобы регистром и пробелами лимит не обойти.
"""
import time

WINDOW = 300     # секунд, в течение которых считаются неудачи
LIMIT = 8        # неудач до блокировки
_fails: dict[str, list[float]] = {}


def key(ip: str, email: str) -> str:
    return f"{ip}|{email.strip().casefold()}"


def _recent(k: str, now: float) -> list[float]:
    recent = [t for t in _fails.get(k, []) if now - t < WINDOW]
    if recent:
        _fails[k] = recent
    else:
        _fails.pop(k, None)
    return recent


def retry_after(k: str, now: float | None = None) -> int:
    """Сколько секунд осталось до разблокировки; 0 — вход разрешён."""
    now = time.time() if now is None else now
    recent = _recent(k, now)
    return int(WINDOW - (now - recent[0])) + 1 if len(recent) >= LIMIT else 0


def failed(k: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    if len(_fails) > 10_000:  # защита памяти от потока разных адресов
        for old in list(_fails):
            _recent(old, now)
    _recent(k, now)
    _fails.setdefault(k, []).append(now)


def succeeded(k: str) -> None:
    _fails.pop(k, None)

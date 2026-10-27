"""Поиск, сортировка, фасеты и постраничная выдача для больших списков (реестр, заявки, журнал действий).

Поиск — по словам: каждое слово запроса должно встретиться в строке поиска записи (регистр и «ё» не важны),
поэтому «иван орен» находит «Иванов … г. Оренбург». Ответ страницы — `{total, offset, limit, items}`.
"""
from collections import Counter
from collections.abc import Callable, Iterable

MAX_LIMIT = 500
PAGE_SIZES = (25, 50, 100, 200)


def norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def tokens(q: str | None) -> list[str]:
    return norm(q or "").split()


def matches(words: list[str], *fields) -> bool:
    if not words:
        return True
    hay = norm(" ".join(str(f) for f in fields if f))
    return all(w in hay for w in words)


def page(items: list, offset: int, limit: int, **extra) -> dict:
    offset = max(offset, 0)
    limit = min(max(limit, 1), MAX_LIMIT)
    return {"total": len(items), "offset": offset, "limit": limit, "items": items[offset:offset + limit], **extra}


def facets(rows: Iterable, keys: dict[str, Callable]) -> dict[str, dict[str, int]]:
    """Число записей по значениям каждого ключа; функция ключа может вернуть одно значение или список."""
    out: dict[str, Counter] = {k: Counter() for k in keys}
    for r in rows:
        for k, fn in keys.items():
            v = fn(r)
            for x in (v if isinstance(v, (list, tuple, set)) else [v]):
                if x is not None and x != "":
                    out[k][x] += 1
    return {k: dict(c) for k, c in out.items()}


AUDIT_LIMIT = 5000  # сколько последних записей журнала держит каждый сервис


def search_audit(entries: list[dict], q: str | None = None, actor: str | None = None, date_from=None, date_to=None) -> list[dict]:
    """Записи журнала по словам (действие, объект, автор), автору и датам включительно; порядок сохраняется."""
    words, lo, hi = tokens(q), date_from and date_from.isoformat(), date_to and date_to.isoformat()
    return [a for a in entries
            if matches(words, a["action"], a["object"], a["actor"])
            and (not actor or a["actor"] == actor)
            and (not lo or a["at"][:10] >= lo) and (not hi or a["at"][:10] <= hi)]

"""Состояние больших списков в адресе страницы: параметры, страницы, сортировка и чипы активных фильтров.

Фильтрация и выборка страниц — в сервисах (`shared/listing.py`); здесь только то, что нужно шаблонам.
"""
from collections.abc import Callable
from urllib.parse import urlencode

from fastapi import Request

from shared.listing import PAGE_SIZES


def qs(request: Request, **changes) -> str:
    """Текущий адрес с изменёнными параметрами: пустое значение убирает параметр; любой фильтр, кроме страницы, сбрасывает её."""
    params = dict(request.query_params)
    if any(k != "page" for k in changes):
        params.pop("page", None)
    for k, v in changes.items():
        if v in (None, "", False):
            params.pop(k, None)
        else:
            params[k] = str(v)
    return f"{request.url.path}?{urlencode(params)}" if params else request.url.path


def pages(result: dict) -> dict:
    """Пагинатор для шаблона по ответу `{total, offset, limit}`: номер страницы, всего, показанный диапазон, окно номеров."""
    total, limit, offset = result["total"], result["limit"], result["offset"]
    count = max(1, -(-total // limit))
    current = min(offset // limit + 1, count)
    window = sorted({1, count, *range(max(1, current - 2), min(count, current + 2) + 1)})
    return {"total": total, "limit": limit, "page": current, "pages": count, "window": window,
            "first": offset + 1 if total else 0, "last": min(offset + limit, total)}


def list_args(request: Request, sorts: tuple[str, ...], default_sort: str, default_order: str = "asc") -> dict:
    """Страница, размер, сортировка из адреса; размер страницы ограничен `PAGE_SIZES`."""
    p = request.query_params
    per = int(p["per"]) if p.get("per", "").isdigit() and int(p["per"]) in PAGE_SIZES else PAGE_SIZES[1]
    page_no = int(p["page"]) if p.get("page", "").isdigit() and int(p["page"]) > 0 else 1
    sort = p.get("sort") if p.get("sort") in sorts else default_sort
    order = p.get("order") if p.get("order") in ("asc", "desc") else default_order
    return {"sort": sort, "order": order, "limit": per, "offset": (page_no - 1) * per}


def chips(request: Request, filters: dict, labels: dict[str, Callable] | None = None,
          defaults: dict[str, str] | None = None) -> tuple[list[tuple[str, str]], str]:
    """Чипы активных фильтров `[(подпись, адрес без него)]` и адрес «сбросить всё».

    `labels` — подпись значения по ключу (по умолчанию само значение); `defaults` — значение, которое фильтром не считается
    и подставляется при снятии (например, статус `all`).
    """
    labels, defaults = labels or {}, defaults or {}
    active = [(k, v) for k, v in filters.items() if v and v != defaults.get(k)]
    items = [(labels.get(k, str)(v), qs(request, **{k: defaults.get(k)})) for k, v in active]
    return items, qs(request, **{k: defaults.get(k) for k in filters})

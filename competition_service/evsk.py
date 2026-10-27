"""Соответствие результата требованиям ЕВСК (подсветка в протоколе и кабинете федерации). Чистые функции, без I/O.

Результат подходит под разряд, если на соревновании уровня L спортсмен занял место не ниже N, а в протоколе
не менее M участников (таблица — `shared.domain.EVSK_REQUIREMENTS`). Дисквалификация результата не даёт.
"""
from shared.domain import EVSK_REQUIREMENTS, LEVEL_LABELS, RANK_BY_CODE, RANK_ORDER


def requirement_text(level: str, max_place: int, min_participants: int) -> str:
    place = "1 место" if max_place == 1 else f"место не ниже {max_place}-го"
    return f"{LEVEL_LABELS[level]}: {place}, участников не менее {min_participants}"


def met_ranks(level: str, place: int | None, status: str, participants: int) -> list[dict]:
    """Все разряды (по возрастанию), требования которых выполнены этим результатом."""
    if status != "OK" or not place:
        return []
    out = []
    for code, rules in EVSK_REQUIREMENTS.items():
        rule = next(((lvl, mp, mn) for lvl, mp, mn in rules if lvl == level and place <= mp and participants >= mn), None)
        if rule:
            out.append({"code": code, "short": RANK_BY_CODE[code]["short"], "title": RANK_BY_CODE[code]["title"],
                        "requirement": requirement_text(*rule)})
    return sorted(out, key=lambda r: RANK_ORDER[r["code"]])


def classify(target_code: str, held: dict | None) -> str:
    """assign — выше текущего разряда; confirm — совпадает с текущим, срок которого истекает или истёк; have — иначе."""
    if not held:
        return "assign"
    if RANK_ORDER[target_code] > RANK_ORDER[held["code"]]:
        return "assign"
    if RANK_ORDER[target_code] == RANK_ORDER[held["code"]] and held.get("state") in ("expired", "critical", "warning"):
        return "confirm"
    return "have"

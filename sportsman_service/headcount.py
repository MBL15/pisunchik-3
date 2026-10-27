"""Отчёт о численности занимающихся с динамикой по годам и разрезами. Чистые функции — без I/O.

Занимающийся на конец года Y — спортсмен реестра с `start_year` ≤ Y (данных о выбытии в реестре нет).
Для текущего года отчётная дата — сегодня, для прошлых — 31 декабря. Возраст и разряд берутся на отчётную дату;
разряд — по текущей записи профиля (истории присвоений реестр не хранит), поэтому в прошлых годах он занижен.
"""
from datetime import date

from shared.domain import DISCIPLINE_BY_CODE, DISCIPLINES, GENDER_LABELS, HEADCOUNT_AGE_BANDS, RANK_BY_CODE, RANK_ORDER, Role
from sportsman_service.models import PersonRecord
from sportsman_service.store import age_on

NO_RANK = "без разряда"


def _age_band(p: PersonRecord, on: date) -> list[str]:
    age = age_on(p.birth_date, on)
    return [next(label for label, lo, hi in HEADCOUNT_AGE_BANDS if lo <= age <= hi)]


def _rank(p: PersonRecord, on: date) -> list[str]:
    r = p.rank
    held = r and r.assigned_at <= on and (r.expires_at is None or r.expires_at >= on)
    return [RANK_BY_CODE[r.code]["short"] if held else NO_RANK]


# (ключ, заголовок, функция разреза → подписи строк, в которые попадает спортсмен, порядок строк или None → по убыванию)
def _cuts() -> list[tuple[str, str, object, list[str] | None]]:
    ranks = [NO_RANK] + [r["short"] for r in sorted(RANK_BY_CODE.values(), key=lambda r: RANK_ORDER[r["code"]])]
    return [
        ("discipline", "По дисциплинам", lambda p, on: [DISCIPLINE_BY_CODE[d]["short"] for d in p.disciplines],
         [d["short"] for d in DISCIPLINES]),
        ("municipality", "По муниципальным образованиям", lambda p, on: [p.municipality], None),
        ("organization", "По организациям", lambda p, on: [p.organization], None),
        ("age", "По возрастным группам", _age_band, [label for label, _, _ in HEADCOUNT_AGE_BANDS]),
        ("gender", "По полу", lambda p, on: [GENDER_LABELS[p.gender]], list(GENDER_LABELS.values())),
        ("rank", "По спортивным разрядам", _rank, ranks),
    ]


def _delta(counts: list[int]) -> tuple[int, float | None]:
    diff = counts[-1] - counts[0]
    return diff, round(diff * 100 / counts[0], 1) if counts[0] else None


def build_report(persons: list[PersonRecord], today: date, years: int = 3, discipline: str | None = None,
                 municipality: str | None = None, organization: str | None = None) -> dict:
    athletes = [p for p in persons if Role.ATHLETE in p.roles
                and (not discipline or discipline in p.disciplines)
                and (not municipality or p.municipality == municipality)
                and (not organization or p.organization == organization)]
    span = list(range(today.year - years + 1, today.year + 1))
    as_of = [min(date(y, 12, 31), today) for y in span]
    enrolled = [[p for p in athletes if p.start_year <= y] for y in span]

    cuts = []
    for key, title, labels_of, order in _cuts():
        table: dict[str, list[int]] = {}
        for i, (group, on) in enumerate(zip(enrolled, as_of)):
            for p in group:
                for label in labels_of(p, on):
                    table.setdefault(label, [0] * len(span))[i] += 1
        names = [n for n in order if n in table] if order else sorted(table, key=lambda n: (-table[n][-1], n))
        rows = []
        for name in names:
            diff, pct = _delta(table[name])
            rows.append({"label": name, "counts": table[name], "delta": diff, "delta_pct": pct})
        cuts.append({"key": key, "title": title, "rows": rows,
                     "note": "спортсмен учитывается в каждой своей дисциплине — сумма может превышать итог" if key == "discipline" else ""})

    totals = [len(g) for g in enrolled]
    diff, pct = _delta(totals)
    return {
        "years": span, "as_of": [d.isoformat() for d in as_of], "generated_on": today.isoformat(),
        "filters": {"discipline": discipline, "municipality": municipality, "organization": organization},
        "total": {"counts": totals, "delta": diff, "delta_pct": pct,
                  "joined": [sum(p.start_year == y for p in athletes) for y in span]},
        "cuts": cuts,
    }

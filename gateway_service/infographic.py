"""Инфографика кабинета министерства: отчёты сервисов → геометрия SVG-графиков. Чистые функции — без I/O.

Источники те же, что у страницы показателей: отчёт о численности (динамика по годам и разрезы), статистика реестра и
соревнования по годам из календаря. Каждый блок собирается независимо: если источник недоступен, блока просто нет.
"""
from math import ceil, pi

W, H = 480, 260
PAD_L, PAD_R, PAD_T, PAD_B = 36, 12, 30, 32
MONTHS = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
PALETTE = ("#8C80FF", "#402FFF", "#34d399", "#fbbf24", "#fb7185", "#9B9C9E")


def _scale(top_value: float) -> tuple[int, int]:
    """(верх шкалы, шаг делений): не более четырёх делений, шаг 1/2/5 × 10ⁿ."""
    if top_value <= 0:
        return 4, 1
    mag = 1
    while True:
        for f in (1, 2, 5):
            step = f * mag
            if top_value / step <= 4:
                return ceil(top_value / step) * step, step
        mag *= 10


def combo_chart(labels: list, bars: list[int], line: list[int] | None = None) -> dict:
    """Столбцы по годам (+ линия поверх на той же шкале). Координаты — в пикселях viewBox."""
    n = len(labels)
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    top, step = _scale(max([*bars, *(line or [])], default=0))
    k = plot_h / top
    slot = plot_w / n
    bw = min(slot * 0.56, 56)
    base = PAD_T + plot_h
    cols = []
    for i, (label, v) in enumerate(zip(labels, bars)):
        x = PAD_L + slot * i + (slot - bw) / 2
        cols.append({"label": label, "value": v, "x": round(x, 1), "w": round(bw, 1), "h": round(v * k, 1), "y": round(base - v * k, 1),
                     "cx": round(x + bw / 2, 1), "delay": i * 70})
    dots = [{"x": c["cx"], "y": round(base - lv * k, 1), "value": lv, "label": c["label"]} for c, lv in zip(cols, line or [])]
    return {
        "w": W, "h": H, "pad_l": PAD_L, "pad_r": PAD_R, "base": base, "bars": cols, "dots": dots,
        "points": " ".join(f"{d['x']},{d['y']}" for d in dots),
        "ticks": [{"y": round(base - v * k, 1), "label": v} for v in range(0, top + 1, step)],
        "aria": "; ".join(f"{label}: {v}" for label, v in zip(labels, bars)),
    }


def donut(items: list[tuple[str, int]]) -> dict | None:
    """Кольцо из долей: каждая доля — дуга окружности (stroke-dasharray), цвет дублируется подписью и процентом."""
    items = [(label, v) for label, v in items if v > 0]
    total = sum(v for _, v in items)
    if not total:
        return None
    r = 40
    c = 2 * pi * r
    segments, acc = [], 0.0
    for i, (label, v) in enumerate(items):
        frac = v / total
        segments.append({"label": label, "value": v, "pct": round(frac * 100), "color": PALETTE[i % len(PALETTE)],
                         "dash": round(frac * c, 2), "gap": round(c - frac * c, 2), "offset": round(-acc * c, 2)})
        acc += frac
    return {"r": r, "c": round(c, 2), "total": total, "segments": segments}


def hbars(rows: list[tuple[str, int]], limit: int | None = None) -> dict | None:
    """Горизонтальные полосы: ширина — доля от максимума, рядом — доля от суммы."""
    rows = [(label, v) for label, v in rows if v > 0]
    if not rows:
        return None
    total, peak = sum(v for _, v in rows), max(v for _, v in rows)
    shown = rows[:limit] if limit else rows
    return {"total": total, "rest": sum(v for _, v in rows[limit:]) if limit else 0,
            "rows": [{"label": label, "value": v, "width": round(v * 100 / peak), "share": round(v * 100 / total)} for label, v in shown]}


def growth(years: list, counts: list[int]) -> list[dict]:
    """Прирост к предыдущему году: абсолют и процент (None, если в прошлом году было 0)."""
    out = []
    for i, (y, n) in enumerate(zip(years, counts)):
        prev = counts[i - 1] if i else None
        out.append({"year": y, "count": n, "delta": None if prev is None else n - prev,
                    "pct": None if not prev else round((n - prev) * 100 / prev, 1)})
    return out


def _cut(headcount: dict, key: str) -> list[tuple[str, int]]:
    cut = next((c for c in headcount["cuts"] if c["key"] == key), None)
    return [(r["label"], r["counts"][-1]) for r in cut["rows"]] if cut else []


def _competition_blocks(comp_years: list[dict]) -> dict:
    """Соревнования и участники: по годам, а если календарь заполнен меньше чем за два года — по месяцам последнего года."""
    active = [c for c in comp_years if c["kpi"]["total"]]
    if len(active) >= 2:
        labels = [c["year"] for c in comp_years]
        return {"comp_period": {"mode": "years", "year": None},
                "participants": combo_chart(labels, [c["kpi"]["participants"] for c in comp_years]),
                "competitions": combo_chart(labels, [c["kpi"]["held"] for c in comp_years], [c["kpi"]["total"] for c in comp_years])}
    if not active:
        return {}
    year = active[-1]
    planned, held, people = [0] * 12, [0] * 12, [0] * 12
    for c in year["items"]:
        i = int(c["start_date"][5:7]) - 1
        planned[i] += 1
        if c["status"] == "FINISHED":
            held[i] += 1
            people[i] += c["participants"]
    return {"comp_period": {"mode": "months", "year": year["year"]},
            "participants": combo_chart(MONTHS, people), "competitions": combo_chart(MONTHS, held, planned)}


def build(headcount: dict | None, stats: dict | None, comp_years: list[dict]) -> dict:
    """Все блоки инфографики. `comp_years` — ответы `/development/competitions` по годам (в порядке возрастания; пустые отброшены)."""
    out: dict = {}
    if headcount:
        years, total = headcount["years"], headcount["total"]
        out["athletes"] = combo_chart(years, total["counts"], total["joined"])
        out["growth"] = growth(years, total["counts"])
        out["period"] = {"first": years[0], "last": years[-1], "start": total["counts"][0], "end": total["counts"][-1],
                         "delta": total["delta"], "pct": total["delta_pct"], "joined": sum(total["joined"])}
        out["disciplines"] = donut(_cut(headcount, "discipline"))
        out["municipalities"] = hbars(_cut(headcount, "municipality"), limit=8)
        out["age"] = hbars(_cut(headcount, "age"))
        out["gender"] = hbars(_cut(headcount, "gender"))
        out["stages"] = hbars(_cut(headcount, "stage"))
    out.update(_competition_blocks(comp_years))
    if stats and stats.get("athletes"):
        out["ranked"] = {"with_rank": stats["with_rank"], "athletes": stats["athletes"],
                         "pct": round(stats["with_rank"] * 100 / stats["athletes"])}
    return out

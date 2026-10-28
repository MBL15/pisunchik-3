"""Seasonal rating engine (case §6). Pure functions — no I/O — so it is trivially unit-testable.

    rating = Σ base_points(level, place)            over all published protocols of the season
    × stability coefficient (default 1.2)           if podium (1–3, not DSQ) in ≥ min_podiums competitions
    athletes with an active disqualification / suspension are excluded from the table entirely.
"""
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from shared.domain import (
    DEFAULT_POINTS_TABLE, DEFAULT_STABILITY_COEFFICIENT, DEFAULT_STABILITY_MIN_PODIUMS, LEVEL_LABELS, REGION_CODE,
)


@dataclass
class RatingSettings:
    points: dict[str, list[int]] = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_POINTS_TABLE.items()})
    coefficient: float = DEFAULT_STABILITY_COEFFICIENT
    min_podiums: int = DEFAULT_STABILITY_MIN_PODIUMS


def base_points(level: str, place: int | None, status: str, settings: RatingSettings) -> int:
    if status != "OK" or not place:
        return 0
    table = settings.points.get(level, [])
    return table[place - 1] if place <= len(table) else 0


def compute_rating(results: list[dict], settings: RatingSettings, excluded: dict[str, str] | None = None,
                   region: str = REGION_CODE) -> dict:
    """`results`: flat protocol rows {fsp_id, full_name, competition_id, competition_code, title, level, date,
    place, status, dsq_reason?}. `excluded`: fsp_id → human-readable reason (active sanctions)."""
    excluded = excluded or {}
    coef = Decimal(str(settings.coefficient))
    athletes: dict[str, dict] = {}
    for r in results:
        if r["fsp_id"].split("-")[1] != region:
            continue
        a = athletes.setdefault(r["fsp_id"], {"fsp_id": r["fsp_id"], "full_name": r["full_name"], "breakdown": []})
        pts = base_points(r["level"], r["place"], r["status"], settings)
        a["breakdown"].append({
            "competition_id": r["competition_id"], "competition_code": r["competition_code"], "title": r["title"],
            "level": r["level"], "level_label": LEVEL_LABELS.get(r["level"], r["level"]), "date": r["date"],
            "place": r["place"], "status": r["status"], "dsq_reason": r.get("dsq_reason"),
            "points": pts, "podium": r["status"] == "OK" and bool(r["place"]) and r["place"] <= 3,
        })

    rows, excluded_rows = [], []
    for a in athletes.values():
        a["breakdown"].sort(key=lambda b: b["date"])
        base = sum(b["points"] for b in a["breakdown"])
        podiums = sum(b["podium"] for b in a["breakdown"])
        golds = sum(b["podium"] and b["place"] == 1 for b in a["breakdown"])
        applied = podiums >= settings.min_podiums
        k = coef if applied else Decimal("1")
        final = (Decimal(base) * k).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        a.update({
            "base_total": base, "podiums": podiums, "golds": golds, "starts": len(a["breakdown"]),
            "coefficient": float(k), "coefficient_applied": applied,
            "coefficient_reason": (f"Призёр {podiums} соревнований сезона (≥ {settings.min_podiums}) → ×{settings.coefficient}"
                                   if applied else f"Призовых мест: {podiums} из {settings.min_podiums} необходимых"),
            "bonus": float(final - Decimal(base)), "final": float(final),
        })
        if a["fsp_id"] in excluded:
            excluded_rows.append({**a, "excluded_reason": excluded[a["fsp_id"]]})
        else:
            rows.append(a)

    rows.sort(key=lambda a: (-a["final"], -a["golds"], -a["podiums"], a["full_name"]))
    prev, position = None, 0
    for i, a in enumerate(rows, start=1):
        key = (a["final"], a["golds"], a["podiums"])
        if key != prev:
            position, prev = i, key
        a["position"] = position
    return {"rows": rows, "excluded": excluded_rows}


def compute_org_rating(rating_rows: list[dict], persons: dict[str, dict], orgs: dict[str, dict],
                       no_organization: str = "Без организации", only_clubs: bool = False) -> list[dict]:
    """Рейтинг организаций и клубов: сумма рейтинговых баллов их спортсменов.

    `rating_rows` — строки уже посчитанных рейтингов спортсменов (по одной дисциплине или по нескольким подряд: спортсмен,
    выступавший в двух дисциплинах, приносит баллы обеих); санкции и регион там уже учтены. `persons` — реестр
    (`fsp_id` → карточка с `organization`), `orgs` — справочник организаций (`name` → {type, municipality}).
    Организации без баллов и «Без организации» в таблицу не попадают; `only_clubs` оставляет только клубы (места считаются
    внутри выборки). Места при равенстве баллов, побед и призов общие."""
    agg: dict[str, dict] = {}
    for r in rating_rows:
        p = persons.get(r["fsp_id"])
        name = p["organization"] if p else None
        if not name or name == no_organization:
            continue
        a = agg.setdefault(name, {"athletes": {}, "points": Decimal("0"), "podiums": 0, "golds": 0, "starts": 0})
        a["points"] += Decimal(str(r["final"]))
        a["podiums"] += r["podiums"]
        a["golds"] += r["golds"]
        a["starts"] += r["starts"]
        mine = a["athletes"].setdefault(r["fsp_id"], {"fsp_id": r["fsp_id"], "full_name": r["full_name"], "points": Decimal("0")})
        mine["points"] += Decimal(str(r["final"]))
    rows = []
    for name, a in agg.items():
        info = orgs.get(name, {})
        best = max(a["athletes"].values(), key=lambda x: (x["points"], x["full_name"]))
        rows.append({"name": name, "type": info.get("type", "—"), "municipality": info.get("municipality", "—"),
                     "is_club": info.get("type") == "Клуб", "points": float(a["points"].quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)),
                     "athletes": len(a["athletes"]), "podiums": a["podiums"], "golds": a["golds"], "starts": a["starts"],
                     "best": {"fsp_id": best["fsp_id"], "full_name": best["full_name"], "points": float(best["points"])}})
    if only_clubs:
        rows = [r for r in rows if r["is_club"]]
    rows.sort(key=lambda r: (-r["points"], -r["golds"], -r["podiums"], r["name"]))
    prev, position = None, 0
    for i, r in enumerate(rows, start=1):
        key = (r["points"], r["golds"], r["podiums"])
        if key != prev:
            position, prev = i, key
        r["position"] = position
    return rows


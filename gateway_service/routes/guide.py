"""Д7. Личный путь спортсмена (текущий разряд → что нужно для следующего → подходящие старты) и справочник понятий."""
import asyncio
from itertools import groupby

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from gateway_service.web import clients, current_user, render, require, safe
from shared import config, domain, glossary
from shared.domain import Role

router = APIRouter()


def next_rank(person: dict) -> tuple[dict | None, str]:
    """Следующая цель: (разряд, вид цели). Нет разряда → III юношеский; действующий → следующий по ЕВСК; истекающий/истёкший → его подтверждение."""
    held = person["rank"]
    if not held:
        return domain.SPORT_RANKS[0], "assign"
    if held["state"] in ("expired", "critical", "warning") and held["expires_at"]:
        return domain.RANK_BY_CODE[held["code"]], "confirm"
    i = domain.RANK_ORDER[held["code"]] + 1
    return (domain.SPORT_RANKS[i], "assign") if i < len(domain.SPORT_RANKS) else (None, "top")


def rule_progress(rules: list[dict], results: list[dict]) -> list[dict]:
    """Какие требования ЕВСК уже закрыты результатами спортсмена (дисквалификация не считается)."""
    out = []
    for rule in rules:
        hit = next((r for r in results if r["status"] == "OK" and r["level"] == rule["level"] and r["place"]
                    and r["place"] <= rule["max_place"] and r["protocol_size"] >= rule["min_participants"]), None)
        out.append({**rule, "done": bool(hit), "result": hit})
    return out


@router.get("/journey", response_class=HTMLResponse)
async def journey_page(request: Request):
    user = await require(request, Role.ATHLETE)
    person, fid, degraded = user["person"], user["person"]["fsp_id"], []
    perf, reqs, comps, links = await asyncio.gather(
        safe(clients["sportsman"].get(f"/api/v1/persons/{fid}/performance"), None, degraded),
        safe(clients["competition"].get("/api/v1/evsk/requirements"), [], degraded),
        safe(clients["competition"].get("/api/v1/competitions", upcoming=True), [], degraded),
        safe(clients["sportsman"].get(f"/api/v1/athletes/{fid}/links"), [], degraded))
    results = perf["results"] if perf and perf.get("available") else []
    target, kind = next_rank(person)
    rules = next((r["rules"] for r in reqs if target and r["code"] == target["code"]), [])
    progress = rule_progress(rules, results)
    levels = {r["level"] for r in rules}
    suitable = []
    for c in comps:
        if c["discipline"] not in person["disciplines"] or c["status"] not in ("REGISTRATION_OPEN", "PLANNED", "REGISTRATION_CLOSED"):
            continue
        age, fits = domain.age_group_fit(person["birth_date"], c["age_group"], c["start_date"])
        if fits:
            suitable.append({**c, "age_at_start": age, "gives_rank": c["level"] in levels,
                             "can_apply": c["status"] == "REGISTRATION_OPEN"})
    suitable.sort(key=lambda c: (not c["gives_rank"], c["start_date"]))
    checklist = [
        ("Профиль и согласие на обработку данных", bool(person.get("pd_consent_at")), "/cabinet"),
        ("Антидопинг: курс РУСАДА пройден", bool(person.get("antidoping")), "/cabinet#antidoping"),
        ("Есть тренер (активная привязка)", any(lk["status"] == "ACTIVE" for lk in links), "/cabinet#trainers"),
        ("Есть результат в протоколе", bool(results), "/calendar"),
        ("Разряд присвоен", bool(person["rank"]), "/cabinet#rank"),
    ]
    return render(request, "journey.html", user=user, person=person, target=target, kind=kind, progress=progress,
                  suitable=suitable[:8], checklist=checklist, ranks=domain.SPORT_RANKS, results=results,
                  held_order=domain.RANK_ORDER[person["rank"]["code"]] if person["rank"] else -1, degraded=degraded)


@router.get("/glossary", response_class=HTMLResponse)
async def glossary_page(request: Request):
    user = await current_user(request)
    groups = [(cat, [(t, d) for _, t, d in rows]) for cat, rows in groupby(glossary.GLOSSARY, key=lambda x: x[0])]
    return render(request, "glossary.html", user=user, groups=groups, how_to=glossary.HOW_TO, today=config.today(),
                  rusada={"site": glossary.RUSADA_URL, "course": glossary.RUSADA_COURSE_URL, "rules": glossary.RUSADA_RULES_URL})

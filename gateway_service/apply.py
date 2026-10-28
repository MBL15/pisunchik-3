"""Подача заявок тренером: подопечные по соревнованию со статусом документов и уведомление спортсмену.

Документы только подсвечиваются: заявку не блокируют ни РУСАДА, ни согласия — тренер подаёт, а спортсмену уходит
напоминание приложить недостающее. Блокируют только проверки самой заявки (возраст, санкция, повтор).
"""
from gateway_service import notify
from gateway_service.web import clients
from shared import domain
from shared.http import ServiceError


def docs_status(athlete: dict) -> list[dict]:
    """Документы и допуски спортсмена для строки списка: `{label, tone, text, date}`, tone — ok | warn | bad."""
    docs = []
    ad = athlete.get("antidoping")
    docs.append({"key": "rusada", "label": "РУСАДА", "tone": "ok" if ad else "warn",
                 "text": "курс пройден" if ad else "нет сертификата", "date": ad["course_date"] if ad else None})
    pd = athlete.get("pd_consent_at")
    docs.append({"key": "pd", "label": "Согласие ПДн", "tone": "ok" if pd else "bad",
                 "text": "дано" if pd else "нет", "date": pd[:10] if pd else None})
    if athlete.get("is_minor"):
        g = athlete.get("guardian")
        docs.append({"key": "guardian", "label": "Законный представитель", "tone": "ok" if g else "bad",
                     "text": "согласие дано" if g else "нет согласия", "date": g["consent_at"][:10] if g else None})
    rank = athlete.get("rank")
    if rank:
        state = rank["state"]
        tone = "bad" if state == "expired" else "warn" if state in ("critical", "warning") else "ok"
        text = (f"{rank['short']} · истёк" if state == "expired" else
                f"{rank['short']} · {rank['days_left']} дн." if tone == "warn" else rank["short"])
        docs.append({"key": "rank", "label": "Разряд", "tone": tone, "text": text, "date": None})
    if athlete.get("is_sanctioned"):
        docs.append({"key": "sanction", "label": "Санкция", "tone": "bad", "text": "действует", "date": None})
    return docs


def roster(comps: list[dict], links: list[dict], apps: list[dict]) -> list[dict]:
    """Для каждого открытого соревнования — подопечные тренера по его дисциплине.

    Строка: `{athlete_fsp_id, athlete, docs, age, blocked}`; `blocked` — причина, по которой подать нельзя
    (санкция, возраст к дате старта, заявка уже есть), иначе None. `apps` — все заявки на эти соревнования.
    """
    active = [link for link in links if link["status"] == "ACTIVE"]
    taken = {(a["competition_id"], a["athlete_fsp_id"]): a for a in apps if a["status"] != "REJECTED"}
    out = []
    for c in sorted(comps, key=lambda c: (c["start_date"], c["id"])):
        rows, seen = [], set()
        for link in active:
            a = link["athlete"]
            if link["discipline"] != c["discipline"] or a["fsp_id"] in seen:
                continue
            seen.add(a["fsp_id"])
            age, fits = domain.age_group_fit(a["birth_date"], c["age_group"], c["start_date"])
            ex = taken.get((c["id"], a["fsp_id"]))
            blocked = (f"заявка уже подана · {ex['status_label'].lower()}" if ex else
                       "действует санкция" if a["is_sanctioned"] else
                       f"возраст к старту — {age}, группа {c['age_group']}" if not fits else None)
            rows.append({"athlete_fsp_id": a["fsp_id"], "athlete": a, "docs": docs_status(a), "age": age,
                         "blocked": blocked, "applied": bool(ex)})
        rows.sort(key=lambda r: (r["blocked"] is not None, r["athlete"]["full_name"]))
        out.append({"comp": c, "rows": rows, "free": sum(r["blocked"] is None for r in rows)})
    return out


async def announce(app_: dict, submitter_id: str) -> None:
    """Уведомить спортсмена о заявке от тренера; без сертификата РУСАДА — отдельное напоминание его приложить."""
    athlete_id = app_["athlete_fsp_id"]
    if athlete_id == submitter_id:
        return
    await notify.notify(athlete_id, "Тренер подал заявку за вас", app_["competition_title"], "/cabinet")
    try:
        person = await clients["sportsman"].get(f"/api/v1/persons/{athlete_id}")
    except ServiceError:
        return
    if not person.get("antidoping"):
        await notify.notify(athlete_id, "Приложите сертификат РУСАДА",
                            f"Вас заявили на «{app_['competition_title']}». Пройдите онлайн-курс РУСАДА и добавьте дату и ссылку "
                            "на сертификат в профиле — без него на очном старте могут не допустить.", "/cabinet#antidoping")

"""Демо-данные для Д3 и Д4: карточки организаций, группы по этапам, журнал занятий, справочник нормативов и их результаты.

Считаются от `config.today()` и детерминированы. Только участники демо-ростера: синтетический объём (`FSP_DEMO_SCALE`) в группы не попадает.
"""
import random
from datetime import date, timedelta

from shared import config
from shared.demo_roster import ROSTER
from shared.domain import DISCIPLINE_BY_CODE, ORGANIZATION_INFO, Role
from shared.training import STAGE_ORDER, applies, default_norm_rows, evaluate

TOPICS = ["Разбор задач прошлого тура", "Структуры данных", "Работа с кейсом", "Подготовка к старту", "Тренировочный контест"]


def _confirmation(k: int, online: bool, gid: int) -> tuple[str | None, str]:
    """Онлайн всегда подтверждён (лог платформы или запись); очные — листом посещаемости, каждое четвёртое без отметки."""
    if online:
        return ("PLATFORM_LOG", f"LOG-{gid}-{k:03d}") if k % 2 else ("RECORDING", f"https://video.example.org/fsp56/{gid}-{k}")
    return ("SIGN_SHEET", f"Лист № {k + 1}") if k % 4 else (None, "")


def seed(s) -> None:
    today = config.today()
    rnd = random.Random(2026)
    demo_ids = {r["fsp_id"] for r in ROSTER}
    people = [p for p in s.persons.values() if p.fsp_id in demo_ids]
    for row in default_norm_rows():
        s.ext.add("norms", row)
    norms = list(s.ext.table("norms").values())

    # карточки организаций: лицензия и программы подготовки по этапам и дисциплинам их спортсменов
    for n, org in enumerate(ORGANIZATION_INFO, start=1):
        athletes = [p for p in people if p.organization == org and Role.ATHLETE in p.roles and p.training_stage]
        programs = sorted({(p.training_stage, d) for p in athletes for d in p.disciplines},
                          key=lambda x: (STAGE_ORDER.index(x[0]), list(DISCIPLINE_BY_CODE).index(x[1])))
        s.ext.add("org_cards", {
            "name": org, "updated_at": today.isoformat() + "T09:00:00",
            "description": f"Реализует программы спортивной подготовки по виду спорта «спортивное программирование» ({ORGANIZATION_INFO[org]['type'].lower()}).",
            "license": {"number": f"ЛО-56-{2020 + n}-{n:05d}", "issued_at": date(2020 + n % 3, 3, 15).isoformat(),
                        # одна лицензия скоро истекает — виден предупреждающий статус
                        "valid_until": (today + timedelta(days=60)).isoformat() if n == 3 else None,
                        "issuer": "Министерство образования Оренбургской области"},
            "programs": [{"stage": st, "discipline": d} for st, d in programs]})

    # группы: по каждой программе — тренер организации (иначе любой тренер дисциплины) и спортсмены этого этапа
    trainers = [p for p in people if Role.TRAINER in p.roles]
    for card in list(s.ext.table("org_cards").values()):
        for prog in card["programs"]:
            stage, disc = prog["stage"], prog["discipline"]
            members = [p for p in people if p.organization == card["name"] and Role.ATHLETE in p.roles
                       and p.training_stage == stage and disc in p.disciplines]
            coach = next((t for t in trainers if t.organization == card["name"] and disc in t.disciplines), None) \
                or next((t for t in trainers if disc in t.disciplines), None)
            if not members or not coach:
                continue
            g = s.ext.add("groups", {"org": card["name"], "name": f"{DISCIPLINE_BY_CODE[disc]['short']} · {stage}-1", "stage": stage,
                                     "discipline": disc, "trainer_fsp_id": coach.fsp_id, "archived": False, "members": [],
                                     "created_at": f"{today.year - 2}-09-01T10:00:00"})
            for p in members:
                since = min(date(max(p.start_year, today.year - 2), 9, 1), today - timedelta(days=30))
                g["members"].append({"fsp_id": p.fsp_id, "enrolled_at": since.isoformat(), "left_at": None, "left_reason": ""})
            # журнал: раз в две недели за последние ~14 месяцев, каждое третье занятие — онлайн
            day, k = today - timedelta(days=425), 0
            while day <= today - timedelta(days=2):
                present = [m["fsp_id"] for m in g["members"] if m["enrolled_at"] <= day.isoformat() and rnd.random() < 0.82]
                online = k % 3 == 2
                kind, ref = _confirmation(k, online, g["id"])
                s.ext.add("sessions", {"group_id": g["id"], "date": day.isoformat(), "topic": TOPICS[k % len(TOPICS)],
                                       "format": "ONLINE" if online else "OFFLINE", "duration_min": 90, "confirmation_kind": kind,
                                       "confirmation_ref": ref, "attendees": present, "created_by": coach.fsp_id,
                                       "created_at": day.isoformat() + "T20:00:00"})
                day += timedelta(days=14)
                k += 1

    # результаты нормативов: seq % 4 == 0,1 — всё сдано (готов к переводу); 2 — не сдана скорость печати; 3 — ещё не сдавал
    for p in people:
        if Role.ATHLETE not in p.roles or not p.training_stage or p.seq % 4 == 3:
            continue
        coach = next((lk.trainer_fsp_id for lk in s.links if lk.athlete_fsp_id == p.fsp_id and lk.status == "ACTIVE"), p.fsp_id)
        for n in [n for n in norms if applies(n, p.training_stage, p.disciplines, p.gender)]:
            fail = p.seq % 4 == 2 and n["title"].startswith("Скорость печати")
            good = n["threshold"] * (1.05 if n["direction"] == "MIN" else 0.95)
            bad = n["threshold"] * (0.9 if n["direction"] == "MIN" else 1.1)
            value = round(bad if fail else good, 2)
            s.ext.add("norm_results", {
                "athlete_fsp_id": p.fsp_id, "norm_id": n["id"], "value": value, "date": (today - timedelta(days=10 + p.seq % 30)).isoformat(),
                "passed": evaluate(n["direction"], n["threshold"], value), "threshold": n["threshold"], "direction": n["direction"],
                "unit": n["unit"], "stage": n["stage"], "recorded_by": coach, "recorded_at": today.isoformat() + "T12:00:00"})

    # история аттестации: Соколов уже переведён на свой этап прошлой сдачей
    sokolov = next((p for p in people if p.seq == 3), None)
    if sokolov and sokolov.training_stage in STAGE_ORDER[1:]:
        trainer = next(p for p in people if p.seq == 2)
        s.ext.add("attestations", {"athlete_fsp_id": sokolov.fsp_id, "from_stage": STAGE_ORDER[STAGE_ORDER.index(sokolov.training_stage) - 1],
                                   "to_stage": sokolov.training_stage, "date": (today - timedelta(days=330)).isoformat(),
                                   "decided_by": trainer.fsp_id, "note": "Выполнены нормативы этапа", "norm_results": []})

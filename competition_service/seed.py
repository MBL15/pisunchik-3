"""Mock database seed: season calendar + synthetic but realistic FSP-LOG/1 competition logs.

Logs are generated deterministically (random.Random seeded by competition code) from the shared demo
roster's per-discipline strengths, then fed through the *real* log parser — so every seeded protocol is
produced by exactly the same pipeline as an uploaded one.
"""
import math
import random
from datetime import datetime, timedelta

from shared import config, demo_scale
from shared.demo_roster import ROSTER_BY_SEQ, athletes_in, full_name
from shared.domain import AGE_GROUPS, ApplicationStatus, CompetitionStatus, protocol_mode
from shared.fsp_id import make_fsp_id

# (key, discipline, level, type, format, format_note, plan, plan_no, start_offset, days, venue, age_group, status, deadline_offset)
CALENDAR = [
    ("ALG1", "ALG", "REGIONAL", "Первенство", "OFFLINE", "", "REGIONAL", "№ 56-014", -230, 1, "г. Оренбург, ОГУ, учебный корпус № 2", "юноши/девушки 14–17 лет", "FINISHED", None),
    ("ALG2", "ALG", "REGIONAL", "Кубок", "ONLINE", "", "REGIONAL", "№ 56-031", -150, 1, "Онлайн-платформа ФСП", "без ограничений 12+", "FINISHED", None),
    ("ALG3", "ALG", "INTERREG", "Турнир", "HYBRID", "Отборочный тур — онлайн, финал — очно", "EKP", "№ 2-4471", -95, 2, "г. Самара / онлайн", "без ограничений 12+", "FINISHED", None),
    ("ALG4", "ALG", "CHAMP_RU", "Чемпионат", "OFFLINE", "", "EKP", "№ 1-0217", -40, 3, "г. Москва, кластер «Ломоносов»", "мужчины/женщины 18+", "FINISHED", None),
    ("ALG5", "ALG", "REGIONAL", "Турнир", "ONLINE", "", "REGIONAL", "№ 56-077", 0, 1, "Онлайн-платформа ФСП", "без ограничений 12+", "LIVE", None),
    ("ALG6", "ALG", "REGIONAL", "Чемпионат", "OFFLINE", "", "REGIONAL", "№ 56-082", 24, 2, "г. Оренбург, Дом молодёжи", "без ограничений 12+", "REGISTRATION_OPEN", 14),
    ("PRD1", "PRD", "REGIONAL", "Первенство", "HYBRID", "Разработка — онлайн, защита проектов — очно", "REGIONAL", "№ 56-019", -200, 3, "г. Оренбург, Кванториум / онлайн", "юноши/девушки 14–17 лет", "FINISHED", None),
    ("PRD2", "PRD", "INTERREG", "Турнир", "HYBRID", "Хакатон — онлайн, финальный питч — очно", "EKP", "№ 2-5120", -70, 2, "г. Уфа / онлайн", "без ограничений 12+", "FINISHED", None),
    ("PRD3", "PRD", "REGIONAL", "Кубок", "HYBRID", "Разработка — онлайн, защита — очно", "REGIONAL", "№ 56-090", 45, 3, "г. Оренбург, ОГУ", "без ограничений 12+", "REGISTRATION_OPEN", 30),
    ("SEC1", "SEC", "REGIONAL", "Чемпионат", "ONLINE", "CTF Jeopardy", "REGIONAL", "№ 56-022", -180, 1, "Онлайн-платформа OrenCTF", "без ограничений 12+", "FINISHED", None),
    ("SEC2", "SEC", "INTERREG", "Турнир", "OFFLINE", "Attack-Defense", "EKP", "№ 2-4980", -60, 2, "г. Самара, СГАУ", "без ограничений 12+", "FINISHED", None),
    ("SEC3", "SEC", "FIRST_RU", "Первенство", "OFFLINE", "Attack-Defense", "EKP", "№ 1-0388", -25, 2, "г. Санкт-Петербург", "юниоры 16–21 год", "FINISHED", None),
    ("AI1", "AI", "REGIONAL", "Первенство", "ONLINE", "", "REGIONAL", "№ 56-027", -160, 2, "Онлайн-платформа ФСП", "без ограничений 12+", "FINISHED", None),
    ("AI2", "AI", "INTERREG", "Турнир", "ONLINE", "", "EKP", "№ 2-5033", -50, 3, "Онлайн", "без ограничений 12+", "FINISHED", None),
    ("AI3", "AI", "REGIONAL", "Чемпионат", "OFFLINE", "", "REGIONAL", "№ 56-095", 60, 2, "г. Оренбург, ОГУ", "мужчины/женщины 18+", "PLANNED", None),
    ("ROB1", "ROB", "REGIONAL", "Первенство", "OFFLINE", "", "REGIONAL", "№ 56-041", -120, 1, "г. Орск, ЦЦОД «IT-куб»", "мальчики/девочки 10–13 лет", "FINISHED", None),
    ("ROB2", "ROB", "INTERREG", "Турнир", "OFFLINE", "", "EKP", "№ 2-5210", -45, 2, "г. Казань, Иннополис", "без ограничений 12+", "FINISHED", None),
    ("ROB3", "ROB", "REGIONAL", "Кубок", "OFFLINE", "", "REGIONAL", "№ 56-088", 35, 1, "г. Орск, ЦЦОД «IT-куб»", "без ограничений 12+", "REGISTRATION_OPEN", 20),
    ("UAV1", "UAV", "REGIONAL", "Первенство", "HYBRID", "Программирование миссий — онлайн, полёты — очно на полигоне", "REGIONAL", "№ 56-046", -100, 2, "Оренбургский р-н, полигон «Степной»", "без ограничений 12+", "FINISHED", None),
    ("UAV2", "UAV", "REGIONAL", "Кубок", "OFFLINE", "", "REGIONAL", "№ 56-063", -35, 1, "г. Оренбург, аэроклуб", "без ограничений 12+", "FINISHED", None),
    ("UAV3", "UAV", "INTERREG", "Турнир", "OFFLINE", "", "EKP", "№ 2-5302", 80, 2, "г. Самара", "без ограничений 12+", "PLANNED", None),
    # Finished the day before yesterday, protocol not formed yet: the federation enters results for approved applicants.
    ("SEC4", "SEC", "REGIONAL", "Кубок", "ONLINE", "CTF Jeopardy", "REGIONAL", "№ 56-079", -2, 1, "Онлайн-платформа OrenCTF", "без ограничений 12+", "REGISTRATION_CLOSED", -9),
]

TITLES = {
    "ALG1": "Первенство Оренбургской области по спортивному программированию (алгоритмическое)",
    "ALG2": "Кубок Оренбуржья по алгоритмическому программированию",
    "ALG3": "Межрегиональные соревнования ПФО «Волга-Код»",
    "ALG4": "Чемпионат России по спортивному программированию (алгоритмическое)",
    "ALG5": "Открытый онлайн-турнир «Оренбург.Алго» — осенний этап",
    "ALG6": "Чемпионат Оренбургской области по спортивному программированию (алгоритмическое)",
    "PRD1": "Первенство Оренбургской области по продуктовому программированию",
    "PRD2": "Окружной хакатон ПФО «Цифровой прорыв. Регион»",
    "PRD3": "Кубок Оренбуржья по продуктовому программированию",
    "SEC1": "Чемпионат Оренбургской области — CTF «OrenCTF»",
    "SEC2": "Межрегиональные соревнования «Volga CTF Open»",
    "SEC3": "Первенство России по программированию систем информационной безопасности",
    "AI1": "Первенство Оренбургской области по программированию систем ИИ",
    "AI2": "Межрегиональный ML-турнир ПФО",
    "AI3": "Чемпионат Оренбургской области по программированию систем ИИ",
    "ROB1": "Первенство Оренбургской области по программированию робототехники",
    "ROB2": "Межрегиональные соревнования ПФО по программированию робототехники",
    "ROB3": "Кубок Оренбуржья по программированию робототехники",
    "UAV1": "Первенство Оренбургской области по программированию БАС",
    "UAV2": "Кубок Оренбуржья по программированию беспилотных авиационных систем",
    "UAV3": "Межрегиональные соревнования ПФО по программированию БАС",
    "SEC4": "Кубок Оренбуржья по программированию систем информационной безопасности «OrenCTF Осень»",
}

DSQ_PLAN = {"ALG3": (8, "Нарушение регламента: использование внешней помощи")}

_EXT_LAST = ["Иванов", "Смирнова", "Попов", "Васильева", "Новиков", "Фролова", "Волков", "Алексеева", "Медведев",
             "Козлова", "Егоров", "Никитина", "Павлов", "Семёнова", "Голубев", "Виноградова"]
_EXT_FIRST = ["Александр", "Мария", "Михаил", "Софья", "Иван", "Анна", "Даниил", "Елизавета", "Артемий", "Вероника",
              "Марк", "Алиса", "Фёдор", "Варвара", "Степан", "Милана"]
_REGIONS_PFO = {"63": "Самарская обл.", "02": "Респ. Башкортостан", "16": "Респ. Татарстан", "58": "Пензенская обл.",
                "64": "Саратовская обл.", "73": "Ульяновская обл."}
_REGIONS_RU = {"77": "г. Москва", "78": "г. Санкт-Петербург", "50": "Московская обл.", "66": "Свердловская обл.",
               "54": "Новосибирская обл.", "16": "Респ. Татарстан"}


def _ts(seconds: int) -> str:
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _externals(rng: random.Random, level: str, count: int) -> list[tuple[str, float, dict]]:
    """Participants from other regions (stable identity: same FSP ID → same name in every protocol)."""
    regions = _REGIONS_RU if level in ("CHAMP_RU", "FIRST_RU") else _REGIONS_PFO
    lo, hi = (0.80, 0.97) if level in ("CHAMP_RU", "FIRST_RU") else (0.62, 0.92)
    out = []
    for i in range(count):
        code = list(regions)[i % len(regions)]
        fid = make_fsp_id(2025, 300 + i, region=code)
        name = f"{_EXT_LAST[i % 16]} {_EXT_FIRST[(i * 5) % 16]}"  # same parity → matching gender
        out.append((fid, rng.uniform(lo, hi), {"full_name": name, "region": regions[code]}))
    return out


def _icpc_events(rng: random.Random, field: list[tuple[str, float]], tasks: int = 8, duration_min: int = 300) -> list[tuple[int, str]]:
    difficulty = sorted(rng.uniform(0.30, 0.98) for _ in range(tasks))
    events = []
    for fid, s in field:
        events.append((0, f"JOIN {fid}"))
        for i, d in enumerate(difficulty):
            letter = chr(ord("A") + i)
            p_solve = 1 / (1 + math.exp(-(s - d) * 12))
            solved = rng.random() < p_solve
            expected = duration_min * (0.06 + 0.8 * (i + 1) / tasks) * (1.25 - 0.5 * s)
            ac_min = int(min(duration_min - 2, max(4, rng.gauss(expected, 18))))
            wrong = int(rng.expovariate(1.4)) if solved else (rng.randint(1, 3) if p_solve > 0.15 else 0)
            for _ in range(wrong):
                t = rng.randint(2, ac_min if solved else duration_min - 1) * 60 + rng.randint(0, 59)
                events.append((min(t, ac_min * 60 - 1) if solved else t,
                               f"RUN {fid} {letter} {rng.choice(['WA', 'WA', 'TL', 'RE'])}"))
            if solved:
                events.append((ac_min * 60 + rng.randint(0, 59), f"RUN {fid} {letter} OK"))
    return events


def _score_events(rng: random.Random, field: list[tuple[str, float]], rounds: int = 3) -> list[tuple[int, str]]:
    events = []
    for fid, s in field:
        events.append((0, f"JOIN {fid}"))
        for r in range(rounds):
            pts = max(0.0, min(33.33, rng.gauss(33.33 * s, 3.2)))
            events.append(((r + 1) * 3600 + rng.randint(0, 1800), f"SCORE {fid} {pts:.2f}"))
    return events


def _sanctioned_on(person: dict, offset: int) -> bool:
    sc = person.get("sanction")
    return bool(sc) and sc["from_offset"] <= offset <= sc["to_offset"]


def build_log(key: str, code: str, discipline: str, level: str, offset: int) -> tuple[str, list[str], dict, list[str]]:
    """Returns (mode, header+past lines, names snapshot, future lines for live streaming)."""
    rng = random.Random(code)
    field, names = [], {}
    for p in athletes_in(discipline):
        s = p["strength"][discipline]
        if _sanctioned_on(p, offset):
            continue
        if level in ("CHAMP_RU", "FIRST_RU") and s < 0.84:
            continue
        if level == "REGIONAL" and rng.random() > 0.9:
            continue
        field.append((p["fsp_id"], s))
        names[p["fsp_id"]] = {"full_name": full_name(p), "region": "Оренбургская обл."}
    ext_count = {"CHAMP_RU": 14, "FIRST_RU": 12, "INTERREG": 8}.get(level, 0)
    for fid, s, meta in _externals(rng, level, ext_count):
        field.append((fid, s))
        names[fid] = meta

    mode = protocol_mode(discipline)
    events = _score_events(rng, field) if mode == "score" else _icpc_events(rng, field)
    if key in DSQ_PLAN:
        seq, reason = DSQ_PLAN[key]
        events.append((4 * 3600 + 120, f'DSQ {ROSTER_BY_SEQ[seq]["fsp_id"]} "{reason}"'))
    events.sort(key=lambda e: (e[0], e[1]))
    header = f"#! FSP-LOG/1 competition={code} mode={mode} tasks=8 penalty=20"
    lines = [f"{_ts(t)} {body}" for t, body in events]
    if key == "ALG5":
        split = next(i for i, (t, _) in enumerate(events) if t > 95 * 60)
        return mode, [header] + lines[:split], names, lines[split:]
    return mode, [header] + lines, names, []


JUDGE_PANELS = {
    "ALG": [(1, "Главный судья"), (6, "Главный секретарь"), (2, "Судья")],
    "SEC": [(1, "Главный судья"), (6, "Главный секретарь"), (2, "Судья")],
    "PRD": [(1, "Главный судья"), (6, "Главный секретарь"), (5, "Судья")],
    "AI": [(1, "Главный судья"), (6, "Главный секретарь"), (5, "Технический судья")],
    "ROB": [(6, "Главный судья"), (1, "Заместитель главного судьи"), (7, "Судья")],
    "UAV": [(6, "Главный судья"), (1, "Заместитель главного судьи"), (7, "Технический судья")],
}

# (calendar key, athlete seq, submitted_by seq, status)
APPLICATIONS = [
    ("ALG6", 3, 3, "APPROVED"), ("ALG6", 8, 2, "PENDING"), ("ALG6", 9, 2, "PENDING"), ("ALG6", 11, 2, "APPROVED"),
    ("ALG6", 27, 3, "PENDING"),
    ("PRD3", 18, 5, "PENDING"), ("PRD3", 16, 16, "APPROVED"),
    ("ROB3", 21, 4, "PENDING"),
    ("SEC4", 13, 2, "APPROVED"), ("SEC4", 14, 2, "APPROVED"), ("SEC4", 9, 2, "APPROVED"), ("SEC4", 26, 26, "APPROVED"),
    ("SEC4", 15, 15, "PENDING"),
]


def seed(store) -> None:
    from competition_service.models import Application, Competition, JudgeAssignment, Protocol

    today = config.today()
    now = datetime.now()
    keys: dict[str, int] = {}
    for idx, (key, disc, level, ctype, fmt, note, plan, plan_no, start_off, days, venue, age, status, deadline) in enumerate(CALENDAR, start=1):
        start = today + timedelta(days=start_off)
        code = f"ОРБ-{disc}-{start.year}-{idx:02d}"
        panel = JUDGE_PANELS[disc] if level == "REGIONAL" else ([(2, "Судья")] if key == "ALG3" else [])
        comp = Competition(
            id=idx, code=code, title=TITLES[key], discipline=disc, level=level, type=ctype, format=fmt,
            format_note=note, calendar_plan=plan, plan_number=plan_no, start_date=start,
            end_date=start + timedelta(days=days - 1), venue=venue, age_group=age, status=status,
            registration_deadline=today + timedelta(days=deadline) if deadline is not None else None,
            judges=[JudgeAssignment(fsp_id=ROSTER_BY_SEQ[s]["fsp_id"], full_name=full_name(ROSTER_BY_SEQ[s]), position=pos)
                    for s, pos in panel],
        )
        store.competitions[idx] = comp
        keys[key] = idx
        if status in (CompetitionStatus.FINISHED, CompetitionStatus.LIVE):
            mode, lines, names, future = build_log(key, code, disc, level, start_off)
            proto = Protocol(competition_id=idx, mode=mode, raw_log=lines, names=names, updated_at=now,
                             published=status == CompetitionStatus.FINISHED,
                             published_at=now if status == CompetitionStatus.FINISHED else None,
                             source="live" if future else "seed")
            store.protocols[idx] = proto
            store.reparse(proto)
            if future:
                store.live_queue[idx] = list(future)
                store.live_replay[idx] = (list(lines), list(future))

    for key, athlete_seq, by_seq, status in APPLICATIONS:
        athlete = ROSTER_BY_SEQ[athlete_seq]
        store.applications.append(Application(
            id=len(store.applications) + 1, competition_id=keys[key], athlete_fsp_id=athlete["fsp_id"],
            athlete_name=full_name(athlete), submitted_by=ROSTER_BY_SEQ[by_seq]["fsp_id"],
            status=ApplicationStatus(status), created_at=now - timedelta(days=3),
            decided_at=now - timedelta(days=1) if status != "PENDING" else None,
        ))
    _seed_scale_applications(store, today, now)
    store.log("system", "seed", f"{len(store.competitions)} соревнований, {len(store.protocols)} протоколов")


def _seed_scale_applications(store, today, now) -> None:
    """FSP_DEMO_SCALE: заявки синтетических спортсменов на открытые старты — объём для модерации и поиска."""
    from competition_service.models import Application

    people = [p for p in demo_scale.generate() if "athlete" in p["roles"]]
    if not people:
        return
    rnd = random.Random(57)
    opened = [c for c in store.competitions.values() if c.status == CompetitionStatus.REGISTRATION_OPEN]
    for c in opened:
        lo, hi = AGE_GROUPS[c.age_group]
        for p in people:
            if c.discipline in p["disciplines"] and lo <= demo_scale.age_on(p["birth_date"], today) <= hi and rnd.random() < 0.5:
                roll = rnd.random()
                status = "PENDING" if roll < 0.7 else "APPROVED" if roll < 0.92 else "REJECTED"
                store.applications.append(Application(
                    id=len(store.applications) + 1, competition_id=c.id, athlete_fsp_id=p["fsp_id"],
                    athlete_name=f"{p['last_name']} {p['first_name']} {p['middle_name']}", submitted_by=p["fsp_id"],
                    status=ApplicationStatus(status), created_at=now - timedelta(hours=rnd.randint(1, 24 * 14)),
                    decided_at=now if status != "PENDING" else None,
                ))

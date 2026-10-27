"""Д3. Организации спортивной подготовки: карточка (лицензия, программы), учебно-тренировочные группы по этапам, зачисление и перевод,
журнал занятий (очно / онлайн с подтверждением). Данные группы и журнала попадают в отчёт о динамике численности (`headcount.py`).
"""
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from shared import config
from shared.domain import DISCIPLINE_BY_CODE, NO_ORGANIZATION, ORGANIZATION_INFO, ORGANIZATIONS, TRAINING_STAGES, Role
from shared.training import next_stage
from sportsman_service import norms
from sportsman_service.store import age_on, store

router = APIRouter(prefix="/api/v1")
CARDS, GROUPS, SESSIONS = "org_cards", "groups", "sessions"
CONFIRMATIONS = {"RECORDING": "Запись занятия", "PLATFORM_LOG": "Лог платформы", "SIGN_SHEET": "Лист посещаемости"}
FORMATS = {"OFFLINE": "Очно", "ONLINE": "Онлайн"}


class LicenseIn(BaseModel):
    number: str = Field(min_length=3, max_length=60)
    issued_at: date
    valid_until: date | None = None  # None — бессрочная
    issuer: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def _period(self):
        if self.valid_until and self.valid_until < self.issued_at:
            raise ValueError("Лицензия действует до даты раньше выдачи")
        return self


class ProgramIn(BaseModel):
    stage: str
    discipline: str

    @model_validator(mode="after")
    def _known(self):
        if self.stage not in TRAINING_STAGES:
            raise ValueError("Неизвестный этап подготовки")
        if self.discipline not in DISCIPLINE_BY_CODE:
            raise ValueError("Неизвестная дисциплина")
        return self


class CardIn(BaseModel):
    license: LicenseIn | None = None
    programs: list[ProgramIn] = Field(default=[], max_length=24)
    description: str = Field(default="", max_length=500)
    actor: str


class GroupIn(BaseModel):
    org: str
    name: str = Field(min_length=3, max_length=80)
    stage: str
    discipline: str
    trainer_fsp_id: str
    actor: str


class MemberIn(BaseModel):
    fsp_id: str
    actor: str


class LeaveIn(BaseModel):
    actor: str
    reason: str = Field(default="", max_length=200)


class TransferIn(BaseModel):
    fsp_id: str
    to_group_id: int
    actor: str
    note: str = Field(default="", max_length=300)


class SessionIn(BaseModel):
    on: date
    topic: str = Field(min_length=3, max_length=160)
    format: Literal["OFFLINE", "ONLINE"]
    duration_min: int = Field(ge=15, le=480)
    confirmation_kind: Literal["RECORDING", "PLATFORM_LOG", "SIGN_SHEET"] | None = None
    confirmation_ref: str = Field(default="", max_length=300)
    attendees: list[str] = Field(default=[], max_length=100)
    actor: str

    @model_validator(mode="after")
    def _confirmation(self):
        ref = self.confirmation_ref.strip()
        if self.format == "ONLINE":
            if self.confirmation_kind not in ("RECORDING", "PLATFORM_LOG") or not ref:
                raise ValueError("Онлайн-занятие подтверждается ссылкой на запись или номером в логе платформы")
        elif self.confirmation_kind in ("RECORDING", "PLATFORM_LOG"):
            raise ValueError("Очное занятие подтверждается листом посещаемости")
        if self.confirmation_kind == "RECORDING" and not ref.lower().startswith(("http://", "https://")):
            raise ValueError("Ссылка на запись должна начинаться с http:// или https://")
        if self.confirmation_kind and not ref:
            raise ValueError("Укажите ссылку или номер подтверждения")
        return self


def _admin(fid: str) -> bool:
    p = store.persons.get(fid)
    return bool(p and Role.ADMIN in p.roles)


def _full(p) -> str:
    return f"{p.last_name} {p.first_name} {p.middle_name}".strip()


def _group(gid: int) -> dict:
    g = store.ext.table(GROUPS).get(gid)
    if not g:
        raise HTTPException(404, "Группа не найдена")
    return g


def _may_run(g: dict, actor: str) -> None:
    if not (_admin(actor) or actor == g["trainer_fsp_id"]):
        raise HTTPException(403, "Группой управляет её тренер или федерация")


def active_members(g: dict, on: date | None = None) -> list[dict]:
    on = on or config.today()
    return [m for m in g["members"] if date.fromisoformat(m["enrolled_at"]) <= on and (not m["left_at"] or date.fromisoformat(m["left_at"]) > on)]


def leave_stage_groups(fid: str, new_stage: str, on: date, reason: str) -> None:
    """После перевода на этап закрывает членство спортсмена в группах другого этапа."""
    for g in store.ext.table(GROUPS).values():
        for m in g["members"]:
            if m["fsp_id"] == fid and not m["left_at"] and g["stage"] != new_stage:
                m["left_at"], m["left_reason"] = on.isoformat(), reason


def _license_state(lic: dict | None, today: date) -> dict:
    if not lic:
        return {"state": "none", "label": "лицензия не указана", "days_left": None}
    if not lic["valid_until"]:
        return {"state": "ok", "label": "бессрочная", "days_left": None}
    days = (date.fromisoformat(lic["valid_until"]) - today).days
    return {"state": "expired" if days < 0 else "warning" if days <= 90 else "ok", "days_left": days,
            "label": "истекла" if days < 0 else f"осталось {days} дн."}


def _card(name: str) -> dict:
    return next((c for c in store.ext.table(CARDS).values() if c["name"] == name), None)


def present_group(g: dict, with_members: bool = False) -> dict:
    members = active_members(g)
    trainer = store.persons.get(g["trainer_fsp_id"])
    out = {**g, "stage_label": TRAINING_STAGES[g["stage"]], "discipline_label": DISCIPLINE_BY_CODE[g["discipline"]]["short"],
           "trainer_name": _full(trainer) if trainer else "—", "members_count": len(members),
           "sessions_count": sum(s["group_id"] == g["id"] for s in store.ext.table(SESSIONS).values())}
    if with_members:
        today = config.today()
        out["members"] = []
        for m in sorted(g["members"], key=lambda m: (bool(m["left_at"]), m["enrolled_at"])):
            p = store.persons.get(m["fsp_id"])
            out["members"].append({**m, "full_name": _full(p) if p else m["fsp_id"], "age": age_on(p.birth_date, today) if p else None,
                                   "active": not m["left_at"], "ready": norms.readiness(m["fsp_id"])["ready"] if p else False})
    else:
        del out["members"]
    return out


@router.get("/organizations/{name}/card")
async def organization_card(name: str):
    """Карточка организации: тип, лицензия и её срок, программы подготовки, тренеры-преподаватели, группы и журнал в цифрах."""
    if name not in ORGANIZATIONS or name == NO_ORGANIZATION:
        raise HTTPException(404, "Организация не найдена")
    today, info, card = config.today(), ORGANIZATION_INFO.get(name, {}), _card(name) or {}
    people = [p for p in store.persons.values() if p.organization == name]
    groups = [g for g in store.ext.table(GROUPS).values() if g["org"] == name]
    sessions = [s for s in store.ext.table(SESSIONS).values() if store.ext.table(GROUPS).get(s["group_id"], {}).get("org") == name]
    return {
        "name": name, "type": info.get("type", "—"), "municipality": info.get("municipality", "—"),
        "license": card.get("license"), "license_state": _license_state(card.get("license"), today), "programs": [
            {**p, "stage_label": TRAINING_STAGES[p["stage"]], "discipline_label": DISCIPLINE_BY_CODE[p["discipline"]]["short"]}
            for p in card.get("programs", [])],
        "description": card.get("description", ""),
        "trainers": [{"fsp_id": p.fsp_id, "full_name": _full(p), "disciplines": p.disciplines,
                      "wards": sum(lk.trainer_fsp_id == p.fsp_id for lk in store.links if lk.status == "ACTIVE")}
                     for p in people if Role.TRAINER in p.roles],
        "athletes": sum(Role.ATHLETE in p.roles for p in people), "groups": [present_group(g) for g in sorted(groups, key=lambda g: g["name"])],
        "sessions": {"total": len(sessions), "offline": sum(s["format"] == "OFFLINE" for s in sessions),
                     "online": sum(s["format"] == "ONLINE" for s in sessions)},
    }


@router.put("/organizations/{name}/card")
async def put_card(name: str, data: CardIn):
    if name not in ORGANIZATIONS or name == NO_ORGANIZATION:
        raise HTTPException(404, "Организация не найдена")
    if not _admin(data.actor):
        raise HTTPException(403, "Карточку организации ведёт федерация")
    body = {"name": name, "license": data.license.model_dump(mode="json") if data.license else None,
            "programs": [p.model_dump() for p in data.programs], "description": data.description.strip(),
            "updated_at": datetime.now().isoformat(timespec="seconds")}
    if card := _card(name):
        card.update(body)
    else:
        store.ext.add(CARDS, body)
    store.log(data.actor, "Обновлена карточка организации", name)
    return await organization_card(name)


@router.get("/groups")
async def list_groups(org: str | None = None, trainer_fsp_id: str | None = None, stage: str | None = None, discipline: str | None = None,
                      athlete_fsp_id: str | None = None):
    rows = [g for g in store.ext.table(GROUPS).values() if not g["archived"]
            and (not org or g["org"] == org) and (not trainer_fsp_id or g["trainer_fsp_id"] == trainer_fsp_id)
            and (not stage or g["stage"] == stage) and (not discipline or g["discipline"] == discipline)
            and (not athlete_fsp_id or any(m["fsp_id"] == athlete_fsp_id and not m["left_at"] for m in g["members"]))]
    return [present_group(g) for g in sorted(rows, key=lambda g: (g["org"], g["name"]))]


@router.get("/groups/{gid}")
async def get_group(gid: int):
    return present_group(_group(gid), with_members=True)


@router.post("/groups", status_code=201)
async def create_group(data: GroupIn):
    card = _card(data.org)
    if data.org not in ORGANIZATIONS or data.org == NO_ORGANIZATION or not card:
        raise HTTPException(422, "Организация не найдена или у неё нет карточки")
    if data.stage not in TRAINING_STAGES or data.discipline not in DISCIPLINE_BY_CODE:
        raise HTTPException(422, "Неизвестный этап или дисциплина")
    trainer = store.persons.get(data.trainer_fsp_id)
    if not trainer or Role.TRAINER not in trainer.roles:
        raise HTTPException(422, "Тренером группы может быть участник с ролью «Тренер»")
    if not (_admin(data.actor) or data.actor == data.trainer_fsp_id):
        raise HTTPException(403, "Группу создаёт федерация или её будущий тренер")
    if {"stage": data.stage, "discipline": data.discipline} not in card["programs"]:
        raise HTTPException(422, "В карточке организации нет такой программы подготовки (этап × дисциплина)")
    if any(g["org"] == data.org and g["name"].lower() == data.name.strip().lower() and not g["archived"] for g in store.ext.table(GROUPS).values()):
        raise HTTPException(409, "Группа с таким названием уже есть")
    g = store.ext.add(GROUPS, {"org": data.org, "name": data.name.strip(), "stage": data.stage, "discipline": data.discipline,
                               "trainer_fsp_id": data.trainer_fsp_id, "members": [], "archived": False,
                               "created_at": datetime.now().isoformat(timespec="seconds")})
    store.log(data.actor, "Создана учебно-тренировочная группа", f"{data.org}: {g['name']} ({data.stage}, {data.discipline})")
    return present_group(g, with_members=True)


def _enroll(g: dict, athlete, actor: str) -> None:
    if Role.ATHLETE not in athlete.roles:
        raise HTTPException(422, "Зачислять можно только спортсменов")
    if athlete.organization != g["org"]:
        raise HTTPException(422, "Спортсмен числится в другой организации — сначала измените организацию в профиле")
    if g["discipline"] not in athlete.disciplines:
        raise HTTPException(422, "Дисциплина группы не входит в дисциплины спортсмена")
    for other in store.ext.table(GROUPS).values():
        if other["id"] != g["id"] and other["discipline"] == g["discipline"] and any(
                m["fsp_id"] == athlete.fsp_id and not m["left_at"] for m in other["members"]):
            raise HTTPException(409, f"Спортсмен уже занимается в группе «{other['name']}» по этой дисциплине — оформите перевод")
    if athlete.training_stage and athlete.training_stage != g["stage"]:
        higher = next_stage(athlete.training_stage) == g["stage"]
        raise HTTPException(422, "Перевод на следующий этап оформляется аттестацией по нормативам" if higher
                            else f"Этап спортсмена «{athlete.training_stage}» не совпадает с этапом группы «{g['stage']}»")
    if any(m["fsp_id"] == athlete.fsp_id and not m["left_at"] for m in g["members"]):
        raise HTTPException(409, "Спортсмен уже в этой группе")
    today = config.today()
    g["members"].append({"fsp_id": athlete.fsp_id, "enrolled_at": today.isoformat(), "left_at": None, "left_reason": ""})
    athlete.training_stage = g["stage"]
    store.log(actor, "Зачислен в группу", f"{g['name']}: {_full(athlete)}")


@router.post("/groups/{gid}/members", status_code=201)
async def enroll(gid: int, data: MemberIn):
    g = _group(gid)
    _may_run(g, data.actor)
    athlete = store.persons.get(data.fsp_id)
    if not athlete:
        raise HTTPException(404, "Спортсмен не найден в реестре")
    _enroll(g, athlete, data.actor)
    return present_group(g, with_members=True)


@router.post("/groups/{gid}/members/{fid}/leave")
async def leave(gid: int, fid: str, data: LeaveIn):
    g = _group(gid)
    _may_run(g, data.actor)
    m = next((m for m in g["members"] if m["fsp_id"] == fid and not m["left_at"]), None)
    if not m:
        raise HTTPException(404, "Спортсмен не состоит в группе")
    m["left_at"], m["left_reason"] = config.today().isoformat(), data.reason.strip() or "выбыл из группы"
    store.log(data.actor, "Отчислен из группы", f"{g['name']}: {fid}")
    return present_group(g, with_members=True)


@router.post("/groups/{gid}/transfer")
async def transfer(gid: int, data: TransferIn):
    """Перевод в другую группу той же организации: в пределах этапа — сразу, на следующий этап — только после аттестации по нормативам."""
    src, dst = _group(gid), _group(data.to_group_id)
    _may_run(src, data.actor)
    if src["org"] != dst["org"] or src["id"] == dst["id"]:
        raise HTTPException(422, "Перевод возможен в другую группу той же организации")
    if src["discipline"] != dst["discipline"]:
        raise HTTPException(422, "Группы должны вести одну дисциплину")
    athlete = store.persons.get(data.fsp_id)
    m = next((m for m in src["members"] if m["fsp_id"] == data.fsp_id and not m["left_at"]), None)
    if not athlete or not m:
        raise HTTPException(404, "Спортсмен не состоит в исходной группе")
    attestation = None
    if dst["stage"] != src["stage"]:
        if next_stage(src["stage"]) != dst["stage"]:
            raise HTTPException(422, "Перевод возможен в группу того же или следующего этапа")
        state = norms.readiness(data.fsp_id)
        if not state["ready"]:
            raise HTTPException(409, state["reason"] or "Нормативы этапа не выполнены")
        attestation = norms.transfer_stage(athlete, state, data.actor, data.note)  # закрывает членство в src, ставит новый этап
    else:
        m["left_at"], m["left_reason"] = config.today().isoformat(), f"переведён в группу «{dst['name']}»"
    _enroll(dst, athlete, data.actor)
    return {"group": present_group(dst, with_members=True), "attestation": attestation}


# ---------------------------------------------------------------- журнал занятий
def present_session(s: dict) -> dict:
    g = store.ext.table(GROUPS).get(s["group_id"], {})
    names = {fid: _full(store.persons[fid]) for fid in s["attendees"] if fid in store.persons}
    return {**s, "format_label": FORMATS[s["format"]], "confirmation_label": CONFIRMATIONS.get(s["confirmation_kind"] or "", "не подтверждено"),
            "group_name": g.get("name", "—"), "org": g.get("org", "—"), "attendee_names": [names.get(f, f) for f in s["attendees"]],
            "confirmed": bool(s["confirmation_kind"])}


@router.get("/groups/{gid}/sessions")
async def group_sessions(gid: int):
    _group(gid)
    rows = sorted((s for s in store.ext.table(SESSIONS).values() if s["group_id"] == gid), key=lambda s: (s["date"], s["id"]), reverse=True)
    return [present_session(s) for s in rows]


@router.post("/groups/{gid}/sessions", status_code=201)
async def add_session(gid: int, data: SessionIn):
    g = _group(gid)
    _may_run(g, data.actor)
    if data.on > config.today():
        raise HTTPException(422, "Занятие нельзя внести заранее: журнал ведётся по проведённым занятиям")
    present = {m["fsp_id"] for m in active_members(g, data.on)}
    outsiders = [f for f in data.attendees if f not in present]
    if outsiders:
        raise HTTPException(422, "Отмечены спортсмены, которых в этот день не было в группе: " + ", ".join(outsiders))
    s = store.ext.add(SESSIONS, {"group_id": gid, "date": data.on.isoformat(), "topic": data.topic.strip(), "format": data.format,
                                 "duration_min": data.duration_min, "confirmation_kind": data.confirmation_kind,
                                 "confirmation_ref": data.confirmation_ref.strip(), "attendees": list(dict.fromkeys(data.attendees)),
                                 "created_by": data.actor, "created_at": datetime.now().isoformat(timespec="seconds")})
    store.log(data.actor, "Занесено занятие в журнал", f"{g['name']}: {s['topic']} ({FORMATS[s['format']].lower()}, {len(s['attendees'])} чел.)")
    return present_session(s)


def report_data() -> dict:
    """Данные групп и журнала для отчёта о численности: членство с датами и занятия с посещаемостью."""
    groups = store.ext.table(GROUPS)
    return {
        "memberships": [{"fsp_id": m["fsp_id"], "stage": g["stage"], "org": g["org"], "discipline": g["discipline"],
                         "enrolled_at": m["enrolled_at"], "left_at": m["left_at"]} for g in groups.values() for m in g["members"]],
        "sessions": [{"date": s["date"], "format": s["format"], "confirmed": bool(s["confirmation_kind"]), "attendees": s["attendees"],
                      "org": groups.get(s["group_id"], {}).get("org"), "discipline": groups.get(s["group_id"], {}).get("discipline")}
                     for s in store.ext.table(SESSIONS).values()],
    }

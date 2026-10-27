"""Команды участников командных дисциплин (алгоритмика, продуктовое программирование, CTF и ИИ): капитан создаёт команду и приглашает спортсменов
по ФСП ID, приглашённый принимает или отклоняет, участник может выйти, капитан — исключить или распустить команду.

Не путать со сборной (`competition_service`, «Team»): сборную формирует федерация приказом, команду — сами спортсмены.
Правило одно: спортсмен состоит не более чем в одной команде каждой дисциплины. Хранение — таблица `squads` в `store.ext`.
"""
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from shared.domain import DISCIPLINE_BY_CODE, TEAM_DISCIPLINES, TEAM_MAX_SIZE, Role
from sportsman_service.store import store

router = APIRouter(prefix="/api/v1")
SQUADS = "squads"
LINK = "/squads"


class SquadIn(BaseModel):
    title: str = Field(min_length=3, max_length=50)
    discipline: str
    actor: str

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 3 or not any(ch.isalnum() for ch in v):
            raise ValueError("Название команды: от 3 символов, с буквами или цифрами")
        return v


class ActorIn(BaseModel):
    actor: str


class MemberIn(BaseModel):
    fsp_id: str
    actor: str


class RespondIn(BaseModel):
    accept: bool
    actor: str


def _full(p) -> str:
    return f"{p.last_name} {p.first_name} {p.middle_name}".strip()


def _short(fid: str) -> str:
    p = store.persons.get(fid)
    return f"{p.last_name} {p.first_name[0]}." if p else fid


def _squad(sid: int) -> dict:
    s = store.ext.table(SQUADS).get(sid)
    if not s:
        raise HTTPException(404, "Команда не найдена")
    return s


def _athlete(fid: str):
    p = store.persons.get(fid)
    if not p:
        raise HTTPException(404, f"Участник {fid} не найден в реестре")
    if Role.ATHLETE not in p.roles:
        raise HTTPException(403, "Команду собирают спортсмены: нужна роль «Спортсмен»")
    return p


def _admin(fid: str) -> bool:
    p = store.persons.get(fid)
    return bool(p and Role.ADMIN in p.roles)


def _captain_only(s: dict, actor: str, admin_ok: bool = False) -> None:
    if actor != s["captain"] and not (admin_ok and _admin(actor)):
        raise HTTPException(403, "Это действие доступно капитану команды")


def squad_of(fid: str, discipline: str) -> dict | None:
    return next((s for s in store.ext.table(SQUADS).values() if s["discipline"] == discipline and fid in s["members"]), None)


def _drop_invites(fid: str, discipline: str) -> None:
    for s in store.ext.table(SQUADS).values():
        if s["discipline"] == discipline:
            s["invites"] = [i for i in s["invites"] if i["fsp_id"] != fid]


def present(s: dict) -> dict:
    members = []
    for fid in s["members"]:
        p = store.persons.get(fid)
        members.append({"fsp_id": fid, "full_name": _full(p) if p else fid, "captain": fid == s["captain"],
                        "rank": p.rank.code if p and p.rank else None, "municipality": p.municipality if p else "",
                        "organization": p.organization if p else ""})
    invites = [{**i, "full_name": _full(store.persons[i["fsp_id"]]) if i["fsp_id"] in store.persons else i["fsp_id"],
                "by_name": _short(i["by"])} for i in s["invites"]]
    return {**s, "discipline_label": DISCIPLINE_BY_CODE[s["discipline"]]["short"], "members": members, "invites": invites,
            "captain_name": _short(s["captain"]), "size": len(members), "max_size": TEAM_MAX_SIZE,
            "full": len(members) >= TEAM_MAX_SIZE}


def _remove_member(s: dict, fid: str, reason: str) -> None:
    """Убирает участника; пустая команда распускается, капитан передаётся следующему по списку."""
    s["members"].remove(fid)
    if not s["members"]:
        del store.ext.table(SQUADS)[s["id"]]
        return
    if s["captain"] == fid:
        s["captain"] = s["members"][0]
        store.notify([s["captain"]], f"Вы капитан команды «{s['title']}»", f"{reason}. Капитаном стали вы.", LINK)


def on_disciplines_changed(fid: str, removed: set[str]) -> None:
    """Спортсмен снял дисциплину: выходит из команд этой дисциплины и теряет приглашения в них."""
    for d in removed & set(TEAM_DISCIPLINES):
        _drop_invites(fid, d)
        s = squad_of(fid, d)
        if s:
            _remove_member(s, fid, f"{_short(fid)} сменил(а) дисциплины и вышел(ла) из команды")
            store.notify(s["members"], f"{_short(fid)} вышел(ла) из команды «{s['title']}»", "Участник убрал дисциплину из профиля.", LINK)
            store.log(fid, "Выход из команды", f"«{s['title']}»: снята дисциплина {d}")


@router.get("/squads")
async def list_squads(discipline: str | None = None, member: str | None = None, q: str | None = None):
    if discipline and discipline not in TEAM_DISCIPLINES:
        raise HTTPException(422, "Команды есть в алгоритмике, продуктовом программировании, CTF и ИИ")
    words = (q or "").casefold().split()
    out = [s for s in store.ext.table(SQUADS).values()
           if (not discipline or s["discipline"] == discipline) and (not member or member in s["members"])
           and all(w in s["title"].casefold() for w in words)]
    return [present(s) for s in sorted(out, key=lambda s: (s["discipline"], s["title"].casefold()))]


@router.get("/squads/{sid}")
async def get_squad(sid: int):
    return present(_squad(sid))


@router.get("/athletes/{fid}/squads")
async def athlete_squads(fid: str):
    """Команды спортсмена, входящие приглашения и командные дисциплины, в которых он может собрать или найти команду."""
    p = store.persons.get(fid)
    if not p:
        raise HTTPException(404, f"Участник {fid} не найден в реестре")
    mine = [s for s in store.ext.table(SQUADS).values() if fid in s["members"]]
    invites = [{"invited_at": i["at"], "by_name": _short(i["by"]), "squad": present(s)}
               for s in store.ext.table(SQUADS).values() for i in s["invites"] if i["fsp_id"] == fid]
    open_for = [d for d in TEAM_DISCIPLINES if d in p.disciplines and not squad_of(fid, d)]
    return {"squads": [present(s) for s in mine], "invites": invites, "can_create": open_for,
            "team_disciplines": [d for d in TEAM_DISCIPLINES if d in p.disciplines]}


@router.post("/squads", status_code=201)
async def create_squad(data: SquadIn):
    if data.discipline not in TEAM_DISCIPLINES:
        raise HTTPException(422, "Команды собирают в алгоритмике, продуктовом программировании, CTF и ИИ")
    p = _athlete(data.actor)
    if data.discipline not in p.disciplines:
        raise HTTPException(422, f"Сначала выберите дисциплину «{DISCIPLINE_BY_CODE[data.discipline]['short']}» в профиле")
    if squad_of(p.fsp_id, data.discipline):
        raise HTTPException(409, "Вы уже состоите в команде этой дисциплины — сначала выйдите из неё")
    if any(s["discipline"] == data.discipline and s["title"].casefold() == data.title.casefold()
           for s in store.ext.table(SQUADS).values()):
        raise HTTPException(409, "Команда с таким названием уже есть в этой дисциплине")
    s = store.ext.add(SQUADS, {"title": data.title, "discipline": data.discipline, "captain": p.fsp_id, "members": [p.fsp_id],
                               "invites": [], "created_at": datetime.now().isoformat(timespec="seconds")})
    _drop_invites(p.fsp_id, data.discipline)
    store.log(p.fsp_id, "Создана команда", f"«{s['title']}» · {data.discipline}")
    return present(s)


@router.post("/squads/{sid}/invite", status_code=201)
async def invite(sid: int, data: MemberIn):
    s = _squad(sid)
    _captain_only(s, data.actor)
    fid = data.fsp_id.strip()
    target = store.persons.get(fid)
    if not target:
        raise HTTPException(404, f"Участник {fid} не найден: проверьте ФСП ID")
    if Role.ATHLETE not in target.roles:
        raise HTTPException(422, "Пригласить можно только спортсмена")
    if s["discipline"] not in target.disciplines:
        raise HTTPException(422, f"{_short(fid)} не занимается дисциплиной «{DISCIPLINE_BY_CODE[s['discipline']]['short']}»")
    if fid in s["members"]:
        raise HTTPException(409, f"{_short(fid)} уже в команде")
    if squad_of(fid, s["discipline"]):
        raise HTTPException(409, f"{_short(fid)} уже состоит в другой команде этой дисциплины")
    if any(i["fsp_id"] == fid for i in s["invites"]):
        raise HTTPException(409, f"{_short(fid)} уже приглашён(а)")
    if len(s["members"]) >= TEAM_MAX_SIZE:
        raise HTTPException(409, f"В команде не больше {TEAM_MAX_SIZE} человек")
    s["invites"].append({"fsp_id": fid, "by": data.actor, "at": datetime.now().isoformat(timespec="seconds")})
    store.notify([fid], f"Приглашение в команду «{s['title']}»",
                 f"{_short(s['captain'])} зовёт вас в команду по дисциплине «{DISCIPLINE_BY_CODE[s['discipline']]['short']}».", LINK)
    store.log(data.actor, "Приглашение в команду", f"«{s['title']}» → {fid}")
    return present(s)


@router.post("/squads/{sid}/respond")
async def respond(sid: int, data: RespondIn):
    s = _squad(sid)
    if not any(i["fsp_id"] == data.actor for i in s["invites"]):
        raise HTTPException(404, "Приглашения в эту команду нет — возможно, капитан его отозвал")
    if data.accept:
        if squad_of(data.actor, s["discipline"]):
            raise HTTPException(409, "Вы уже состоите в команде этой дисциплины")
        if len(s["members"]) >= TEAM_MAX_SIZE:
            raise HTTPException(409, "Команда уже заполнена")
        s["members"].append(data.actor)
        _drop_invites(data.actor, s["discipline"])
        store.notify([s["captain"]], f"{_short(data.actor)} в команде «{s['title']}»", "Приглашение принято.", LINK)
        store.log(data.actor, "Вступление в команду", f"«{s['title']}»")
    else:
        s["invites"] = [i for i in s["invites"] if i["fsp_id"] != data.actor]
        store.notify([s["captain"]], f"{_short(data.actor)} отклонил(а) приглашение", f"Команда «{s['title']}».", LINK)
        store.log(data.actor, "Приглашение отклонено", f"«{s['title']}»")
    return present(s)


@router.post("/squads/{sid}/revoke")
async def revoke(sid: int, data: MemberIn):
    s = _squad(sid)
    _captain_only(s, data.actor)
    if not any(i["fsp_id"] == data.fsp_id for i in s["invites"]):
        raise HTTPException(404, "Такого приглашения нет")
    s["invites"] = [i for i in s["invites"] if i["fsp_id"] != data.fsp_id]
    return present(s)


@router.post("/squads/{sid}/kick")
async def kick(sid: int, data: MemberIn):
    s = _squad(sid)
    _captain_only(s, data.actor, admin_ok=True)
    if data.fsp_id == s["captain"]:
        raise HTTPException(422, "Капитана нельзя исключить: передайте роль, выйдя из команды, или распустите её")
    if data.fsp_id not in s["members"]:
        raise HTTPException(404, "Этого участника нет в команде")
    _remove_member(s, data.fsp_id, "Исключение из команды")
    store.notify([data.fsp_id], f"Вас исключили из команды «{s['title']}»", "", LINK)
    store.log(data.actor, "Исключение из команды", f"«{s['title']}»: {data.fsp_id}")
    return present(s)


@router.post("/squads/{sid}/leave")
async def leave(sid: int, data: ActorIn):
    s = _squad(sid)
    if data.actor not in s["members"]:
        raise HTTPException(404, "Вы не состоите в этой команде")
    title, alone = s["title"], len(s["members"]) == 1
    _remove_member(s, data.actor, f"{_short(data.actor)} вышел(ла) из команды")
    if not alone:
        store.notify(s["members"], f"{_short(data.actor)} вышел(ла) из команды «{title}»", "", LINK)
    store.log(data.actor, "Выход из команды", f"«{title}»" + (" (распущена)" if alone else ""))
    return {"left": True, "disbanded": alone}


@router.post("/squads/{sid}/disband")
async def disband(sid: int, data: ActorIn):
    s = _squad(sid)
    _captain_only(s, data.actor, admin_ok=True)
    del store.ext.table(SQUADS)[sid]
    store.notify([f for f in s["members"] if f != data.actor] + [i["fsp_id"] for i in s["invites"]],
                 f"Команда «{s['title']}» распущена", "", LINK)
    store.log(data.actor, "Команда распущена", f"«{s['title']}»")
    return {"disbanded": True}


def seed(s) -> None:
    """Демо: команда CTF с приглашением (Валиева видит его в кабинете) и продуктовая команда."""
    from shared.demo_roster import ROSTER_BY_SEQ

    ids = {n: ROSTER_BY_SEQ[n]["fsp_id"] for n in (9, 13, 14, 16, 18)}
    at = datetime.now().isoformat(timespec="seconds")
    s.ext.add(SQUADS, {"title": "Null Pointers", "discipline": "SEC", "captain": ids[13], "members": [ids[13], ids[14]],
                       "invites": [{"fsp_id": ids[9], "by": ids[13], "at": at}], "created_at": at})
    s.ext.add(SQUADS, {"title": "Квант-Продукт", "discipline": "PRD", "captain": ids[16], "members": [ids[16], ids[18]],
                       "invites": [], "created_at": at})

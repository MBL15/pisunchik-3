"""Клубы: тренер сам заводит клуб как организацию спортивной подготовки.

Клуб — запись в таблице `clubs` (ext) + обычная карточка организации (`org_cards`) без лицензии и программ.
Название добавляется в общие списки организаций (`shared.domain.sync_clubs`), поэтому клуб сразу доступен в реестре,
при регистрации, в отчёте о численности и для создания групп. Карточку клуба (описание, программы) ведёт его основатель,
лицензию — только федерация.
"""
import re
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from shared.domain import MUNICIPALITIES, NO_ORGANIZATION, ORGANIZATIONS, Role, sync_clubs
from sportsman_service.groups import CARDS, _admin, _card
from sportsman_service.store import store

router = APIRouter(prefix="/api/v1")
CLUBS = "clubs"
MAX_CLUBS_PER_TRAINER = 5
NAME_RE = re.compile(r"^[\w «»\"'().,№&—–-]{3,80}$")


class ClubIn(BaseModel):
    name: str = Field(min_length=3, max_length=80)
    municipality: str
    description: str = Field(default="", max_length=500)
    actor: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip()
        if not NAME_RE.match(v) or not re.search(r"[^\W\d_]", v):
            raise ValueError("Название клуба — 3–80 символов: буквы, цифры, пробел и знаки «» \" ' ( ) . , № & -")
        return v

    @field_validator("municipality")
    @classmethod
    def _municipality(cls, v: str) -> str:
        if v not in MUNICIPALITIES:
            raise ValueError("Выберите муниципалитет из списка")
        return v


def clubs() -> list[dict]:
    return sorted(store.ext.table(CLUBS).values(), key=lambda c: c["id"])


def sync() -> None:
    """Приводит общие списки организаций в соответствие с сохранёнными клубами (при запуске и после создания)."""
    sync_clubs([{"name": c["name"], "municipality": c["municipality"]} for c in clubs()])


def founder_of(name: str) -> str | None:
    return next((c["founder_fsp_id"] for c in clubs() if c["name"] == name), None)


@router.get("/organizations-names")
async def organization_names():
    """Все названия организаций (справочник + клубы) — для списков выбора в шлюзе; «Без организации» последним."""
    return list(ORGANIZATIONS)


@router.get("/clubs")
async def list_clubs(founder: str | None = None):
    return [c for c in clubs() if not founder or c["founder_fsp_id"] == founder]


@router.post("/clubs", status_code=201)
async def create_club(data: ClubIn):
    actor = store.persons.get(data.actor)
    if not actor or not ({Role.TRAINER, Role.ADMIN} & set(actor.roles)):
        raise HTTPException(403, "Клуб создаёт тренер или федерация")
    if data.name.casefold() in {o.casefold() for o in ORGANIZATIONS}:
        raise HTTPException(409, f"Организация «{data.name}» уже есть в реестре")
    if not _admin(data.actor) and sum(c["founder_fsp_id"] == data.actor for c in clubs()) >= MAX_CLUBS_PER_TRAINER:
        raise HTTPException(409, f"Один тренер может создать не больше {MAX_CLUBS_PER_TRAINER} клубов")
    club = store.ext.add(CLUBS, {"name": data.name, "municipality": data.municipality, "founder_fsp_id": data.actor,
                                 "created_at": datetime.now().isoformat(timespec="seconds")})
    store.ext.add(CARDS, {"name": data.name, "license": None, "programs": [], "description": data.description.strip(),
                          "updated_at": club["created_at"]})
    sync()
    joined = actor.organization == NO_ORGANIZATION and Role.TRAINER in actor.roles
    if joined:
        actor.organization = data.name  # основатель — тренер клуба
    store.log(data.actor, "Создан клуб", f"{data.name} ({data.municipality})")
    return {**club, "joined": joined, "card": _card(data.name)}

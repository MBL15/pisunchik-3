"""Д8. Премии Губернатора (Указ Губернатора Оренбургской области от 04.03.2011 № 93-ук): справочник размеров, автоотбор по протоколам
за год, расчёт суммы и ходатайство со статусом «подготовлено → направлено → премия назначена».

Размеры в справочнике по умолчанию демонстрационные: перед боевым использованием их нужно сверить с Указом — правится только справочник.
Паспортные данные, ИНН и СНИЛС в пакете документов — только маски.
"""
import asyncio
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from competition_service.deps import clients
from competition_service.store import store
from shared import config
from shared.domain import CompetitionLevel, LEVEL_LABELS, REGION_CODE
from shared.http import ServiceError

router = APIRouter(prefix="/api/v1/awards")

DEFAULT_RATES: dict[str, list[int]] = {
    CompetitionLevel.CHAMP_RU: [100000, 70000, 50000],
    CompetitionLevel.FIRST_RU: [60000, 40000, 30000],
    CompetitionLevel.INTERREG: [30000, 20000, 10000],
    CompetitionLevel.REGIONAL: [10000, 7000, 5000],
}
PETITIONS, RATES = "award_petitions", "award_rates"
MASKS = {"passport": "** ** ******", "inn": "************", "snils": "***-***-*** **"}
STATUS_LABELS = {"PREPARED": "Подготовлено", "SENT": "Направлено", "AWARDED": "Премия назначена"}
NEXT_STATUS = {"PREPARED": "SENT", "SENT": "AWARDED"}


class RatesIn(BaseModel):
    rates: dict[str, list[int]]
    actor: str

    @field_validator("rates")
    @classmethod
    def _rates(cls, v: dict[str, list[int]]) -> dict[str, list[int]]:
        if set(v) != set(DEFAULT_RATES):
            raise ValueError("Укажите размеры для всех четырёх уровней соревнований")
        for level, row in v.items():
            if len(row) != 3 or any(x < 0 or x > 10_000_000 for x in row):
                raise ValueError(f"{LEVEL_LABELS[level]}: три неотрицательные суммы за 1, 2 и 3 место")
            if not row[0] >= row[1] >= row[2]:
                raise ValueError(f"{LEVEL_LABELS[level]}: за более высокое место сумма не может быть меньше")
        return v


class PetitionIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    exclude: list[str] = []
    note: str = Field(default="", max_length=300)
    actor: str


class PetitionStatusIn(BaseModel):
    status: Literal["SENT", "AWARDED"]
    order_no: str = Field(default="", max_length=60)
    actor: str


def rates() -> dict[str, list[int]]:
    rec = next(iter(store.ext.table(RATES).values()), None)
    return rec["rates"] if rec else {k.value: list(v) for k, v in DEFAULT_RATES.items()}


def deadline(year: int, today: date) -> dict:
    due = date(year + 1, 2, 1)
    return {"date": due.isoformat(), "days_left": (due - today).days}


async def candidates(year: int) -> dict:
    """Спортсмены региона с призовыми местами в опубликованных протоколах года и сумма премии по справочнику."""
    table, today = rates(), config.today()
    rows = [r for r in store.published_results(year) if r["status"] == "OK" and r["place"] in (1, 2, 3)
            and r["fsp_id"].split("-")[1] == REGION_CODE]
    persons: dict[str, dict] = {}
    checked = True
    try:
        persons = {p["fsp_id"]: p for p in await clients["sportsman"].get("/api/v1/persons", role="athlete")}
    except (ServiceError, KeyError):
        checked = False
    by_person: dict[str, dict] = {}
    for r in sorted(rows, key=lambda r: (r["date"], r["competition_id"])):
        amount = table[r["level"]][r["place"] - 1]
        item = by_person.setdefault(r["fsp_id"], {"fsp_id": r["fsp_id"], "full_name": r["full_name"], "results": [], "amount": 0})
        item["results"].append({"competition_id": r["competition_id"], "competition_code": r["competition_code"], "title": r["title"],
                                "date": r["date"], "level": r["level"], "level_label": r["level_label"], "place": r["place"],
                                "discipline_label": r["discipline_label"], "amount": amount})
        item["amount"] += amount
    items, excluded = [], []
    for fid, item in by_person.items():
        p = persons.get(fid)
        if p:
            item.update(birth_date=p["birth_date"], municipality=p["municipality"], organization=p["organization"])
            active = [s for s in p["sanctions"] if s["active"]]
            if active:
                excluded.append({**item, "reason": f"{active[0]['label']} до {active[0]['date_to']}: {active[0]['reason']}"})
                continue
        items.append(item)
    links = await asyncio.gather(*(clients["sportsman"].get(f"/api/v1/athletes/{i['fsp_id']}/links") for i in items),
                                 return_exceptions=True) if checked else []
    for item, ls in zip(items, links):
        item["coaches"] = sorted({lk["trainer_name"] for lk in ls if lk["status"] == "ACTIVE"}) if isinstance(ls, list) else []
    items.sort(key=lambda i: (-i["amount"], i["full_name"]))
    petition = next((p for p in store.ext.table(PETITIONS).values() if p["year"] == year), None)
    return {"year": year, "years": sorted({c.end_date.year for c in store.competitions.values()
                                           if (pr := store.protocols.get(c.id)) and pr.published}, reverse=True),
            "rates": table, "deadline": deadline(year, today), "items": items, "excluded": excluded,
            "total": sum(i["amount"] for i in items), "sanctions_checked": checked,
            "petition": present(petition) if petition else None}


def present(p: dict) -> dict:
    return {**p, "status_label": STATUS_LABELS[p["status"]], "next_status": NEXT_STATUS.get(p["status"]),
            "deadline": deadline(p["year"], config.today())}


@router.get("/rates")
async def get_rates():
    return {"rates": rates(), "levels": {k: LEVEL_LABELS[k] for k in DEFAULT_RATES}}


@router.put("/rates")
async def put_rates(data: RatesIn):
    table = store.ext.table(RATES)
    if table:
        next(iter(table.values()))["rates"] = data.rates
    else:
        store.ext.add(RATES, {"rates": data.rates})
    store.log(data.actor, "Изменён справочник премий Губернатора", "; ".join(f"{k}: {'/'.join(map(str, v))}" for k, v in data.rates.items()))
    return await get_rates()


@router.get("/candidates")
async def get_candidates(year: int | None = None):
    seasons = sorted({c.end_date.year for c in store.competitions.values() if (pr := store.protocols.get(c.id)) and pr.published},
                     reverse=True)
    return await candidates(year or (seasons[0] if seasons else config.today().year))


@router.get("/petitions")
async def list_petitions(year: int | None = None):
    rows = [present(p) for p in store.ext.table(PETITIONS).values() if year is None or p["year"] == year]
    return sorted(rows, key=lambda p: p["created_at"], reverse=True)


@router.get("/petitions/{pid}")
async def get_petition(pid: int):
    p = store.ext.table(PETITIONS).get(pid)
    if not p:
        raise HTTPException(404, "Ходатайство не найдено")
    return present(p)


@router.post("/petitions", status_code=201)
async def create_petition(data: PetitionIn):
    if any(p["year"] == data.year for p in store.ext.table(PETITIONS).values()):
        raise HTTPException(409, f"Ходатайство за {data.year} год уже подготовлено")
    found = await candidates(data.year)
    items = [{**i, **MASKS} for i in found["items"] if i["fsp_id"] not in set(data.exclude)]
    if not items:
        raise HTTPException(422, "В списке нет спортсменов, имеющих право на премию")
    petition = store.ext.add(PETITIONS, {
        "year": data.year, "status": "PREPARED", "items": items, "total": sum(i["amount"] for i in items), "rates": found["rates"],
        "note": data.note.strip(), "order_no": "", "created_by": data.actor, "created_at": datetime.now().isoformat(timespec="seconds"),
        "sent_at": None, "awarded_at": None})
    store.log(data.actor, "Подготовлено ходатайство на премии Губернатора", f"{data.year}: {len(items)} чел., {petition['total']} ₽")
    return present(petition)


@router.post("/petitions/{pid}/status")
async def set_petition_status(pid: int, data: PetitionStatusIn):
    p = store.ext.table(PETITIONS).get(pid)
    if not p:
        raise HTTPException(404, "Ходатайство не найдено")
    if NEXT_STATUS.get(p["status"]) != data.status:
        raise HTTPException(409, f"Из статуса «{STATUS_LABELS[p['status']]}» нельзя перейти в «{STATUS_LABELS[data.status]}»")
    now = datetime.now().isoformat(timespec="seconds")
    if data.status == "SENT":
        p["sent_at"] = now
    else:
        if not data.order_no.strip():
            raise HTTPException(422, "Укажите реквизиты акта о назначении премии")
        p["awarded_at"], p["order_no"] = now, data.order_no.strip()
    p["status"] = data.status
    store.log(data.actor, f"Ходатайство на премии: {STATUS_LABELS[data.status].lower()}", f"{p['year']}: {p['total']} ₽")
    return present(p)

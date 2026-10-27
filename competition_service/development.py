"""Д6. Показатели развития вида спорта: официальные соревнования региона за год и подтверждающие документы к показателям.

Численность и организации считает sportsman_service, шлюз собирает страницу из трёх источников; здесь — то, что относится к
календарю и протоколам, и общее хранилище вложений (ссылка или небольшой файл) для любого показателя.
"""
import base64
import binascii
from collections import Counter
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

from competition_service.store import store
from shared import config
from shared.domain import CompetitionStatus, LEVEL_STATUS_LABELS

router = APIRouter(prefix="/api/v1")

INDICATORS = {"headcount": "Численность занимающихся", "organizations": "Организации спортивной подготовки",
              "competitions": "Официальные соревнования"}
MEDIA_TYPES = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
               "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
               "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
MAX_FILE_BYTES = 2 * 1024 * 1024
TABLE = "attachments"


class AttachmentIn(BaseModel):
    indicator: str
    year: int = Field(ge=2000, le=2100)
    title: str = Field(min_length=3, max_length=120)
    url: str | None = Field(default=None, max_length=300)
    filename: str | None = Field(default=None, max_length=120)
    content_b64: str | None = None
    actor: str

    @model_validator(mode="after")
    def _check(self):
        if self.indicator not in INDICATORS:
            raise ValueError("Неизвестный показатель")
        if bool(self.url) == bool(self.content_b64):
            raise ValueError("Приложите файл или укажите ссылку — что-то одно")
        if self.url and not self.url.lower().startswith(("http://", "https://")):
            raise ValueError("Ссылка должна начинаться с http:// или https://")
        if self.content_b64:
            ext = (self.filename or "").rsplit(".", 1)[-1].lower()
            if ext not in MEDIA_TYPES:
                raise ValueError(f"Допустимые файлы: {', '.join(sorted(MEDIA_TYPES))}")
            try:
                size = len(base64.b64decode(self.content_b64, validate=True))
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Файл повреждён") from exc
            if not 0 < size <= MAX_FILE_BYTES:
                raise ValueError(f"Размер файла — не более {MAX_FILE_BYTES // 1024 // 1024} МБ")
        return self


def _meta(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "content_b64"}


@router.get("/development/competitions")
async def development_competitions(year: int | None = None):
    """Официальные соревнования региона за год: статус, уровень, календарный план и номер в нём, число участников."""
    years = sorted({c.end_date.year for c in store.competitions.values()}, reverse=True)
    year = year or config.today().year
    rows = []
    for c in sorted((c for c in store.competitions.values() if c.end_date.year == year), key=lambda c: c.start_date):
        p = store.present_competition(c)
        rows.append({k: p[k] for k in ("id", "code", "title", "discipline_label", "level", "level_label", "level_status", "format_label",
                                       "plan_label", "plan_number", "status", "status_label", "start_date", "end_date", "venue",
                                       "participants", "protocol_published", "calendar_plan")})
    held = [r for r in rows if r["status"] == CompetitionStatus.FINISHED]
    return {
        "year": year, "years": years, "items": rows,
        "kpi": {"total": len(rows), "held": len(held), "published_protocols": sum(r["protocol_published"] for r in rows),
                "participants": sum(r["participants"] for r in held),
                "by_level": dict(Counter(LEVEL_STATUS_LABELS[r["level"]] for r in rows)),
                "by_plan": dict(Counter(r["plan_label"] for r in rows))},
    }


@router.get("/attachments")
async def list_attachments(indicator: str | None = None, year: int | None = None):
    rows = [_meta(a) for a in store.ext.table(TABLE).values()
            if (not indicator or a["indicator"] == indicator) and (year is None or a["year"] == year)]
    return sorted(rows, key=lambda a: a["added_at"], reverse=True)


@router.post("/attachments", status_code=201)
async def add_attachment(data: AttachmentIn):
    size = len(base64.b64decode(data.content_b64)) if data.content_b64 else 0
    item = store.ext.add(TABLE, {
        "indicator": data.indicator, "year": data.year, "title": data.title.strip(), "url": data.url,
        "filename": data.filename if data.content_b64 else None, "size": size, "content_b64": data.content_b64,
        "added_by": data.actor, "added_at": datetime.now().isoformat(timespec="seconds")})
    store.log(data.actor, "Прикреплён документ к показателю", f"{INDICATORS[data.indicator]} {data.year}: {item['title']}")
    return _meta(item)


@router.get("/attachments/{aid}/file")
async def attachment_file(aid: int):
    item = store.ext.table(TABLE).get(aid)
    if not item or not item["content_b64"]:
        raise HTTPException(404, "Файл не найден")
    ext = item["filename"].rsplit(".", 1)[-1].lower()
    return {"filename": item["filename"], "media_type": MEDIA_TYPES.get(ext, "application/octet-stream"),
            "content_b64": item["content_b64"]}


@router.delete("/attachments/{aid}")
async def delete_attachment(aid: int, actor: str = Query(...)):
    item = store.ext.table(TABLE).pop(aid, None)
    if not item:
        raise HTTPException(404, "Документ не найден")
    store.log(actor, "Удалён документ показателя", f"{INDICATORS[item['indicator']]} {item['year']}: {item['title']}")
    return {"deleted": aid}

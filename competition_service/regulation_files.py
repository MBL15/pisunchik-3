"""Файл положения о соревновании: федерация загружает утверждённый документ, спортсмены и тренеры его скачивают.

Страница положения по-прежнему собирается из карточки соревнования; загруженный файл — официальная версия рядом с ней.
Файл один на соревнование: новая загрузка заменяет прежнюю.
"""
import base64
import binascii
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from competition_service.store import store

router = APIRouter(prefix="/api/v1")

TABLE = "regulation_files"
MEDIA_TYPES = {"pdf": "application/pdf",
               "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_FILE_BYTES = 4 * 1024 * 1024


class RegulationFileIn(BaseModel):
    filename: str = Field(min_length=3, max_length=120)
    content_b64: str
    actor: str

    @field_validator("filename")
    @classmethod
    def _extension(cls, v: str) -> str:
        if v.rsplit(".", 1)[-1].lower() not in MEDIA_TYPES:
            raise ValueError(f"Положение загружается файлом {' или '.join('.' + e for e in MEDIA_TYPES)}")
        return v

    @field_validator("content_b64")
    @classmethod
    def _size(cls, v: str) -> str:
        try:
            size = len(base64.b64decode(v, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Файл повреждён") from exc
        if not 0 < size <= MAX_FILE_BYTES:
            raise ValueError(f"Размер файла — не более {MAX_FILE_BYTES // 1024 // 1024} МБ")
        return v


def _find(cid: int) -> dict | None:
    return next((f for f in store.ext.table(TABLE).values() if f["competition_id"] == cid), None)


def _competition(cid: int):
    c = store.competitions.get(cid)
    if not c:
        raise HTTPException(404, "Соревнование не найдено")
    return c


def _meta(item: dict) -> dict:
    return {k: v for k, v in item.items() if k != "content_b64"}


@router.get("/competitions/{cid}/regulation-file")
async def regulation_file_info(cid: int):
    """Сведения о загруженном файле положения или `null`, если его нет."""
    _competition(cid)
    item = _find(cid)
    return _meta(item) if item else None


@router.put("/competitions/{cid}/regulation-file")
async def upload_regulation_file(cid: int, data: RegulationFileIn):
    c = _competition(cid)
    old = _find(cid)
    if old:
        store.ext.table(TABLE).pop(old["id"], None)
    size = len(base64.b64decode(data.content_b64))
    item = store.ext.add(TABLE, {"competition_id": cid, "filename": data.filename, "size": size, "content_b64": data.content_b64,
                                 "uploaded_by": data.actor, "uploaded_at": datetime.now().isoformat(timespec="seconds")})
    store.log(data.actor, "Загружено положение" if not old else "Заменено положение", f"{c.code}: {data.filename}")
    return _meta(item)


@router.get("/competitions/{cid}/regulation-file/content")
async def regulation_file_content(cid: int):
    _competition(cid)
    item = _find(cid)
    if not item:
        raise HTTPException(404, "Файл положения не загружен")
    ext = item["filename"].rsplit(".", 1)[-1].lower()
    return {"filename": item["filename"], "media_type": MEDIA_TYPES.get(ext, "application/octet-stream"), "content_b64": item["content_b64"]}


@router.delete("/competitions/{cid}/regulation-file")
async def delete_regulation_file(cid: int, actor: str = Query(...)):
    c = _competition(cid)
    item = _find(cid)
    if not item:
        raise HTTPException(404, "Файл положения не загружен")
    store.ext.table(TABLE).pop(item["id"], None)
    store.log(actor, "Удалено положение", f"{c.code}: {item['filename']}")
    return {"deleted": cid}

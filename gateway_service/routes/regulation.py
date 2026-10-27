"""Файл положения о соревновании: загружает и удаляет федерация, скачивает любой посетитель (положение публичное)."""
import base64
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from gateway_service.web import back, clients, humanize, require
from shared.domain import Role
from shared.http import ServiceError

router = APIRouter()

MAX_UPLOAD = 4 * 1024 * 1024


def _page(cid: int) -> str:
    return f"/competitions/{cid}/regulation"


@router.post("/competitions/{cid}/regulation-file")
async def upload_regulation_file(request: Request, cid: int):
    user = await require(request, Role.ADMIN)
    upload = (await request.form()).get("file")
    if upload is None or not getattr(upload, "filename", ""):
        return back(_page(cid), "error", "Выберите файл положения (.pdf или .docx)")
    data = await upload.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        return back(_page(cid), "error", f"Файл больше {MAX_UPLOAD // 1024 // 1024} МБ")
    try:
        await clients["competition"].put(f"/api/v1/competitions/{cid}/regulation-file", json={
            "filename": upload.filename, "content_b64": base64.b64encode(data).decode(), "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(_page(cid), "error", humanize(exc.detail))
    return back(_page(cid), "ok", "Файл положения загружен")


@router.get("/competitions/{cid}/regulation-file")
async def download_regulation_file(cid: int):
    try:
        f = await clients["competition"].get(f"/api/v1/competitions/{cid}/regulation-file/content")
    except ServiceError as exc:
        if exc.unavailable:
            raise
        raise HTTPException(404, humanize(exc.detail)) from exc
    name = f["filename"].replace('"', "").replace("\r", "").replace("\n", "")
    return Response(base64.b64decode(f["content_b64"]), media_type=f["media_type"],
                    headers={"Content-Disposition": f'attachment; filename="{name.encode("ascii", "ignore").decode() or "regulation"}"; '
                                                    f"filename*=UTF-8''{quote(name)}",
                             "X-Content-Type-Options": "nosniff"})


@router.post("/competitions/{cid}/regulation-file/delete")
async def delete_regulation_file(request: Request, cid: int):
    user = await require(request, Role.ADMIN)
    try:
        await clients["competition"].request("DELETE", f"/api/v1/competitions/{cid}/regulation-file",
                                             params={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back(_page(cid), "error", humanize(exc.detail))
    return back(_page(cid), "ok", "Файл положения удалён")

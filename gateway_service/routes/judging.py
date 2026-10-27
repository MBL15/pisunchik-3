"""Д12. Документы о судействе: справка о составе судейской коллегии соревнования и отчёт о соревнованиях судьи (.xlsx / .docx / .pdf)."""
import asyncio

from fastapi import APIRouter, HTTPException, Request

from gateway_service import reports
from gateway_service.web import clients, download, known_format, require
from shared import config
from shared.domain import Role

router = APIRouter()


@router.get("/competitions/{cid}/panel.{fmt}")
async def judging_panel(request: Request, cid: int, fmt: str):
    """Справка о составе коллегии: федерации, министерству и судьям, входящим в эту коллегию."""
    known_format(fmt)
    user = await require(request, Role.ADMIN, Role.MINISTRY, Role.JUDGE)
    c = await clients["competition"].get(f"/api/v1/competitions/{cid}")
    if user["role"] == Role.JUDGE and not any(j["fsp_id"] == user["person"]["fsp_id"] for j in c["judges"]):
        raise HTTPException(403, "Справка доступна судьям этой коллегии, федерации и министерству")
    if not c["judges"]:
        raise HTTPException(422, "Судейская коллегия ещё не назначена")
    judges = await asyncio.gather(*(clients["sportsman"].get(f"/api/v1/persons/{j['fsp_id']}") for j in c["judges"]))
    return await download(reports.judging_panel_doc(c, list(judges), config.today()), fmt)


@router.get("/judges/{fid}/report.{fmt}")
async def judge_report(request: Request, fid: str, fmt: str):
    """Отчёт о соревнованиях судьи: самому судье, федерации и министерству."""
    known_format(fmt)
    user = await require(request, Role.ADMIN, Role.MINISTRY, Role.JUDGE)
    if user["role"] == Role.JUDGE and fid != user["person"]["fsp_id"]:
        raise HTTPException(403, "Отчёт судьи доступен ему самому, федерации и министерству")
    person, assignments = await asyncio.gather(clients["sportsman"].get(f"/api/v1/persons/{fid}"),
                                               clients["competition"].get(f"/api/v1/judges/{fid}/assignments"))
    if "judge" not in person["roles"]:
        raise HTTPException(422, "Отчёт о судействе формируется только для судьи")
    return await download(reports.judge_report_doc(person, assignments, config.today()), fmt)

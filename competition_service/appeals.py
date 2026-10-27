"""Д5. Апелляции и фиксация нарушений.

Апелляция: подача с доказательствами → рассмотрение → решение с обоснованием → при необходимости корректировка протокола.
Рапорт о нарушении: рапорт с доказательствами → рассмотрение → решение (дисквалификация, техническое поражение, нарушения нет).
У каждого шага есть статус и запись в истории; уведомления рассылает шлюз. Корректировка протокола меняет итоговую таблицу
(места пересчитываются) и фиксируется в `Protocol.corrections` со ссылкой на апелляцию или рапорт.
"""
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from competition_service.deps import clients
from competition_service.models import Protocol
from competition_service.store import store
from shared import config
from shared.domain import LinkStatus
from shared.http import ServiceError

router = APIRouter(prefix="/api/v1")

APPEALS, VIOLATIONS = "appeals", "violations"
EVIDENCE_KINDS = {"LOG": "Лог", "SCREENSHOT": "Скриншот", "VIDEO": "Видео", "OTHER": "Другое"}
APPEAL_STATUSES = {"SUBMITTED": "Подана", "IN_REVIEW": "На рассмотрении", "ACCEPTED": "Удовлетворена", "REJECTED": "Отклонена",
                   "WITHDRAWN": "Отозвана"}
VIOLATION_STATUSES = {"REPORTED": "Рапорт подан", "IN_REVIEW": "На рассмотрении", "DECIDED": "Решение принято"}
DECISIONS = {"DSQ": "Дисквалификация", "TECH_DEFEAT": "Техническое поражение", "NO_VIOLATION": "Нарушение не подтверждено"}
CORRECTIONS = {"DSQ": "дисквалификация результата", "RESTORE": "снятие дисквалификации", "RESCORE": "пересчёт результата",
               "TECH_DEFEAT": "техническое поражение (результат аннулирован)"}


class Evidence(BaseModel):
    kind: Literal["LOG", "SCREENSHOT", "VIDEO", "OTHER"] = "OTHER"
    title: str = Field(min_length=2, max_length=100)
    url: str = Field(max_length=300)

    @field_validator("url")
    @classmethod
    def _http(cls, v: str) -> str:
        v = v.strip()
        if not v.lower().startswith(("http://", "https://")):
            raise ValueError("Ссылка на доказательство должна начинаться с http:// или https://")
        return v


class CorrectionIn(BaseModel):
    action: Literal["DSQ", "RESTORE", "RESCORE", "TECH_DEFEAT"]
    fsp_id: str | None = None  # по умолчанию — спортсмен апелляции или рапорта
    solved: int = Field(default=0, ge=0, le=26)
    penalty: int = Field(default=0, ge=0, le=100_000)
    score: float = Field(default=0, ge=0, le=10_000)
    reason: str = Field(default="", max_length=200)


class AppealIn(BaseModel):
    competition_id: int
    athlete_fsp_id: str
    subject: str = Field(min_length=5, max_length=160)
    description: str = Field(min_length=20, max_length=2000)
    evidence: list[Evidence] = Field(default=[], max_length=10)
    actor: str


class ViolationIn(BaseModel):
    competition_id: int
    subject_fsp_id: str
    description: str = Field(min_length=20, max_length=2000)
    evidence: list[Evidence] = Field(default=[], max_length=10)
    actor: str


class ActionIn(BaseModel):
    actor: str
    action: Literal["review", "withdraw"]


class AppealDecisionIn(BaseModel):
    actor: str
    accept: bool
    rationale: str = Field(min_length=10, max_length=1000)
    correction: CorrectionIn | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.correction and not self.accept:
            raise ValueError("Корректировка протокола возможна только при удовлетворении апелляции")
        return self


class ViolationDecisionIn(BaseModel):
    actor: str
    decision: Literal["DSQ", "TECH_DEFEAT", "NO_VIOLATION"]
    rationale: str = Field(min_length=10, max_length=1000)
    sanction_days: int = Field(default=0, ge=0, le=3650)  # при дисквалификации: срок санкции в реестре, 0 — только результат в протоколе
    reason: str = Field(default="", max_length=200)


def _competition(cid: int):
    c = store.competitions.get(cid)
    if not c:
        raise HTTPException(404, "Соревнование не найдено")
    return c


def _protocol(cid: int) -> Protocol:
    proto = store.protocols.get(cid)
    if not proto:
        raise HTTPException(422, "У соревнования ещё нет протокола — оспаривать и фиксировать нарушения пока нечего")
    return proto


def _event(item: dict, actor: str, status: str, note: str = "") -> None:
    item["history"].append({"at": datetime.now().isoformat(timespec="seconds"), "actor": actor, "status": status, "note": note})
    item["status"] = status


def present_appeal(a: dict) -> dict:
    c = store.competitions[a["competition_id"]]
    return {**a, "status_label": APPEAL_STATUSES[a["status"]], "competition_title": c.title, "competition_code": c.code,
            "evidence": [{**e, "kind_label": EVIDENCE_KINDS[e["kind"]]} for e in a["evidence"]],
            "open": a["status"] in ("SUBMITTED", "IN_REVIEW")}


def present_violation(v: dict) -> dict:
    c = store.competitions[v["competition_id"]]
    return {**v, "status_label": VIOLATION_STATUSES[v["status"]], "competition_title": c.title, "competition_code": c.code,
            "decision_label": DECISIONS.get(v["decision"] or "", ""),
            "evidence": [{**e, "kind_label": EVIDENCE_KINDS[e["kind"]]} for e in v["evidence"]],
            "open": v["status"] != "DECIDED"}


def apply_correction(cid: int, corr: CorrectionIn, default_fsp_id: str, ref: str, actor: str) -> dict:
    """Меняет результат одного участника и пересчитывает места. Протокол переходит на ручной ввод итогов (лог сохраняется)."""
    proto = _protocol(cid)
    if not proto.published and (cid in store.live_queue or cid in store.live_replay):
        raise HTTPException(409, "Соревнование идёт — протокол можно корректировать после его завершения")
    fid = corr.fsp_id or default_fsp_id
    if not any(e["fsp_id"] == fid for e in proto.entries):
        raise HTTPException(422, "Спортсмена нет в протоколе соревнования")
    if proto.source != "manual":
        proto.results = [{"fsp_id": e["fsp_id"], "solved": e["solved"], "penalty": e["penalty"], "score": e["score"],
                          "dsq": e["status"] == "DSQ", "dsq_reason": e["dsq_reason"] or ""} for e in proto.entries]
        proto.source = "manual"
    row = next(r for r in proto.results if r["fsp_id"] == fid)
    before = next(e for e in proto.entries if e["fsp_id"] == fid)
    if corr.action == "DSQ":
        row.update(dsq=True, dsq_reason=corr.reason or "Дисквалификация по решению федерации")
    elif corr.action == "RESTORE":
        row.update(dsq=False, dsq_reason="")
    elif corr.action == "RESCORE":
        row.update(solved=corr.solved, penalty=corr.penalty, score=corr.score, dsq=False, dsq_reason="")
    else:
        row.update(solved=0, penalty=0, score=0, dsq=False, dsq_reason="")
    store.reparse(proto)
    after = next(e for e in proto.entries if e["fsp_id"] == fid)
    note = {"at": datetime.now().isoformat(timespec="seconds"), "actor": actor, "ref": ref, "fsp_id": fid, "action": corr.action,
            "action_label": CORRECTIONS[corr.action], "before": {"place": before["place"], "status": before["status"]},
            "after": {"place": after["place"], "status": after["status"]}}
    proto.corrections.append(note)
    store.log(actor, f"Корректировка протокола: {CORRECTIONS[corr.action]}", f"{ref} · {fid} · место {before['place'] or 'DSQ'} → {after['place'] or 'DSQ'}")
    return note


# ---------------------------------------------------------------- апелляции
@router.get("/appeals")
async def list_appeals(competition_id: int | None = None, athlete_fsp_id: str | None = None, submitted_by: str | None = None,
                       status: str | None = None):
    rows = [a for a in store.ext.table(APPEALS).values()
            if (competition_id is None or a["competition_id"] == competition_id)
            and (not athlete_fsp_id or a["athlete_fsp_id"] == athlete_fsp_id)
            and (not submitted_by or a["submitted_by"] == submitted_by) and (not status or a["status"] == status)]
    return [present_appeal(a) for a in sorted(rows, key=lambda a: a["created_at"], reverse=True)]


@router.get("/appeals/{aid}")
async def get_appeal(aid: int):
    a = store.ext.table(APPEALS).get(aid)
    if not a:
        raise HTTPException(404, "Апелляция не найдена")
    return present_appeal(a)


@router.post("/appeals", status_code=201)
async def submit_appeal(data: AppealIn):
    c = _competition(data.competition_id)
    proto = _protocol(c.id)
    if not any(e["fsp_id"] == data.athlete_fsp_id for e in proto.entries):
        raise HTTPException(422, "Апелляцию можно подать только на результат, который есть в протоколе соревнования")
    if data.actor != data.athlete_fsp_id:
        try:
            links = await clients["sportsman"].get(f"/api/v1/athletes/{data.athlete_fsp_id}/links")
        except ServiceError as exc:
            raise HTTPException(exc.status if exc.status < 500 else 503, exc.detail) from exc
        if not any(lk["trainer_fsp_id"] == data.actor and lk["status"] == LinkStatus.ACTIVE for lk in links):
            raise HTTPException(403, "Апелляцию подаёт сам спортсмен или его тренер с активной привязкой")
    if any(a["competition_id"] == c.id and a["athlete_fsp_id"] == data.athlete_fsp_id and a["status"] in ("SUBMITTED", "IN_REVIEW")
           for a in store.ext.table(APPEALS).values()):
        raise HTTPException(409, "Апелляция на этот результат уже рассматривается")
    entry = next(e for e in proto.entries if e["fsp_id"] == data.athlete_fsp_id)
    item = {"competition_id": c.id, "athlete_fsp_id": data.athlete_fsp_id, "athlete_name": entry["full_name"], "subject": data.subject.strip(),
            "description": data.description.strip(), "evidence": [e.model_dump() for e in data.evidence], "submitted_by": data.actor,
            "created_at": datetime.now().isoformat(timespec="seconds"), "status": "SUBMITTED", "history": [], "decision": None}
    _event(item, data.actor, "SUBMITTED", "Апелляция подана")
    store.ext.add(APPEALS, item)
    store.log(data.actor, "Подана апелляция", f"{c.code} · {entry['full_name']}: {item['subject']}")
    return present_appeal(item)


@router.post("/appeals/{aid}/action")
async def appeal_action(aid: int, data: ActionIn):
    a = store.ext.table(APPEALS).get(aid)
    if not a:
        raise HTTPException(404, "Апелляция не найдена")
    if data.action == "withdraw":
        if data.actor not in (a["submitted_by"], a["athlete_fsp_id"]):
            raise HTTPException(403, "Отозвать апелляцию может её автор")
        if a["status"] != "SUBMITTED":
            raise HTTPException(409, "Отозвать можно апелляцию, которую ещё не взяли в рассмотрение")
        _event(a, data.actor, "WITHDRAWN", "Отозвана автором")
    else:
        if a["status"] != "SUBMITTED":
            raise HTTPException(409, f"Апелляция уже в статусе «{APPEAL_STATUSES[a['status']]}»")
        _event(a, data.actor, "IN_REVIEW", "Взята в рассмотрение")
    store.log(data.actor, f"Апелляция: {APPEAL_STATUSES[a['status']].lower()}", f"№{aid}")
    return present_appeal(a)


@router.post("/appeals/{aid}/decision")
async def decide_appeal(aid: int, data: AppealDecisionIn):
    a = store.ext.table(APPEALS).get(aid)
    if not a:
        raise HTTPException(404, "Апелляция не найдена")
    if a["status"] not in ("SUBMITTED", "IN_REVIEW"):
        raise HTTPException(409, f"Апелляция уже в статусе «{APPEAL_STATUSES[a['status']]}»")
    correction = None
    if data.correction:
        correction = apply_correction(a["competition_id"], data.correction, a["athlete_fsp_id"], f"апелляция №{aid}", data.actor)
    a["decision"] = {"accepted": data.accept, "rationale": data.rationale.strip(), "correction": correction,
                     "at": datetime.now().isoformat(timespec="seconds"), "by": data.actor}
    _event(a, data.actor, "ACCEPTED" if data.accept else "REJECTED", data.rationale.strip())
    store.log(data.actor, f"Апелляция {'удовлетворена' if data.accept else 'отклонена'}", f"№{aid}")
    return present_appeal(a)


# ---------------------------------------------------------------- рапорты о нарушениях
@router.get("/violations")
async def list_violations(competition_id: int | None = None, subject_fsp_id: str | None = None, reported_by: str | None = None,
                          status: str | None = None):
    rows = [v for v in store.ext.table(VIOLATIONS).values()
            if (competition_id is None or v["competition_id"] == competition_id)
            and (not subject_fsp_id or v["subject_fsp_id"] == subject_fsp_id)
            and (not reported_by or v["reported_by"] == reported_by) and (not status or v["status"] == status)]
    return [present_violation(v) for v in sorted(rows, key=lambda v: v["created_at"], reverse=True)]


@router.post("/violations", status_code=201)
async def report_violation(data: ViolationIn):
    c = _competition(data.competition_id)
    proto = _protocol(c.id)
    entry = next((e for e in proto.entries if e["fsp_id"] == data.subject_fsp_id), None)
    if not entry:
        raise HTTPException(422, "Нарушитель должен быть участником протокола соревнования")
    item = {"competition_id": c.id, "subject_fsp_id": data.subject_fsp_id, "subject_name": entry["full_name"],
            "description": data.description.strip(), "evidence": [e.model_dump() for e in data.evidence], "reported_by": data.actor,
            "created_at": datetime.now().isoformat(timespec="seconds"), "status": "REPORTED", "history": [], "decision": None,
            "rationale": "", "sanction": None}
    _event(item, data.actor, "REPORTED", "Рапорт подан")
    store.ext.add(VIOLATIONS, item)
    store.log(data.actor, "Подан рапорт о нарушении", f"{c.code} · {entry['full_name']}")
    return present_violation(item)


@router.post("/violations/{vid}/action")
async def violation_action(vid: int, data: ActionIn):
    v = store.ext.table(VIOLATIONS).get(vid)
    if not v:
        raise HTTPException(404, "Рапорт не найден")
    if data.action != "review" or v["status"] != "REPORTED":
        raise HTTPException(409, "Взять в рассмотрение можно только новый рапорт")
    _event(v, data.actor, "IN_REVIEW", "Взят в рассмотрение")
    store.log(data.actor, "Рапорт о нарушении: на рассмотрении", f"№{vid}")
    return present_violation(v)


@router.post("/violations/{vid}/decision")
async def decide_violation(vid: int, data: ViolationDecisionIn):
    v = store.ext.table(VIOLATIONS).get(vid)
    if not v:
        raise HTTPException(404, "Рапорт не найден")
    if v["status"] == "DECIDED":
        raise HTTPException(409, "По рапорту уже принято решение")
    correction = sanction = None
    if data.decision in ("DSQ", "TECH_DEFEAT"):
        corr = CorrectionIn(action=data.decision, reason=data.reason or data.rationale[:200])
        proto = _protocol(v["competition_id"])
        if not any(e["fsp_id"] == v["subject_fsp_id"] for e in proto.entries):
            raise HTTPException(422, "Нарушителя больше нет в протоколе")
        if data.decision == "DSQ" and data.sanction_days:
            today = config.today()
            sanction = {"type": "DISQUALIFICATION", "date_from": today.isoformat(), "reason": data.rationale[:200],
                        "date_to": (today + timedelta(days=data.sanction_days)).isoformat()}
            try:
                await clients["sportsman"].post(f"/api/v1/persons/{v['subject_fsp_id']}/sanctions", json={**sanction, "actor": data.actor})
            except ServiceError as exc:
                raise HTTPException(exc.status if exc.status < 500 else 503, exc.detail) from exc
        correction = apply_correction(v["competition_id"], corr, v["subject_fsp_id"], f"рапорт №{vid}", data.actor)
    v["decision"], v["rationale"], v["sanction"] = data.decision, data.rationale.strip(), sanction
    v["correction"] = correction
    _event(v, data.actor, "DECIDED", f"{DECISIONS[data.decision]}: {data.rationale.strip()}")
    store.log(data.actor, f"Решение по рапорту: {DECISIONS[data.decision].lower()}", f"№{vid} · {v['subject_name']}")
    return present_violation(v)

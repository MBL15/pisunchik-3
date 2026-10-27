"""Д4. Нормативы и аттестация по ФССП: справочник, ввод результатов, автоматическая оценка, готовность к переводу, перевод на этап.

Результаты вносит тренер (за подопечных с активной привязкой) или федерация; платформа сама сравнивает значение с порогом
этапа, дисциплины и пола. Перевод на следующий этап возможен, когда выполнены все обязательные применимые нормативы.
"""
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from shared import config
from shared.domain import DISCIPLINE_BY_CODE, TRAINING_STAGES, LinkStatus, Role
from shared.training import DIRECTION_LABELS, KIND_LABELS, STAGE_ORDER, applies, evaluate, latest_by_norm, next_stage
from sportsman_service.store import store

router = APIRouter(prefix="/api/v1")
NORMS, RESULTS, ATTESTATIONS = "norms", "norm_results", "attestations"


class NormIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    kind: Literal["GENERAL", "SPECIAL"]
    stage: str
    discipline: str = "ALL"
    gender: Literal["ALL", "М", "Ж"] = "ALL"
    unit: str = Field(min_length=1, max_length=20)
    direction: Literal["MIN", "MAX"]
    threshold: float = Field(ge=0, le=1_000_000)
    required: bool = True
    active: bool = True
    note: str = Field(default="", max_length=200)
    actor: str


class NormResultIn(BaseModel):
    norm_id: int
    athlete_fsp_id: str
    value: float = Field(ge=0, le=1_000_000)
    performed_on: date | None = None  # дата сдачи; по умолчанию — сегодня
    actor: str


class AttestationIn(BaseModel):
    actor: str
    note: str = Field(default="", max_length=300)


class StageIn(BaseModel):
    stage: str
    actor: str


def present_norm(n: dict) -> dict:
    return {**n, "kind_label": KIND_LABELS[n["kind"]], "stage_label": TRAINING_STAGES[n["stage"]],
            "discipline_label": "Общий" if n["discipline"] == "ALL" else DISCIPLINE_BY_CODE[n["discipline"]]["short"],
            "gender_label": {"ALL": "М и Ж", "М": "Мужской", "Ж": "Женский"}[n["gender"]],
            "direction_label": DIRECTION_LABELS[n["direction"]]}


def _validate_norm(data: NormIn) -> None:
    if data.stage not in TRAINING_STAGES:
        raise HTTPException(422, "Неизвестный этап подготовки")
    if data.discipline != "ALL" and data.discipline not in DISCIPLINE_BY_CODE:
        raise HTTPException(422, "Неизвестная дисциплина")


def _athlete(fid: str):
    p = store.persons.get(fid)
    if not p:
        raise HTTPException(404, f"Участник {fid} не найден в реестре")
    if Role.ATHLETE not in p.roles:
        raise HTTPException(422, "Нормативы сдают только спортсмены")
    return p


def _may_manage(actor: str, athlete_fid: str) -> bool:
    """Результаты вносит федерация или тренер подопечного с активной привязкой."""
    person = store.persons.get(actor)
    if person and Role.ADMIN in person.roles:
        return True
    return any(lk.trainer_fsp_id == actor and lk.athlete_fsp_id == athlete_fid and lk.status == LinkStatus.ACTIVE for lk in store.links)


def results_of(fid: str) -> list[dict]:
    return [r for r in store.ext.table(RESULTS).values() if r["athlete_fsp_id"] == fid]


def readiness(fid: str) -> dict:
    """Применимые нормативы спортсмена на его этапе, последние попытки и готовность к переводу на следующий этап."""
    p = _athlete(fid)
    stage, nxt = p.training_stage, next_stage(p.training_stage)
    applicable = [n for n in store.ext.table(NORMS).values() if applies(n, stage, p.disciplines, p.gender)]
    last = latest_by_norm(results_of(fid))
    rows = []
    for n in sorted(applicable, key=lambda n: (n["kind"], n["title"])):
        r = last.get(n["id"])
        rows.append({"norm": present_norm(n), "result": r, "passed": r["passed"] if r else None})
    missing = [r for r in rows if r["norm"]["required"] and not r["passed"]]
    if not stage:
        reason = "Этап спортивной подготовки не указан: зачислите спортсмена в группу или назначьте этап"
    elif not nxt:
        reason = "Высший этап подготовки"
    elif not rows:
        reason = "Для этапа и дисциплин спортсмена нет нормативов в справочнике"
    elif missing:
        reason = f"Не выполнено обязательных нормативов: {len(missing)} из {sum(r['norm']['required'] for r in rows)}"
    else:
        reason = ""
    return {"fsp_id": fid, "full_name": f"{p.last_name} {p.first_name} {p.middle_name}".strip(), "stage": stage,
            "stage_label": TRAINING_STAGES.get(stage, ""), "next_stage": nxt, "next_stage_label": TRAINING_STAGES.get(nxt, ""),
            "norms": rows, "passed": sum(bool(r["passed"]) for r in rows), "total": len(rows), "missing": len(missing),
            "ready": bool(nxt and rows and not missing), "reason": reason}


@router.get("/norms")
async def list_norms(stage: str | None = None, discipline: str | None = None, include_inactive: bool = False):
    rows = [present_norm(n) for n in store.ext.table(NORMS).values()
            if (not stage or n["stage"] == stage) and (not discipline or n["discipline"] in (discipline, "ALL"))
            and (include_inactive or n["active"])]
    order = {s: i for i, s in enumerate(STAGE_ORDER)}
    return sorted(rows, key=lambda n: (order[n["stage"]], n["kind"], n["title"], n["gender"], n["discipline"]))


@router.post("/norms", status_code=201)
async def create_norm(data: NormIn):
    _validate_norm(data)
    item = store.ext.add(NORMS, data.model_dump(exclude={"actor"}))
    store.log(data.actor, "Добавлен норматив", f"{data.title} · {data.stage}: {DIRECTION_LABELS[data.direction]} {data.threshold:g} {data.unit}")
    return present_norm(item)


@router.put("/norms/{nid}")
async def update_norm(nid: int, data: NormIn):
    _validate_norm(data)
    item = store.ext.table(NORMS).get(nid)
    if not item:
        raise HTTPException(404, "Норматив не найден")
    old = item["threshold"]
    item.update(data.model_dump(exclude={"actor"}))
    store.log(data.actor, "Изменён норматив", f"{data.title} · {data.stage}: порог {old:g} → {data.threshold:g} {data.unit}")
    return present_norm(item)


@router.get("/athletes/{fid}/training")
async def athlete_training(fid: str):
    """Профиль подготовки: этап, нормативы этапа с последними попытками, готовность к переводу, история попыток и аттестаций."""
    base = readiness(fid)
    norms = store.ext.table(NORMS)
    history = sorted(results_of(fid), key=lambda r: (r["date"], r["id"]), reverse=True)
    attest = sorted((a for a in store.ext.table(ATTESTATIONS).values() if a["athlete_fsp_id"] == fid), key=lambda a: a["id"], reverse=True)
    return {**base, "history": [{**r, "norm": present_norm(norms[r["norm_id"]])} for r in history if r["norm_id"] in norms],
            "attestations": [{**a, "from_label": TRAINING_STAGES[a["from_stage"]], "to_label": TRAINING_STAGES[a["to_stage"]]} for a in attest]}


@router.post("/norm-results", status_code=201)
async def add_result(data: NormResultIn):
    p = _athlete(data.athlete_fsp_id)
    norm = store.ext.table(NORMS).get(data.norm_id)
    if not norm or not norm["active"]:
        raise HTTPException(404, "Норматив не найден или отключён")
    if not _may_manage(data.actor, data.athlete_fsp_id):
        raise HTTPException(403, "Результаты вносит федерация или тренер подопечного с активной привязкой")
    if not applies(norm, p.training_stage, p.disciplines, p.gender):
        raise HTTPException(422, "Норматив не относится к этапу, дисциплинам и полу спортсмена")
    on = data.performed_on or config.today()
    if on > config.today():
        raise HTTPException(422, "Дата сдачи не может быть в будущем")
    passed = evaluate(norm["direction"], norm["threshold"], data.value)
    item = store.ext.add(RESULTS, {"athlete_fsp_id": data.athlete_fsp_id, "norm_id": norm["id"], "value": data.value, "date": on.isoformat(),
                                   "passed": passed, "threshold": norm["threshold"], "direction": norm["direction"], "unit": norm["unit"],
                                   "stage": norm["stage"], "recorded_by": data.actor,
                                   "recorded_at": datetime.now().isoformat(timespec="seconds")})
    store.log(data.actor, "Внесён результат норматива", f"{p.last_name} {p.first_name[0]}.: {norm['title']} = {data.value:g} {norm['unit']} "
                                                        f"({'выполнен' if passed else 'не выполнен'})")
    return {**item, "norm": present_norm(norm), "ready": readiness(data.athlete_fsp_id)["ready"]}


@router.delete("/norm-results/{rid}")
async def delete_result(rid: int, actor: str = Query(...)):
    r = store.ext.table(RESULTS).get(rid)
    if not r:
        raise HTTPException(404, "Результат не найден")
    if not _may_manage(actor, r["athlete_fsp_id"]):
        raise HTTPException(403, "Удалить результат может внёсший его тренер или федерация")
    del store.ext.table(RESULTS)[rid]
    store.log(actor, "Удалён результат норматива", f"№{rid} · {r['athlete_fsp_id']}")
    return {"deleted": rid}


@router.post("/athletes/{fid}/stage")
async def set_stage(fid: str, data: StageIn):
    """Начальное назначение этапа спортивнику без него (федерация); дальше этап меняют зачисление в группу и аттестация."""
    p = _athlete(fid)
    person = store.persons.get(data.actor)
    if not person or Role.ADMIN not in person.roles:
        raise HTTPException(403, "Назначить этап может федерация")
    if data.stage not in TRAINING_STAGES:
        raise HTTPException(422, "Неизвестный этап подготовки")
    if p.training_stage:
        raise HTTPException(409, "Этап уже указан: меняйте его зачислением в группу или аттестацией")
    p.training_stage = data.stage
    store.log(data.actor, "Назначен этап подготовки", f"{fid}: {data.stage}")
    return readiness(fid)


@router.post("/athletes/{fid}/attestation", status_code=201)
async def attest(fid: str, data: AttestationIn):
    """Промежуточная аттестация: перевод на следующий этап оформляет тренер подопечного или федерация, если норматив выполнен."""
    p = _athlete(fid)
    if not _may_manage(data.actor, fid):
        raise HTTPException(403, "Перевод оформляет федерация или тренер подопечного с активной привязкой")
    state = readiness(fid)
    if not state["ready"]:
        raise HTTPException(409, state["reason"] or "Спортсмен не готов к переводу")
    return transfer_stage(p, state, data.actor, data.note)


def transfer_stage(p, state: dict, actor: str, note: str = "") -> dict:
    """Фиксирует аттестацию и меняет этап; членство в группе прежнего этапа закрывается (спортсмена зачисляют в новую группу)."""
    from sportsman_service import groups  # отложенный импорт: groups использует готовность из этого модуля

    today = config.today()
    item = store.ext.add(ATTESTATIONS, {
        "athlete_fsp_id": p.fsp_id, "from_stage": state["stage"], "to_stage": state["next_stage"], "date": today.isoformat(), "decided_by": actor,
        "note": note.strip(), "norm_results": [r["result"]["id"] for r in state["norms"] if r["result"]]})
    p.training_stage = state["next_stage"]
    groups.leave_stage_groups(p.fsp_id, p.training_stage, today, f"перевод на этап «{p.training_stage}»")
    store.log(actor, "Перевод на следующий этап подготовки", f"{p.last_name} {p.first_name[0]}.: {item['from_stage']} → {item['to_stage']}")
    return {**item, "from_label": TRAINING_STAGES[item["from_stage"]], "to_label": TRAINING_STAGES[item["to_stage"]]}


@router.get("/training/overview")
async def training_overview(trainer_fsp_id: str | None = None, stage: str | None = None, ready: bool | None = None, q: str | None = None,
                            limit: int = Query(100, ge=1, le=500)):
    """Спортсмены с этапом и готовностью к переводу: все, подопечные тренера, готовые к аттестации."""
    scope = None
    if trainer_fsp_id:
        scope = {lk.athlete_fsp_id for lk in store.links if lk.trainer_fsp_id == trainer_fsp_id and lk.status == LinkStatus.ACTIVE}
    words = (q or "").lower().split()
    rows = []
    for p in store.persons.values():
        if Role.ATHLETE not in p.roles or (scope is not None and p.fsp_id not in scope) or (stage and p.training_stage != stage):
            continue
        name = f"{p.last_name} {p.first_name} {p.middle_name}".strip()
        if words and not all(w in f"{name} {p.fsp_id}".lower() for w in words):
            continue
        s = readiness(p.fsp_id)
        if ready is not None and s["ready"] != ready:
            continue
        rows.append({k: s[k] for k in ("fsp_id", "full_name", "stage", "stage_label", "next_stage", "next_stage_label", "passed", "total", "missing", "ready", "reason")}
                    | {"disciplines": p.disciplines, "organization": p.organization})
    rows.sort(key=lambda r: (not r["ready"], r["full_name"]))
    return {"total": len(rows), "ready": sum(r["ready"] for r in rows), "items": rows[:limit]}

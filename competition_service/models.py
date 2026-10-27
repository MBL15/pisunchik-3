from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from shared.domain import (
    AGE_GROUPS, DISCIPLINE_BY_CODE, RANK_BY_CODE, ApplicationStatus, CalendarPlan, CompetitionFormat, CompetitionLevel,
    CompetitionStatus,
)


class JudgeAssignment(BaseModel):
    fsp_id: str
    full_name: str
    position: str


class Competition(BaseModel):
    id: int
    code: str
    title: str
    discipline: str
    level: CompetitionLevel
    type: str
    format: CompetitionFormat
    format_note: str = ""
    calendar_plan: CalendarPlan
    plan_number: str
    start_date: date
    end_date: date
    venue: str
    age_group: str
    status: CompetitionStatus
    registration_deadline: date | None = None
    judges: list[JudgeAssignment] = []


class CompetitionCreateIn(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    discipline: str
    level: CompetitionLevel
    type: str
    format: CompetitionFormat
    format_note: str = Field(default="", max_length=200)
    calendar_plan: CalendarPlan
    plan_number: str = Field(min_length=1, max_length=40)
    start_date: date
    end_date: date
    venue: str = Field(min_length=2, max_length=200)
    age_group: str
    registration_deadline: date
    actor: str

    @model_validator(mode="after")
    def _check(self):
        if self.discipline not in DISCIPLINE_BY_CODE:
            raise ValueError("Неизвестная дисциплина")
        if self.age_group not in AGE_GROUPS:
            raise ValueError("Неизвестная возрастная группа")
        if self.end_date < self.start_date:
            raise ValueError("Дата окончания раньше даты начала")
        if self.registration_deadline > self.start_date:
            raise ValueError("Приём заявок должен закрываться до начала соревнования")
        if self.level in (CompetitionLevel.CHAMP_RU, CompetitionLevel.FIRST_RU, CompetitionLevel.INTERREG) \
                and self.calendar_plan != CalendarPlan.EKP:
            raise ValueError("Всероссийские и межрегиональные соревнования включаются в ЕКП Минспорта России")
        return self


class CompetitionTypeIn(BaseModel):
    title: str = Field(min_length=3, max_length=40)
    actor: str

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        v = " ".join(v.split())
        if not any(ch.isalpha() for ch in v):
            raise ValueError("Название типа должно содержать буквы")
        return v


class RegistrationIn(BaseModel):
    action: Literal["open", "close"]
    deadline: date | None = None  # новый срок окончания приёма при открытии или продлении
    actor: str


class Protocol(BaseModel):
    competition_id: int
    mode: str
    raw_log: list[str]
    names: dict[str, dict]
    entries: list[dict] = []
    warnings: list[str] = []
    events: int = 0
    last_time: str = "00:00:00"
    published: bool = False
    published_at: datetime | None = None
    updated_at: datetime
    source: str = "seed"  # seed | live | upload | manual
    results: list[dict] = []  # source=manual: totals entered by the federation


class ResultIn(BaseModel):
    fsp_id: str
    solved: int = Field(default=0, ge=0, le=26)
    penalty: int = Field(default=0, ge=0, le=100_000)
    score: float = Field(default=0, ge=0, le=10_000)
    dsq: bool = False
    dsq_reason: str = Field(default="", max_length=200)


class ResultsIn(BaseModel):
    results: list[ResultIn] = Field(min_length=1, max_length=500)
    actor: str
    publish: bool = False


class Application(BaseModel):
    id: int
    competition_id: int
    athlete_fsp_id: str
    athlete_name: str
    submitted_by: str
    status: ApplicationStatus
    created_at: datetime
    decided_at: datetime | None = None
    comment: str = ""


class ApplicationIn(BaseModel):
    competition_id: int
    athlete_fsp_id: str
    submitted_by: str


class DecisionIn(BaseModel):
    status: ApplicationStatus
    actor: str
    comment: str = Field(default="", max_length=300)


class BatchDecisionIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    status: ApplicationStatus
    actor: str
    comment: str = Field(default="", max_length=300)


class IngestIn(BaseModel):
    log: str = Field(min_length=10, max_length=200_000)
    actor: str


class ActorIn(BaseModel):
    actor: str


class JudgeAssignIn(BaseModel):
    fsp_id: str
    position: str
    actor: str


class SettingsIn(BaseModel):
    points: dict[CompetitionLevel, list[int]]
    coefficient: float = Field(ge=1.0, le=3.0)
    min_podiums: int = Field(ge=1, le=10)
    actor: str

    @model_validator(mode="after")
    def _check(self):
        for level, pts in self.points.items():
            if len(pts) != 3 or any(p < 0 or p > 1000 for p in pts):
                raise ValueError(f"{level}: нужно три значения баллов от 0 до 1000")
            if not pts[0] >= pts[1] >= pts[2]:
                raise ValueError(f"{level}: баллы за 1 место не могут быть меньше, чем за 2 и 3")
        return self


class TeamMember(BaseModel):
    """Снимок спортсмена на момент включения в состав: место и баллы рейтинга, разряд, тренеры."""
    fsp_id: str
    full_name: str
    role: Literal["MAIN", "RESERVE"] = "MAIN"
    position: int
    points: float
    rank_short: str
    municipality: str
    organization: str = ""
    birth_date: str | None = None
    coaches: list[str] = []


class Team(BaseModel):
    id: int
    title: str
    discipline: str
    season: int
    purpose: str = ""
    age_group: str | None = None  # фильтры рейтинга, из которого набран состав
    min_rank: str | None = None
    status: Literal["DRAFT", "APPROVED"] = "DRAFT"
    order_no: str = ""
    order_date: date | None = None
    members: list[TeamMember] = []
    created_by: str
    created_at: datetime
    updated_at: datetime


class TeamCreateIn(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    discipline: str
    season: int = Field(ge=2000, le=2100)
    purpose: str = Field(default="", max_length=300)
    age_group: str | None = None
    min_rank: str | None = None
    fsp_ids: list[str] = Field(min_length=1, max_length=40)
    actor: str

    @model_validator(mode="after")
    def _check(self):
        if self.discipline not in DISCIPLINE_BY_CODE:
            raise ValueError("Неизвестная дисциплина")
        if self.age_group and self.age_group not in AGE_GROUPS:
            raise ValueError("Неизвестная возрастная группа")
        if self.min_rank and self.min_rank not in RANK_BY_CODE:
            raise ValueError("Неизвестный разряд")
        if len(set(self.fsp_ids)) != len(self.fsp_ids):
            raise ValueError("Спортсмен указан в составе дважды")
        return self


class TeamUpdateIn(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    purpose: str = Field(default="", max_length=300)
    actor: str


class TeamMemberIn(BaseModel):
    fsp_id: str
    actor: str


class TeamRoleIn(BaseModel):
    role: Literal["MAIN", "RESERVE"]
    actor: str


class TeamApproveIn(BaseModel):
    order_no: str = Field(min_length=1, max_length=120)
    order_date: date
    actor: str

    @field_validator("order_no")
    @classmethod
    def _order_no(cls, v: str) -> str:
        v = v.strip()
        if not any(ch.isdigit() for ch in v):
            raise ValueError("Укажите номер приказа")
        return v


class DocumentIn(BaseModel):
    fsp_id: str
    competition_id: int
    issued_by: str
    purpose: str = Field(default="для предъявления по месту требования", max_length=120)

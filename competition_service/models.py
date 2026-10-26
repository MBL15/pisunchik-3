from datetime import date, datetime

from pydantic import BaseModel, Field, model_validator

from shared.domain import (
    AGE_GROUPS, COMPETITION_TYPES, DISCIPLINE_BY_CODE, ApplicationStatus, CalendarPlan, CompetitionFormat,
    CompetitionLevel, CompetitionStatus,
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
        if self.type not in COMPETITION_TYPES:
            raise ValueError("Неизвестный тип соревнования")
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


class DocumentIn(BaseModel):
    fsp_id: str
    competition_id: int
    issued_by: str
    purpose: str = Field(default="для предъявления по месту требования", max_length=120)

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from shared.domain import (
    DISCIPLINE_BY_CODE, MUNICIPALITIES, ORGANIZATIONS, SELF_SERVICE_ROLES, ApplicationStatus, LinkStatus,
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class RankRecord(BaseModel):
    code: str
    assigned_at: date
    expires_at: date | None
    order_no: str
    basis: str = ""  # за что присвоен: соревнование и результат из заявки


class JudgeCategoryRecord(BaseModel):
    code: str
    expires_at: date
    order_no: str = ""


class QualificationRequest(BaseModel):
    id: int
    kind: str  # RANK | JUDGE_CATEGORY
    person_fsp_id: str
    code: str
    purpose: str  # ASSIGN | CONFIRM
    basis: dict
    submitted_by: str
    status: ApplicationStatus
    created_at: datetime
    decided_at: datetime | None = None
    decided_by: str = ""
    comment: str = ""
    order_no: str = ""


class QualificationIn(BaseModel):
    kind: str = Field(pattern="^(RANK|JUDGE_CATEGORY)$")
    person_fsp_id: str
    code: str
    submitted_by: str
    competition_id: int | None = None
    note: str = Field(default="", max_length=300)


class QualificationDecisionIn(BaseModel):
    status: ApplicationStatus
    actor: str
    comment: str = Field(default="", max_length=300)
    order_no: str = Field(default="", max_length=120)
    valid_until: date | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.status == ApplicationStatus.PENDING:
            raise ValueError("Выберите решение: удовлетворить или отклонить")
        if self.status == ApplicationStatus.APPROVED and not self.order_no.strip():
            raise ValueError("Укажите номер приказа о присвоении")
        return self


class Sanction(BaseModel):
    type: str
    date_from: date
    date_to: date
    reason: str


class Guardian(BaseModel):
    full_name: str
    consent_at: datetime


class Antidoping(BaseModel):
    """Отметка о прохождении онлайн-курса РУСАДА."""
    course_date: date
    certificate_url: str


class SanctionIn(BaseModel):
    """Санкция по решению федерации (например, по рапорту о нарушении): исключает из рейтинга и подачи заявок на срок."""
    type: Literal["DISQUALIFICATION", "SUSPENSION"] = "DISQUALIFICATION"
    date_from: date
    date_to: date
    reason: str = Field(min_length=5, max_length=200)
    actor: str

    @model_validator(mode="after")
    def _period(self):
        if self.date_to < self.date_from:
            raise ValueError("Окончание санкции раньше её начала")
        return self


class AntidopingIn(BaseModel):
    course_date: date
    certificate_url: str = Field(min_length=8, max_length=300)

    @field_validator("certificate_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("https://", "http://")):
            raise ValueError("Ссылка на сертификат должна начинаться с https://")
        return v


class Notification(BaseModel):
    id: int
    fsp_id: str
    title: str
    text: str = ""
    link: str = ""
    key: str = ""  # для напоминаний: одно уведомление на ключ
    at: datetime
    read: bool = False


class NotificationsReadIn(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=500)


class NotificationIn(BaseModel):
    fsp_ids: list[str] = Field(min_length=1, max_length=1000)
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=600)
    link: str = Field(default="", max_length=300)
    key: str = Field(default="", max_length=200)


class PersonRecord(BaseModel):
    """Internal storage model. Never returned directly (holds password hash)."""
    fsp_id: str
    seq: int
    last_name: str
    first_name: str
    middle_name: str = ""
    gender: str
    birth_date: date
    municipality: str
    organization: str
    roles: list[str]
    disciplines: list[str]
    start_year: int
    training_stage: str | None = None  # код из TRAINING_STAGES
    antidoping: Antidoping | None = None
    email: str | None = None
    password_hash: str | None = None
    rank: RankRecord | None = None
    judge_category: JudgeCategoryRecord | None = None
    sanctions: list[Sanction] = []
    guardian: Guardian | None = None
    pd_consent_at: datetime
    created_at: datetime


class RegistrationIn(BaseModel):
    last_name: str = Field(min_length=2, max_length=60)
    first_name: str = Field(min_length=2, max_length=60)
    middle_name: str = Field(default="", max_length=60)
    gender: str = Field(pattern="^(М|Ж)$")
    birth_date: date
    municipality: str
    organization: str
    roles: list[str] = Field(min_length=1)
    disciplines: list[str] = []
    email: str
    password: str = Field(min_length=8, max_length=128)
    consent_pd: bool
    guardian_name: str = ""
    guardian_consent: bool = False

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Некорректный адрес электронной почты")
        return v

    @field_validator("roles")
    @classmethod
    def _roles(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(SELF_SERVICE_ROLES)
        if bad:
            raise ValueError(f"Недопустимые роли: {', '.join(sorted(bad))}")
        return list(dict.fromkeys(v))

    @field_validator("disciplines")
    @classmethod
    def _disciplines(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(DISCIPLINE_BY_CODE)
        if bad:
            raise ValueError(f"Неизвестные дисциплины: {', '.join(sorted(bad))}")
        return list(dict.fromkeys(v))

    @field_validator("municipality")
    @classmethod
    def _municipality(cls, v: str) -> str:
        if v not in MUNICIPALITIES:
            raise ValueError("Выберите муниципалитет из списка")
        return v

    @field_validator("organization")
    @classmethod
    def _organization(cls, v: str) -> str:
        if v not in ORGANIZATIONS:
            raise ValueError("Выберите организацию из списка")
        return v

    @model_validator(mode="after")
    def _business_rules(self):
        if not self.consent_pd:
            raise ValueError("Необходимо согласие на обработку персональных данных (152-ФЗ)")
        if ({"athlete", "trainer"} & set(self.roles)) and not self.disciplines:
            raise ValueError("Спортсмену и тренеру необходимо указать хотя бы одну дисциплину")
        return self


class AuthIn(BaseModel):
    email: str
    password: str


class LookupIn(BaseModel):
    fsp_ids: list[str] = Field(max_length=500)


class LinkCreateIn(BaseModel):
    trainer_fsp_id: str
    athlete_fsp_id: str
    discipline: str


class LinkStatusIn(BaseModel):
    status: LinkStatus
    actor_fsp_id: str
    note: str = Field(default="", max_length=300)


class LinkEvent(BaseModel):
    status: LinkStatus
    at: datetime
    by: str
    note: str = ""


class TrainerLink(BaseModel):
    id: int
    trainer_fsp_id: str
    athlete_fsp_id: str
    discipline: str
    status: LinkStatus
    history: list[LinkEvent]


class DisciplinesIn(BaseModel):
    """Самостоятельный выбор дисциплин спортсменом (в профиле и кабинете)."""
    disciplines: list[str] = Field(max_length=len(DISCIPLINE_BY_CODE))
    actor: str

    @field_validator("disciplines")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        bad = set(v) - set(DISCIPLINE_BY_CODE)
        if bad:
            raise ValueError(f"Неизвестные дисциплины: {', '.join(sorted(bad))}")
        return list(dict.fromkeys(v))

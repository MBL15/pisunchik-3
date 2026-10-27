from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_SKILLS = 12
MAX_PORTFOLIO = 8

Availability = Literal["OPEN", "BUSY"]
ProfileStatus = Literal["ACTIVE", "ARCHIVED"]
OfferStatus = Literal["PENDING", "ACCEPTED", "DECLINED", "WITHDRAWN"]


class PortfolioItem(BaseModel):
    title: str = Field(min_length=2, max_length=100)
    url: str = Field(max_length=300)
    description: str = Field(default="", max_length=300)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        value = value.strip()
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("Ссылка должна начинаться с http:// или https://")
        return value


class Profile(BaseModel):
    id: int
    owner_fsp_id: str
    owner_name: str
    headline: str
    about: str
    skills: list[str]
    portfolio: list[PortfolioItem]
    rate: str = ""
    contact: str = ""
    availability: Availability = "OPEN"
    status: ProfileStatus = "ACTIVE"
    created_at: datetime
    updated_at: datetime


class ProfileIn(BaseModel):
    actor: str
    owner_name: str = Field(default="", max_length=200)
    headline: str = Field(min_length=5, max_length=120)
    about: str = Field(min_length=20, max_length=2000)
    skills: list[str] = Field(min_length=1)
    portfolio: list[PortfolioItem] = Field(default=[], max_length=MAX_PORTFOLIO)
    rate: str = Field(default="", max_length=80)
    contact: str = Field(min_length=3, max_length=120)
    availability: Availability = "OPEN"

    @field_validator("skills")
    @classmethod
    def _skills(cls, value: list[str]) -> list[str]:
        out: dict[str, str] = {}
        for raw in value:
            skill = raw.strip()
            if len(skill) > 30:
                raise ValueError("Навык не длиннее 30 символов")
            if skill:
                out.setdefault(skill.lower(), skill)
        if not out:
            raise ValueError("Укажите хотя бы один навык")
        if len(out) > MAX_SKILLS:
            raise ValueError(f"Не более {MAX_SKILLS} навыков")
        return list(out.values())


class StatusIn(BaseModel):
    actor: str
    status: ProfileStatus


class Offer(BaseModel):
    id: int
    profile_id: int
    from_fsp_id: str
    from_name: str
    project_title: str
    description: str
    budget: str = ""
    contact: str = ""
    status: OfferStatus = "PENDING"
    note: str = ""
    created_at: datetime
    decided_at: datetime | None = None


class OfferIn(BaseModel):
    profile_id: int
    from_fsp_id: str
    from_name: str = Field(default="", max_length=200)
    project_title: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=20, max_length=2000)
    budget: str = Field(default="", max_length=80)
    contact: str = Field(default="", max_length=120)


class DecisionIn(BaseModel):
    actor: str
    accept: bool
    note: str = Field(default="", max_length=300)


class ActorIn(BaseModel):
    actor: str


class ApiToken(BaseModel):
    """Личный токен доступа к API и MCP: в хранилище — только SHA-256, сам токен показывается один раз."""
    id: int
    owner_fsp_id: str
    name: str
    prefix: str
    digest: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class TokenIn(BaseModel):
    actor: str
    name: str = Field(min_length=2, max_length=60)


class TokenVerifyIn(BaseModel):
    token: str = Field(max_length=200)

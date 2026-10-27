"""In-memory repository (mock database) for the sportsman service, seeded from the shared demo roster."""
from datetime import date, datetime, timedelta

from shared import config, demo_scale
from shared.demo_roster import DEMO_PASSWORD, ROSTER, ROSTER_BY_SEQ, TRAINER_LINKS
from shared.domain import (
    NO_ORGANIZATION, DISCIPLINE_BY_CODE, JUDGE_CATEGORY_BY_CODE, LINK_STATUS_LABELS, QUALIFICATION_KIND_LABELS,
    QUALIFICATION_PURPOSE_LABELS, QUALIFICATION_STATUS_LABELS, RANK_BY_CODE, RANK_ORDER, ROLE_LABELS, SANCTION_LABELS,
    TRAINING_STAGES, ApplicationStatus, LinkStatus, expiry_state,
)
from shared.ext import ExtTables
from shared.fsp_id import make_fsp_id
from shared.listing import AUDIT_LIMIT
from sportsman_service.models import (
    Antidoping, Guardian, JudgeCategoryRecord, LinkEvent, Notification, PersonRecord, QualificationRequest, RankRecord,
    Sanction, TrainerLink,
)
from sportsman_service.security import hash_password


def _years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:
        return d.replace(year=d.year - years, day=28)


def age_on(birth: date, on: date) -> int:
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


class Store:
    def __init__(self) -> None:
        self.persons: dict[str, PersonRecord] = {}
        self.links: list[TrainerLink] = []
        self.requests: list[QualificationRequest] = []
        self.notifications: list[Notification] = []
        self.notified_keys: set[tuple[str, str]] = set()  # (адресат, ключ) — напоминание уже было
        self.audit: list[dict] = []
        self.ext = ExtTables()  # организации, группы, журнал, нормативы, клиенты ФСП ID
        self._next_seq = 1
        self._next_link_id = 1
        self._next_notification_id = 1

    # ---------- identity ----------
    def issue_fsp_id(self, year: int) -> tuple[str, int]:
        seq = self._next_seq
        self._next_seq += 1
        return make_fsp_id(year, seq), seq

    def by_email(self, email: str) -> PersonRecord | None:
        return next((p for p in self.persons.values() if p.email == email.lower()), None)

    def log(self, actor: str, action: str, obj: str) -> None:
        self.audit.insert(0, {"at": datetime.now().isoformat(timespec="seconds"), "service": "sportsman",
                              "actor": actor, "action": action, "object": obj})
        del self.audit[AUDIT_LIMIT:]

    # ---------- notifications (лента в кабинете) ----------
    def notify(self, fsp_ids: list[str], title: str, text: str = "", link: str = "", key: str = "",
               at: datetime | None = None, read: bool = False) -> list[str]:
        """Добавляет уведомление каждому адресату; с `key` — не чаще одного раза на (адресат, ключ). Возвращает адресатов."""
        out = []
        for fid in dict.fromkeys(fsp_ids):
            if fid not in self.persons or (key and (fid, key) in self.notified_keys):
                continue
            if key:
                self.notified_keys.add((fid, key))
            self.notifications.append(Notification(id=self._next_notification_id, fsp_id=fid, title=title, text=text,
                                                   link=link, key=key, at=at or datetime.now(), read=read))
            self._next_notification_id += 1
            out.append(fid)
        return out

    # ---------- links ----------
    def add_link(self, trainer: str, athlete: str, discipline: str, status: LinkStatus, by: str, note: str) -> TrainerLink:
        now = datetime.now()
        history = [LinkEvent(status=LinkStatus.PENDING, at=now, by=by, note="Запрос на привязку")]
        if status != LinkStatus.PENDING:
            history.append(LinkEvent(status=status, at=now, by=athlete, note=note))
        link = TrainerLink(id=self._next_link_id, trainer_fsp_id=trainer, athlete_fsp_id=athlete,
                           discipline=discipline, status=status, history=history)
        self._next_link_id += 1
        self.links.append(link)
        return link

    def get_link(self, link_id: int) -> TrainerLink | None:
        return next((link for link in self.links if link.id == link_id), None)

    # ---------- presenters (public-safe views) ----------
    def present(self, p: PersonRecord, on: date | None = None) -> dict:
        on = on or config.today()
        rank = None
        if p.rank:
            meta = RANK_BY_CODE[p.rank.code]
            days_left = (p.rank.expires_at - on).days if p.rank.expires_at else None
            rank = {**p.rank.model_dump(mode="json"), "title": meta["title"], "short": meta["short"],
                    "days_left": days_left, "state": expiry_state(days_left)}
        judge = None
        if p.judge_category:
            days_left = (p.judge_category.expires_at - on).days
            judge = {**p.judge_category.model_dump(mode="json"),
                     "title": JUDGE_CATEGORY_BY_CODE[p.judge_category.code]["title"],
                     "days_left": days_left, "state": expiry_state(days_left)}
        active = [s for s in p.sanctions if s.date_from <= on <= s.date_to]
        return {
            "fsp_id": p.fsp_id,
            "full_name": f"{p.last_name} {p.first_name} {p.middle_name}".strip(),
            "short_name": f"{p.last_name} {p.first_name[0]}.{(' ' + p.middle_name[0] + '.') if p.middle_name else ''}",
            "last_name": p.last_name, "first_name": p.first_name, "middle_name": p.middle_name,
            "gender": p.gender, "birth_date": p.birth_date.isoformat(), "age": age_on(p.birth_date, on),
            "municipality": p.municipality, "organization": p.organization,
            "roles": p.roles, "role_labels": [ROLE_LABELS[r] for r in p.roles],
            "disciplines": p.disciplines,
            "discipline_labels": [DISCIPLINE_BY_CODE[d]["short"] for d in p.disciplines],
            "start_year": p.start_year, "email": p.email,
            "training_stage": p.training_stage,
            "training_stage_label": TRAINING_STAGES.get(p.training_stage, "") if p.training_stage else "",
            "antidoping": p.antidoping.model_dump(mode="json") if p.antidoping else None,
            "rank": rank, "judge_category": judge,
            "sanctions": [{**s.model_dump(mode="json"), "label": SANCTION_LABELS[s.type],
                           "active": s in active} for s in p.sanctions],
            "is_sanctioned": bool(active),
            "is_minor": age_on(p.birth_date, on) < 18,
            "guardian": p.guardian.model_dump(mode="json") if p.guardian else None,
            "pd_consent_at": p.pd_consent_at.isoformat(timespec="seconds"),
            "created_at": p.created_at.isoformat(timespec="seconds"),
        }

    def present_request(self, r: QualificationRequest) -> dict:
        person, by = self.persons[r.person_fsp_id], self.persons.get(r.submitted_by)
        title = (RANK_BY_CODE[r.code]["title"] if r.kind == "RANK" else JUDGE_CATEGORY_BY_CODE[r.code]["title"])
        current = person.rank if r.kind == "RANK" else person.judge_category
        return {
            **r.model_dump(mode="json"), "code_title": title,
            "kind_label": QUALIFICATION_KIND_LABELS[r.kind], "purpose_label": QUALIFICATION_PURPOSE_LABELS[r.purpose],
            "status_label": QUALIFICATION_STATUS_LABELS[r.status],
            "person": self.present(person), "current_code": current.code if current else None,
            "submitted_by_name": f"{by.last_name} {by.first_name[0]}." if by else r.submitted_by,
            "by_trainer": r.submitted_by != r.person_fsp_id,
        }

    def present_link(self, link: TrainerLink, on: date | None = None, with_people: bool = True) -> dict:
        data = link.model_dump(mode="json")
        data["status_label"] = LINK_STATUS_LABELS[link.status]
        data["discipline_label"] = DISCIPLINE_BY_CODE[link.discipline]["short"]
        if with_people:
            data["athlete"] = self.present(self.persons[link.athlete_fsp_id], on)
            trainer = self.persons[link.trainer_fsp_id]
            data["trainer_name"] = f"{trainer.last_name} {trainer.first_name} {trainer.middle_name}".strip()
        return data


store = Store()

DDL = """
CREATE TABLE IF NOT EXISTS sp_persons (key TEXT PRIMARY KEY, data JSONB NOT NULL, email TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS sp_persons_email_uq ON sp_persons (lower(email)) WHERE email IS NOT NULL;
CREATE TABLE IF NOT EXISTS sp_links (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS sp_requests (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS sp_notifications (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS sp_meta (key TEXT PRIMARY KEY, data JSONB NOT NULL);
"""


def snapshot(s: Store) -> dict:
    dump = lambda m: m.model_dump(mode="json")  # noqa: E731
    return {
        "sp_persons": {k: (dump(p), {"email": p.email}) for k, p in s.persons.items()},
        "sp_links": {str(x.id): (dump(x), {}) for x in s.links},
        "sp_requests": {str(x.id): (dump(x), {}) for x in s.requests},
        "sp_notifications": {str(x.id): (dump(x), {}) for x in s.notifications},
        "sp_meta": {"counters": ({"next_seq": s._next_seq, "next_link_id": s._next_link_id,
                                  "next_notification_id": s._next_notification_id}, {}),
                    "audit": ({"items": s.audit}, {}),
                    "ext": (s.ext.dump(), {})},
    }


def restore(s: Store, rows: dict[str, dict[str, dict]]) -> None:
    s.persons = {k: PersonRecord.model_validate(d) for k, d in rows["sp_persons"].items()}
    s.links = sorted((TrainerLink.model_validate(d) for d in rows["sp_links"].values()), key=lambda x: x.id)
    s.requests = sorted((QualificationRequest.model_validate(d) for d in rows["sp_requests"].values()), key=lambda x: x.id)
    s.notifications = sorted((Notification.model_validate(d) for d in rows.get("sp_notifications", {}).values()),
                             key=lambda x: x.id)
    s.notified_keys = {(n.fsp_id, n.key) for n in s.notifications if n.key}
    meta = rows["sp_meta"]
    s._next_seq = meta["counters"]["next_seq"]
    s._next_link_id = meta["counters"]["next_link_id"]
    s._next_notification_id = meta["counters"].get("next_notification_id", len(s.notifications) + 1)
    s.audit = meta.get("audit", {}).get("items", [])
    if "ext" in meta:
        s.ext.load(meta["ext"])


def seed(s: Store) -> None:
    today = config.today()
    now = datetime.now()
    demo_hash = hash_password(DEMO_PASSWORD)  # one KDF run for all demo accounts keeps boot instant
    for r in ROSTER:
        rank = None
        if r["rank_code"]:
            validity = RANK_BY_CODE[r["rank_code"]]["validity_years"]
            expires = today + timedelta(days=r["rank_expiry_offset"]) if validity else None
            assigned = _years_before(expires, validity) if expires else today - timedelta(days=900)
            rank = RankRecord(code=r["rank_code"], assigned_at=assigned, expires_at=expires,
                              order_no=f"Приказ МФКиС Оренбургской обл. № {100 + r['seq'] * 7}-р",
                              basis=f"Выполнение требований ЕВСК по итогам сезона {assigned.year} (перенесено из архива)")
        judge = None
        if r["judge_category"]:
            judge = JudgeCategoryRecord(code=r["judge_category"], expires_at=today + timedelta(days=r["judge_expiry_offset"]),
                                        order_no=f"Приказ МФКиС Оренбургской обл. № {300 + r['seq'] * 5}-р")
        sanctions = []
        if r["sanction"]:
            sc = r["sanction"]
            sanctions.append(Sanction(type=sc["type"], date_from=today + timedelta(days=sc["from_offset"]),
                                      date_to=today + timedelta(days=sc["to_offset"]), reason=sc["reason"]))
        birth = date.fromisoformat(r["birth_date"])
        stage = antidoping = None
        if "athlete" in r["roles"]:
            stage = _demo_stage(r["rank_code"], r["organization"])
            if r["seq"] % 3:
                antidoping = Antidoping(course_date=today - timedelta(days=40 + r["seq"] * 9),
                                        certificate_url=f"https://course.rusada.ru/certificate/demo-{r['seq']:05d}")
        guardian = None
        if age_on(birth, today) < 18:
            guardian = Guardian(full_name=f"{r['last_name']} (родитель)", consent_at=now)
        s.persons[r["fsp_id"]] = PersonRecord(
            fsp_id=r["fsp_id"], seq=r["seq"], last_name=r["last_name"], first_name=r["first_name"],
            middle_name=r["middle_name"], gender=r["gender"], birth_date=birth, municipality=r["municipality"],
            organization=r["organization"], roles=r["roles"], disciplines=r["disciplines"],
            start_year=max(birth.year + 10, 2000 + int(r["fsp_id"].split("-")[2]) - r["seq"] % 3), email=r["email"],
            training_stage=stage, antidoping=antidoping,
            password_hash=demo_hash if r["email"] else None, rank=rank, judge_category=judge,
            sanctions=sanctions, guardian=guardian, pd_consent_at=now, created_at=now,
        )
    s._next_seq = max(r["seq"] for r in ROSTER) + 1
    _seed_scale(s, today, now)
    for trainer_seq, athlete_seq, disc, status in TRAINER_LINKS:
        trainer, athlete = ROSTER_BY_SEQ[trainer_seq]["fsp_id"], ROSTER_BY_SEQ[athlete_seq]["fsp_id"]
        note = "Связь приостановлена: пропуск сборов" if status == "SUSPENDED" else "Подтверждено спортсменом"
        s.add_link(trainer, athlete, disc, LinkStatus(status), by=trainer, note=note)
    timofeev = ROSTER_BY_SEQ[5]["fsp_id"]
    s.requests.append(QualificationRequest(
        id=1, kind="JUDGE_CATEGORY", person_fsp_id=timofeev, code="2К", purpose="ASSIGN", submitted_by=timofeev,
        basis={"text": "Судья на первенстве и кубке области по продуктовому программированию, технический судья ИИ",
               "refereed": 3},
        status=ApplicationStatus.PENDING, created_at=now - timedelta(days=2),
    ))
    _seed_notifications(s, now)
    from sportsman_service import training_seed  # отложенный импорт: модуль зависит только от shared

    training_seed.seed(s)
    from sportsman_service import oidc

    oidc.seed_demo_client(s)
    s.log("system", "seed", f"{len(s.persons)} персон, {len(s.links)} связей тренер–спортсмен")


def _seed_scale(s: Store, today: date, now: datetime) -> None:
    """FSP_DEMO_SCALE: синтетические люди для проверки больших списков (без входа, вне протоколов)."""
    for r in demo_scale.generate():
        rank = None
        if r["rank_code"]:
            expires = today + timedelta(days=r["rank_expiry_offset"])
            rank = RankRecord(code=r["rank_code"], assigned_at=_years_before(expires, 2), expires_at=expires,
                              order_no=f"Приказ МФКиС Оренбургской обл. № {r['seq']}-р", basis="Синтетические данные")
        judge = None
        if r["judge_category"]:
            judge = JudgeCategoryRecord(code=r["judge_category"], expires_at=today + timedelta(days=r["judge_expiry_offset"]))
        birth = date.fromisoformat(r["birth_date"])
        athlete = "athlete" in r["roles"]
        s.persons[r["fsp_id"]] = PersonRecord(
            fsp_id=r["fsp_id"], seq=r["seq"], last_name=r["last_name"], first_name=r["first_name"],
            middle_name=r["middle_name"], gender=r["gender"], birth_date=birth, municipality=r["municipality"],
            organization=r["organization"], roles=r["roles"], disciplines=r["disciplines"], start_year=r["start_year"],
            training_stage=_demo_stage(r["rank_code"], r["organization"]) if athlete else None,
            antidoping=Antidoping(course_date=today - timedelta(days=30 + r["seq"] % 300),
                                  certificate_url=f"https://course.rusada.ru/certificate/demo-{r['seq']:05d}")
            if athlete and r["seq"] % 3 else None,
            rank=rank, judge_category=judge,
            guardian=Guardian(full_name=f"{r['last_name']} (родитель)", consent_at=now) if age_on(birth, today) < 18 else None,
            pd_consent_at=now, created_at=now + timedelta(days=r["created_offset"]),
        )


def _demo_stage(rank_code: str | None, organization: str) -> str | None:
    """Этап подготовки демо-спортсмена по разряду; без организации спортивной подготовки этапа нет."""
    if organization == NO_ORGANIZATION:
        return None
    order = RANK_ORDER[rank_code] if rank_code else -1
    return "ССМ" if order >= 5 else "УТ" if order >= 2 else "НП"


def _seed_notifications(s: Store, now: datetime) -> None:
    """Несколько прочитанных событий в ленте демо-аккаунтов, чтобы лента не была пустой при первом входе."""
    admin, trainer, athlete = ROSTER_BY_SEQ[1]["fsp_id"], ROSTER_BY_SEQ[2]["fsp_id"], ROSTER_BY_SEQ[3]["fsp_id"]
    timofeev = ROSTER_BY_SEQ[5]["fsp_id"]
    s.notify([admin], "Заявка на судейскую категорию", "Тимофеев И. — 2К, на рассмотрении", "/cabinet#inbox",
             at=now - timedelta(days=2))
    s.notify([timofeev], "Заявка на категорию отправлена", "2К — на рассмотрении в федерации", "/cabinet#category",
             at=now - timedelta(days=2), read=True)
    s.notify([trainer, athlete], "Добро пожаловать на платформу ФСП",
             "Здесь появляются уведомления о заявках, протоколах, назначениях и сроках.", "/cabinet",
             at=now - timedelta(days=7), read=True)

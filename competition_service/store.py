"""In-memory repository (mock database) for the competition service."""
from datetime import date, datetime

from competition_service.log_parser import parse_log, rank_manual
from competition_service.models import Application, Competition, Protocol, Team
from competition_service.rating import RatingSettings, base_points
from shared import config
from shared.listing import AUDIT_LIMIT
from shared.domain import (
    APPLICATION_STATUS_LABELS, CALENDAR_PLAN_LABELS, COMPETITION_STATUS_LABELS, DEFAULT_COMPETITION_TYPES,
    DISCIPLINE_BY_CODE, FORMAT_LABELS, LEVEL_LABELS, LEVEL_STATUS_LABELS, REGION_CODE, TEAM_ROLE_LABELS,
    TEAM_STATUS_LABELS, ApplicationStatus, CompetitionStatus,
)


class Store:
    def __init__(self) -> None:
        self.competitions: dict[int, Competition] = {}
        self.protocols: dict[int, Protocol] = {}
        self.live_queue: dict[int, list[str]] = {}
        self.last_tick: dict[int, float] = {}
        self.live_replay: dict[int, tuple[list[str], list[str]]] = {}  # demo loop: (initial log, full queue)
        self.applications: list[Application] = []
        self.documents: dict[str, dict] = {}
        self.settings = RatingSettings()
        self.competition_types: list[str] = list(DEFAULT_COMPETITION_TYPES)
        self.teams: dict[int, Team] = {}
        self.audit: list[dict] = []
        self.doc_seq = 0

    def log(self, actor: str, action: str, obj: str) -> None:
        self.audit.insert(0, {"at": datetime.now().isoformat(timespec="seconds"), "service": "competition",
                              "actor": actor, "action": action, "object": obj})
        del self.audit[AUDIT_LIMIT:]

    def approved(self, cid: int) -> dict[str, Application]:
        return {a.athlete_fsp_id: a for a in self.applications
                if a.competition_id == cid and a.status == ApplicationStatus.APPROVED}

    def reparse(self, proto: Protocol) -> None:
        comp = self.competitions[proto.competition_id]
        if proto.source == "manual":
            entries, warnings = rank_manual(proto.results, proto.mode), []
            proto.events = len(proto.results)
        else:
            parsed = parse_log("\n".join(proto.raw_log))
            entries, warnings = parsed.entries, parsed.warnings
            proto.mode, proto.events, proto.last_time = parsed.mode, parsed.events, parsed.last_time
        for e in entries:
            meta = proto.names.get(e["fsp_id"], {})
            e["full_name"] = meta.get("full_name", "Участник не найден в реестре")
            e["region"] = meta.get("region", "—")
            e["base_points"] = base_points(comp.level, e["place"], e["status"], self.settings)
        if any(a.competition_id == comp.id for a in self.applications):
            approved, listed = self.approved(comp.id), {e["fsp_id"] for e in entries}
            warnings += [f"{e['full_name']} ({e['fsp_id']}): нет одобренной заявки на соревнование"
                         for e in entries if e["fsp_id"] not in approved and e["fsp_id"].split("-")[1] == REGION_CODE]
            warnings += [f"{a.athlete_name}: заявка одобрена, но результата нет — не вошёл в протокол"
                         for fid, a in approved.items() if fid not in listed]
        proto.entries, proto.warnings, proto.updated_at = entries, warnings, datetime.now()

    def present_competition(self, c: Competition, on: date | None = None) -> dict:
        on = on or config.today()
        apps = [a for a in self.applications if a.competition_id == c.id]
        proto = self.protocols.get(c.id)
        days_to_deadline = (c.registration_deadline - on).days if c.registration_deadline else None
        approved = sum(a.status == ApplicationStatus.APPROVED for a in apps)
        return {
            **c.model_dump(mode="json"),
            "discipline_label": DISCIPLINE_BY_CODE[c.discipline]["short"],
            "discipline_title": DISCIPLINE_BY_CODE[c.discipline]["title"],
            "level_label": LEVEL_LABELS[c.level], "level_status": LEVEL_STATUS_LABELS[c.level],
            "format_label": FORMAT_LABELS[c.format], "plan_label": CALENDAR_PLAN_LABELS[c.calendar_plan],
            "status_label": COMPETITION_STATUS_LABELS[c.status],
            "registration_expired": c.status == CompetitionStatus.REGISTRATION_OPEN
                                    and c.registration_deadline is not None and c.registration_deadline < on,
            "days_to_start": (c.start_date - on).days, "days_to_deadline": days_to_deadline,
            "applications_total": len(apps),
            "applications_approved": approved,
            "applications_pending": sum(a.status == ApplicationStatus.PENDING for a in apps),
            "has_protocol": proto is not None, "protocol_published": bool(proto and proto.published),
            "protocol_source": proto.source if proto else None,
            "awaiting_results": approved > 0 and c.start_date <= on and not (proto and proto.published)
                                and self.live_queue.get(c.id) is None and c.id not in self.live_replay,
            "participants": len(proto.entries) if proto else 0,
            "season": c.end_date.year,
        }

    @staticmethod
    def present_team(t: Team) -> dict:
        main = [m for m in t.members if m.role == "MAIN"]
        coaches = sorted({c for m in t.members for c in m.coaches})
        return {
            **t.model_dump(mode="json"),
            "discipline_label": DISCIPLINE_BY_CODE[t.discipline]["short"],
            "discipline_title": DISCIPLINE_BY_CODE[t.discipline]["title"],
            "status_label": TEAM_STATUS_LABELS[t.status], "locked": t.status == "APPROVED",
            "main_count": len(main), "reserve_count": len(t.members) - len(main), "coaches": coaches,
            "members": [{**m.model_dump(mode="json"), "role_label": TEAM_ROLE_LABELS[m.role]} for m in t.members],
        }

    def present_application(self, a: Application) -> dict:
        c = self.competitions[a.competition_id]
        return {**a.model_dump(mode="json"), "status_label": APPLICATION_STATUS_LABELS[a.status],
                "competition_title": c.title, "competition_code": c.code, "discipline": c.discipline,
                "start_date": c.start_date.isoformat(),
                "registration_deadline": c.registration_deadline.isoformat() if c.registration_deadline else None}

    def published_results(self, season: int | None = None, discipline: str | None = None) -> list[dict]:
        rows = []
        for cid, proto in self.protocols.items():
            c = self.competitions[cid]
            if not proto.published or (season and c.end_date.year != season) or (discipline and c.discipline != discipline):
                continue
            for e in proto.entries:
                rows.append({
                    "fsp_id": e["fsp_id"], "full_name": e["full_name"], "region": e["region"],
                    "competition_id": cid, "competition_code": c.code, "title": c.title,
                    "discipline": c.discipline, "discipline_label": DISCIPLINE_BY_CODE[c.discipline]["short"],
                    "level": c.level, "level_label": LEVEL_LABELS[c.level], "date": c.end_date.isoformat(),
                    "place": e["place"], "status": e["status"], "dsq_reason": e["dsq_reason"],
                    "solved": e["solved"], "penalty": e["penalty"], "score": e["score"], "mode": proto.mode,
                    "base_points": base_points(c.level, e["place"], e["status"], self.settings),
                    "protocol_size": len(proto.entries),
                })
        return rows


store = Store()

DDL = """
CREATE TABLE IF NOT EXISTS comp_competitions (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS comp_protocols (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS comp_applications (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS comp_teams (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS comp_documents (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS comp_meta (key TEXT PRIMARY KEY, data JSONB NOT NULL);
"""


def snapshot(s: Store) -> dict:
    dump = lambda m: m.model_dump(mode="json")  # noqa: E731
    return {
        "comp_competitions": {str(k): (dump(v), {}) for k, v in s.competitions.items()},
        "comp_protocols": {str(k): (dump(v), {}) for k, v in s.protocols.items()},
        "comp_applications": {str(a.id): (dump(a), {}) for a in s.applications},
        "comp_teams": {str(k): (dump(v), {}) for k, v in s.teams.items()},
        "comp_documents": {k: (v, {}) for k, v in s.documents.items()},
        "comp_meta": {
            "settings": ({"points": s.settings.points, "coefficient": s.settings.coefficient,
                          "min_podiums": s.settings.min_podiums}, {}),
            "types": ({"items": s.competition_types}, {}),
            "counters": ({"doc_seq": s.doc_seq}, {}),
            "audit": ({"items": s.audit}, {}),
        },
    }


def restore(s: Store, rows: dict[str, dict[str, dict]]) -> None:
    """Накладывает сохранённое состояние на засеянное; живые (воспроизводимые) протоколы остаются из seed."""
    s.competitions = {int(k): Competition.model_validate(d) for k, d in rows["comp_competitions"].items()}
    live = set(s.live_queue) | set(s.live_replay)
    s.protocols = {**{int(k): Protocol.model_validate(d) for k, d in rows["comp_protocols"].items() if int(k) not in live},
                   **{k: v for k, v in s.protocols.items() if k in live}}
    s.applications = sorted((Application.model_validate(d) for d in rows["comp_applications"].values()), key=lambda a: a.id)
    s.teams = {int(k): Team.model_validate(d) for k, d in rows["comp_teams"].items()}
    s.documents = dict(rows["comp_documents"])
    meta = rows["comp_meta"]
    s.settings = RatingSettings(**meta["settings"])
    s.competition_types = meta["types"]["items"]
    s.doc_seq = meta["counters"]["doc_seq"]
    s.audit = meta["audit"]["items"]

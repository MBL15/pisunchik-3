"""In-memory repository (mock database) for the market service."""
from datetime import datetime

from market_service.models import ApiToken, Offer, Profile
from shared.listing import AUDIT_LIMIT

AVAILABILITY_LABELS = {"OPEN": "Открыт к проектам", "BUSY": "Занят, но рассмотрю предложения"}
OFFER_STATUS_LABELS = {"PENDING": "На рассмотрении", "ACCEPTED": "Принят", "DECLINED": "Отклонён", "WITHDRAWN": "Отозван"}


class Store:
    def __init__(self) -> None:
        self.profiles: dict[int, Profile] = {}
        self.offers: dict[int, Offer] = {}
        self.tokens: dict[int, ApiToken] = {}
        self.profile_seq = 0
        self.offer_seq = 0
        self.token_seq = 0
        self.audit: list[dict] = []

    def log(self, actor: str, action: str, obj: str) -> None:
        self.audit.insert(0, {"at": datetime.now().isoformat(timespec="seconds"), "service": "market",
                              "actor": actor, "action": action, "object": obj})
        del self.audit[AUDIT_LIMIT:]

    def active_profile_of(self, fsp_id: str) -> Profile | None:
        return next((p for p in self.profiles.values() if p.owner_fsp_id == fsp_id and p.status == "ACTIVE"), None)

    def offers_of(self, profile_id: int) -> list[Offer]:
        return [o for o in self.offers.values() if o.profile_id == profile_id]

    def present_profile(self, p: Profile, viewer: str | None = None) -> dict:
        """Контакт владельца виден только ему; остальные получают его в принятом отклике."""
        offers = self.offers_of(p.id)
        data = p.model_dump(mode="json")
        if viewer != p.owner_fsp_id:
            data["contact"] = ""
        return {**data, "availability_label": AVAILABILITY_LABELS[p.availability],
                "offers_total": len(offers), "offers_pending": sum(o.status == "PENDING" for o in offers),
                "offers_accepted": sum(o.status == "ACCEPTED" for o in offers)}

    def present_offer(self, o: Offer) -> dict:
        p = self.profiles[o.profile_id]
        return {**o.model_dump(mode="json"), "status_label": OFFER_STATUS_LABELS[o.status],
                "profile_headline": p.headline, "owner_fsp_id": p.owner_fsp_id, "owner_name": p.owner_name,
                "owner_contact": p.contact if o.status == "ACCEPTED" else ""}


    @staticmethod
    def present_token(t: ApiToken) -> dict:
        data = t.model_dump(mode="json", exclude={"digest"})
        return {**data, "active": t.revoked_at is None}


store = Store()

DDL = """
CREATE TABLE IF NOT EXISTS market_profiles (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS market_offers (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS market_tokens (key TEXT PRIMARY KEY, data JSONB NOT NULL);
CREATE TABLE IF NOT EXISTS market_meta (key TEXT PRIMARY KEY, data JSONB NOT NULL);
"""


def snapshot(s: Store) -> dict:
    return {
        "market_profiles": {str(k): (v.model_dump(mode="json"), {}) for k, v in s.profiles.items()},
        "market_offers": {str(k): (v.model_dump(mode="json"), {}) for k, v in s.offers.items()},
        "market_tokens": {str(k): (v.model_dump(mode="json"), {}) for k, v in s.tokens.items()},
        "market_meta": {
            "counters": ({"profile_seq": s.profile_seq, "offer_seq": s.offer_seq, "token_seq": s.token_seq}, {}),
            "audit": ({"items": s.audit}, {}),
        },
    }


def restore(s: Store, rows: dict[str, dict[str, dict]]) -> None:
    s.profiles = {int(k): Profile.model_validate(d) for k, d in rows["market_profiles"].items()}
    s.offers = {int(k): Offer.model_validate(d) for k, d in rows["market_offers"].items()}
    s.tokens = {int(k): ApiToken.model_validate(d) for k, d in rows.get("market_tokens", {}).items()}
    meta = rows["market_meta"]
    s.profile_seq, s.offer_seq = meta["counters"]["profile_seq"], meta["counters"]["offer_seq"]
    s.token_seq = meta["counters"].get("token_seq", max(s.tokens, default=0))
    s.audit = meta["audit"]["items"]

"""Биржа программистов: анкеты, фильтры, отклики и их жизненный цикл."""
import unittest

from fastapi.testclient import TestClient

from market_service import main as market
from market_service.seed import seed
from shared.demo_roster import ROSTER_BY_SEQ

SOKOLOV = ROSTER_BY_SEQ[3]["fsp_id"]  # в сиде: анкета и два входящих отклика
KUZNETSOV = ROSTER_BY_SEQ[2]["fsp_id"]  # в сиде: отклик Соколову на рассмотрении
ZAYTSEVA = ROSTER_BY_SEQ[13]["fsp_id"]
ABRAMOV = ROSTER_BY_SEQ[8]["fsp_id"]  # без анкеты


def profile_body(actor, **extra):
    return {"actor": actor, "owner_name": "Абрамов Кирилл Сергеевич", "headline": "Разработка Telegram-ботов",
            "about": "Пишу ботов на Python: приём заказов, оплата, уведомления. Есть опыт сопровождения.",
            "skills": ["Python", "aiogram", "python"], "contact": "@abramov", "availability": "OPEN",
            "portfolio": [{"title": "Бот школьной столовой", "url": "https://example.org/bot", "description": "Заказ обедов"}],
            **extra}


def offer_body(profile_id, sender, **extra):
    return {"profile_id": profile_id, "from_fsp_id": sender, "from_name": "Тест", "project_title": "Сайт турнира",
            "description": "Нужен сайт с регистрацией команд и таблицей результатов.", **extra}


class _MarketCase(unittest.TestCase):
    def setUp(self):
        market.store.__init__()
        seed(market.store)
        self.api = TestClient(market.app)
        self.sokolov = next(p for p in self.api.get("/api/v1/profiles").json()["items"] if p["owner_fsp_id"] == SOKOLOV)


class MarketTest(_MarketCase):
    def test_feed_lists_active_profiles_without_contacts(self):
        feed = self.api.get("/api/v1/profiles").json()
        self.assertEqual(feed["total"], 5)
        self.assertTrue(all(p["contact"] == "" for p in feed["items"]))
        own = self.api.get("/api/v1/profiles", params={"owner": SOKOLOV, "viewer": SOKOLOV}).json()["items"][0]
        self.assertEqual(own["contact"], "@sokolov_dev")

    def test_search_and_skill_filter_with_facets(self):
        found = self.api.get("/api/v1/profiles", params={"q": "поисков индекс"}).json()
        self.assertEqual([p["owner_fsp_id"] for p in found["items"]], [SOKOLOV])
        by_skill = self.api.get("/api/v1/profiles", params={"skill": "python"}).json()
        self.assertGreaterEqual(by_skill["total"], 3)
        self.assertIn("Python", by_skill["facets"]["skills"])
        busy = self.api.get("/api/v1/profiles", params={"availability": "BUSY"}).json()
        self.assertEqual(busy["total"], 1)

    def test_create_normalizes_skills_and_allows_one_active_profile(self):
        r = self.api.post("/api/v1/profiles", json=profile_body(ABRAMOV))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["skills"], ["Python", "aiogram"])
        self.assertEqual(self.api.post("/api/v1/profiles", json=profile_body(ABRAMOV)).status_code, 409)

    def test_portfolio_links_must_be_http(self):
        bad = profile_body(ABRAMOV, portfolio=[{"title": "Скрипт", "url": "javascript:alert(1)"}])
        self.assertEqual(self.api.post("/api/v1/profiles", json=bad).status_code, 422)

    def test_only_owner_edits_and_archives(self):
        r = self.api.put(f"/api/v1/profiles/{self.sokolov['id']}", json=profile_body(ABRAMOV))
        self.assertEqual(r.status_code, 403)
        r = self.api.post(f"/api/v1/profiles/{self.sokolov['id']}/status", json={"actor": SOKOLOV, "status": "ARCHIVED"})
        self.assertEqual(r.json()["status"], "ARCHIVED")
        self.assertEqual(self.api.get("/api/v1/profiles").json()["total"], 4)

    def test_offer_rules(self):
        pid = self.sokolov["id"]
        self.assertEqual(self.api.post("/api/v1/offers", json=offer_body(pid, SOKOLOV)).status_code, 422)
        self.assertEqual(self.api.post("/api/v1/offers", json=offer_body(pid, KUZNETSOV)).status_code, 409)  # уже на рассмотрении
        ok = self.api.post("/api/v1/offers", json=offer_body(pid, ABRAMOV))
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["status"], "PENDING")

    def test_accept_reveals_owner_contact_to_sender_only_after_acceptance(self):
        pid = self.sokolov["id"]
        offer = self.api.post("/api/v1/offers", json=offer_body(pid, ABRAMOV)).json()
        self.assertEqual(offer["owner_contact"], "")
        self.assertEqual(self.api.post(f"/api/v1/offers/{offer['id']}/decision",
                                       json={"actor": ABRAMOV, "accept": True}).status_code, 403)
        done = self.api.post(f"/api/v1/offers/{offer['id']}/decision", json={"actor": SOKOLOV, "accept": True, "note": "Беру"})
        self.assertEqual(done.json()["owner_contact"], "@sokolov_dev")
        again = self.api.post(f"/api/v1/offers/{offer['id']}/decision", json={"actor": SOKOLOV, "accept": False})
        self.assertEqual(again.status_code, 409)

    def test_withdraw_by_author_frees_the_slot_for_a_new_offer(self):
        pid = self.sokolov["id"]
        mine = self.api.get("/api/v1/offers", params={"from_fsp_id": KUZNETSOV, "status": "PENDING"}).json()[0]
        self.assertEqual(self.api.post(f"/api/v1/offers/{mine['id']}/withdraw", json={"actor": SOKOLOV}).status_code, 403)
        self.assertEqual(self.api.post(f"/api/v1/offers/{mine['id']}/withdraw", json={"actor": KUZNETSOV}).json()["status"], "WITHDRAWN")
        self.assertEqual(self.api.post("/api/v1/offers", json=offer_body(pid, KUZNETSOV)).status_code, 201)

    def test_archived_profile_takes_no_offers(self):
        pid = self.sokolov["id"]
        self.api.post(f"/api/v1/profiles/{pid}/status", json={"actor": SOKOLOV, "status": "ARCHIVED"})
        self.assertEqual(self.api.post("/api/v1/offers", json=offer_body(pid, ZAYTSEVA)).status_code, 409)

    def test_incoming_and_outgoing_lists(self):
        incoming = self.api.get("/api/v1/offers", params={"owner_fsp_id": SOKOLOV}).json()
        self.assertEqual(len(incoming), 2)
        outgoing = self.api.get("/api/v1/offers", params={"from_fsp_id": SOKOLOV}).json()
        self.assertEqual([o["status"] for o in outgoing], ["ACCEPTED"])
        self.assertTrue(outgoing[0]["owner_contact"])


class TokenTest(_MarketCase):
    """Токены доступа к API и MCP."""

    def issue(self, actor=SOKOLOV, name="Claude Desktop"):
        return self.api.post("/api/v1/tokens", json={"actor": actor, "name": name})

    def test_token_is_shown_once_and_stored_hashed(self):
        issued = self.issue().json()
        self.assertTrue(issued["token"].startswith("fspm_"))
        self.assertNotIn("token", self.api.get("/api/v1/tokens", params={"owner": SOKOLOV}).json()[0])
        stored = next(iter(market.store.tokens.values()))
        self.assertNotIn(issued["token"], stored.model_dump_json())
        self.assertNotIn("digest", issued)

    def test_verify_resolves_owner_and_rejects_unknown_and_revoked(self):
        issued = self.issue().json()
        ok = self.api.post("/api/v1/tokens/verify", json={"token": issued["token"]})
        self.assertEqual(ok.json()["fsp_id"], SOKOLOV)
        self.assertIsNotNone(self.api.get("/api/v1/tokens", params={"owner": SOKOLOV}).json()[0]["last_used_at"])
        self.assertEqual(self.api.post("/api/v1/tokens/verify", json={"token": "fspm_wrong"}).status_code, 401)
        self.api.post(f"/api/v1/tokens/{issued['id']}/revoke", json={"actor": SOKOLOV})
        self.assertEqual(self.api.post("/api/v1/tokens/verify", json={"token": issued["token"]}).status_code, 401)

    def test_only_owner_revokes_and_active_tokens_are_limited(self):
        issued = self.issue().json()
        self.assertEqual(self.api.post(f"/api/v1/tokens/{issued['id']}/revoke", json={"actor": ABRAMOV}).status_code, 403)
        for _ in range(4):
            self.assertEqual(self.issue().status_code, 201)
        self.assertEqual(self.issue().status_code, 409)
        self.api.post(f"/api/v1/tokens/{issued['id']}/revoke", json={"actor": SOKOLOV})
        self.assertEqual(self.issue().status_code, 201)

    def test_tokens_survive_snapshot_restore(self):
        issued = self.issue().json()
        rows = {t: {k: d for k, (d, _) in r.items()} for t, r in market.snapshot(market.store).items()}
        market.store.__init__()
        market.restore(market.store, rows)
        self.assertEqual(self.api.post("/api/v1/tokens/verify", json={"token": issued["token"]}).status_code, 200)
        self.assertEqual(market.store.token_seq, issued["id"])


if __name__ == "__main__":
    unittest.main()

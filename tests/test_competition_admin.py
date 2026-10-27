"""Federation tools of competition_service: competition types directory, manual registration open/close."""
import unittest
from datetime import timedelta

from fastapi.testclient import TestClient

from competition_service import main as competition
from shared import config

ADMIN = "FSP-56-25-00001-6"
ALG6_OPEN, ALG5_LIVE, AI3_PLANNED, SEC4_CLOSED = 6, 5, 15, 22


class _Base(unittest.TestCase):
    def setUp(self):
        competition.store.__init__()  # lifespan re-seeds the module-level store
        self.ctx = TestClient(competition.app)
        self.api = self.ctx.__enter__()
        self.today = config.today()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)


class CompetitionTypesTest(_Base):
    def payload(self, ctype):
        start = self.today + timedelta(days=40)
        return {"title": "Открытый фестиваль программирования", "discipline": "ALG", "level": "REGIONAL", "type": ctype,
                "format": "OFFLINE", "calendar_plan": "REGIONAL", "plan_number": "№ 56-200", "start_date": start.isoformat(),
                "end_date": start.isoformat(), "venue": "г. Оренбург", "age_group": "без ограничений 12+",
                "registration_deadline": (start - timedelta(days=5)).isoformat(), "actor": ADMIN}

    def test_directory_starts_with_defaults_and_grows(self):
        titles = [t["title"] for t in self.api.get("/api/v1/competition-types").json()]
        self.assertEqual(titles, ["Чемпионат", "Первенство", "Кубок", "Турнир"])
        r = self.api.post("/api/v1/competition-types", json={"title": "  Открытый   фестиваль ", "actor": ADMIN})
        self.assertEqual((r.status_code, r.json()[-1]["title"]), (201, "Открытый фестиваль"))
        self.assertEqual(self.api.post("/api/v1/competition-types", json={"title": "ОТКРЫТЫЙ ФЕСТИВАЛЬ", "actor": ADMIN}).status_code, 409)
        self.assertEqual(self.api.post("/api/v1/competition-types", json={"title": "123", "actor": ADMIN}).status_code, 422)

    def test_new_type_is_accepted_by_competition_creation_and_unknown_is_not(self):
        self.assertEqual(self.api.post("/api/v1/competitions", json=self.payload("Открытый фестиваль")).status_code, 422)
        self.api.post("/api/v1/competition-types", json={"title": "Открытый фестиваль", "actor": ADMIN})
        r = self.api.post("/api/v1/competitions", json=self.payload("Открытый фестиваль"))
        self.assertEqual((r.status_code, r.json()["type"]), (201, "Открытый фестиваль"))

    def test_only_unused_types_can_be_removed(self):
        self.api.post("/api/v1/competition-types", json={"title": "Спартакиада", "actor": ADMIN})
        r = self.api.delete("/api/v1/competition-types", params={"title": "Спартакиада", "actor": ADMIN})
        self.assertNotIn("Спартакиада", [t["title"] for t in r.json()])
        used = self.api.delete("/api/v1/competition-types", params={"title": "Турнир", "actor": ADMIN})
        self.assertEqual(used.status_code, 409)
        self.assertEqual(self.api.delete("/api/v1/competition-types", params={"title": "Нет такого", "actor": ADMIN}).status_code, 404)


class RegistrationToggleTest(_Base):
    def toggle(self, cid, action, deadline=None):
        return self.api.post(f"/api/v1/competitions/{cid}/registration",
                             json={"action": action, "deadline": deadline.isoformat() if deadline else None, "actor": ADMIN})

    def apply(self, cid):
        return self.api.post("/api/v1/applications", json={"competition_id": cid, "athlete_fsp_id": ADMIN, "submitted_by": ADMIN})

    def test_close_blocks_applications_and_reopen_restores_the_window(self):
        r = self.toggle(ALG6_OPEN, "close")
        self.assertEqual(r.json()["status"], "REGISTRATION_CLOSED")
        self.assertIn("закрыт", self.apply(ALG6_OPEN).json()["detail"])
        self.assertEqual(self.toggle(ALG6_OPEN, "close").status_code, 409)  # уже закрыт
        r = self.toggle(ALG6_OPEN, "open")
        self.assertEqual(r.json()["status"], "REGISTRATION_OPEN")
        self.assertNotIn("закрыт", str(self.apply(ALG6_OPEN).json().get("detail", "")))

    def test_planned_competition_needs_a_deadline_before_the_start(self):
        self.assertEqual(self.toggle(AI3_PLANNED, "open").status_code, 422)  # срока нет
        start = self.api.get(f"/api/v1/competitions/{AI3_PLANNED}").json()["start_date"]
        late = self.today + timedelta(days=self.api.get(f"/api/v1/competitions/{AI3_PLANNED}").json()["days_to_start"] + 1)
        self.assertEqual(self.toggle(AI3_PLANNED, "open", late).status_code, 422)  # после старта
        self.assertEqual(self.toggle(AI3_PLANNED, "open", self.today - timedelta(days=1)).status_code, 422)
        ok = self.toggle(AI3_PLANNED, "open", self.today + timedelta(days=10))
        self.assertEqual((ok.status_code, ok.json()["status"], ok.json()["start_date"]), (200, "REGISTRATION_OPEN", start))

    def test_open_registration_can_only_be_extended_with_a_new_date(self):
        self.assertEqual(self.toggle(ALG6_OPEN, "open").status_code, 409)
        new = self.today + timedelta(days=20)
        r = self.toggle(ALG6_OPEN, "open", new)
        self.assertEqual(r.json()["registration_deadline"], new.isoformat())
        self.assertFalse(r.json()["registration_expired"])

    def test_started_or_finished_competitions_are_untouchable(self):
        self.assertEqual(self.toggle(ALG5_LIVE, "close").status_code, 422)
        self.assertEqual(self.toggle(1, "open", self.today).status_code, 422)  # FINISHED
        self.assertEqual(self.toggle(SEC4_CLOSED, "open", self.today + timedelta(days=1)).status_code, 422)  # старт прошёл


if __name__ == "__main__":
    unittest.main()

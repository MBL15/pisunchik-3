"""Rank / judge category requests: who may file, what is checked, what the decision writes to the profile."""
import unittest
from datetime import date

from fastapi.testclient import TestClient

from shared import config
from shared.demo_roster import ROSTER_BY_SEQ
from sportsman_service import main as sportsman

ADMIN, KUZNETSOV, GALIEV, TIMOFEEV = (ROSTER_BY_SEQ[s]["fsp_id"] for s in (1, 2, 7, 5))
ABRAMOV, PETROVA = ROSTER_BY_SEQ[8]["fsp_id"], ROSTER_BY_SEQ[20]["fsp_id"]


class _Competition:
    """Stub for competition_service: one published result per athlete in competition 14."""

    async def get(self, path: str, **params):
        if path == "/api/v1/results":
            return [{"fsp_id": params["fsp_ids"], "competition_id": 14, "competition_code": "ОРБ-AI-2026-14",
                     "title": "ML-турнир", "date": "2026-09-08", "discipline_label": "ИИ", "level_label": "Межрегиональные",
                     "place": 2, "status": "OK", "protocol_size": 12},
                    {"fsp_id": params["fsp_ids"], "competition_id": 3, "competition_code": "X", "title": "Волга-Код",
                     "date": "2026-05-01", "discipline_label": "Алг.", "level_label": "—", "place": None, "status": "DSQ",
                     "protocol_size": 30}]
        return {"upcoming": [], "past": [{"title": "Кубок области", "position": "Судья"}]}

    async def aclose(self):
        pass


class QualificationTest(unittest.TestCase):
    def setUp(self):
        sportsman.store.__init__()  # lifespan re-seeds the module-level store
        self.ctx = TestClient(sportsman.app)
        self.api = self.ctx.__enter__()
        sportsman.clients["competition"] = _Competition()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def file(self, **body):
        return self.api.post("/api/v1/qualification-requests", json={"kind": "RANK", "code": "2", **body})

    def test_self_service_only_without_active_trainer(self):
        r = self.file(person_fsp_id=ABRAMOV, submitted_by=ABRAMOV, competition_id=14)
        self.assertEqual(r.status_code, 403)
        self.assertIn("тренер", r.json()["detail"])
        r = self.file(person_fsp_id=PETROVA, submitted_by=PETROVA, competition_id=14)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["purpose"], "CONFIRM")  # Петрова уже имеет II разряд
        self.assertEqual(self.file(person_fsp_id=PETROVA, submitted_by=PETROVA, competition_id=14).status_code, 409)

    def test_trainer_needs_active_link_and_a_real_result(self):
        self.assertEqual(self.file(person_fsp_id=PETROVA, submitted_by=KUZNETSOV, competition_id=14).status_code, 403)
        self.assertEqual(self.file(person_fsp_id=ABRAMOV, submitted_by=KUZNETSOV, competition_id=99).status_code, 422)
        self.assertEqual(self.file(person_fsp_id=ABRAMOV, submitted_by=KUZNETSOV, competition_id=3).status_code, 422)  # DSQ
        r = self.file(person_fsp_id=ABRAMOV, submitted_by=KUZNETSOV, competition_id=14)
        self.assertEqual(r.status_code, 201)
        self.assertIn("2 место из 12", r.json()["basis"]["text"])

    def test_approval_writes_rank_with_order_basis_and_evsk_validity(self):
        rid = self.file(person_fsp_id=PETROVA, submitted_by=PETROVA, competition_id=14).json()["id"]
        decide = f"/api/v1/qualification-requests/{rid}/decision"
        self.assertEqual(self.api.post(decide, json={"status": "APPROVED", "actor": ADMIN}).status_code, 422)  # нет приказа
        r = self.api.post(decide, json={"status": "APPROVED", "actor": ADMIN, "order_no": "Приказ № 1-р"})
        rank = r.json()["person"]["rank"]
        today = config.today()
        self.assertEqual((rank["code"], rank["order_no"], rank["assigned_at"]), ("2", "Приказ № 1-р", today.isoformat()))
        self.assertEqual(rank["expires_at"], today.replace(year=today.year + 2).isoformat())
        self.assertIn("ML-турнир", rank["basis"])
        self.assertEqual(self.api.post(decide, json={"status": "REJECTED", "actor": ADMIN}).status_code, 422)  # уже решено

    def test_judge_files_for_self_and_federation_sets_the_term(self):
        r = self.api.post("/api/v1/qualification-requests",
                          json={"kind": "JUDGE_CATEGORY", "person_fsp_id": GALIEV, "code": "1К", "submitted_by": KUZNETSOV})
        self.assertEqual(r.status_code, 403)
        r = self.api.post("/api/v1/qualification-requests",
                          json={"kind": "JUDGE_CATEGORY", "person_fsp_id": GALIEV, "code": "1К", "submitted_by": GALIEV})
        self.assertEqual((r.status_code, r.json()["basis"]["refereed"]), (201, 1))
        decide = f"/api/v1/qualification-requests/{r.json()['id']}/decision"
        self.assertEqual(self.api.post(decide, json={"status": "APPROVED", "actor": ADMIN, "order_no": "№ 5"}).status_code, 422)
        until = date(config.today().year + 3, 1, 1).isoformat()
        r = self.api.post(decide, json={"status": "APPROVED", "actor": ADMIN, "order_no": "№ 5", "valid_until": until})
        self.assertEqual(r.json()["person"]["judge_category"]["expires_at"], until)

    def test_rejection_keeps_the_profile(self):
        seeded = self.api.get("/api/v1/qualification-requests", params={"person_fsp_id": TIMOFEEV}).json()[0]
        r = self.api.post(f"/api/v1/qualification-requests/{seeded['id']}/decision",
                          json={"status": "REJECTED", "actor": ADMIN, "comment": "Мало практики"})
        self.assertEqual((r.json()["status"], r.json()["person"]["judge_category"]["code"]), ("REJECTED", "3К"))


if __name__ == "__main__":
    unittest.main()

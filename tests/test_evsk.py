"""ЕВСК: чистый движок соответствия требованиям и эндпоинт кандидатов поверх сидированных протоколов."""
import unittest

from fastapi.testclient import TestClient

from competition_service import evsk
from competition_service import main as competition
from shared.demo_roster import ROSTER_BY_SEQ
from sportsman_service.store import Store, seed

ZHUKOV = ROSTER_BY_SEQ[12]["fsp_id"]  # действующая дисквалификация


class EvskEngineTest(unittest.TestCase):
    def test_highest_met_rank_follows_level_place_and_field_size(self):
        met = evsk.met_ranks("REGIONAL", 1, "OK", 10)
        self.assertEqual([r["code"] for r in met][-1], "1")
        self.assertIn("Региональные: 1 место, участников не менее 8", met[-1]["requirement"])
        self.assertEqual(evsk.met_ranks("INTERREG", 1, "OK", 12)[-1]["code"], "КМС")
        self.assertEqual(evsk.met_ranks("REGIONAL", 4, "OK", 10)[-1]["code"], "1Ю")

    def test_small_field_dsq_and_no_place_give_nothing(self):
        self.assertEqual(evsk.met_ranks("REGIONAL", 1, "OK", 5), [])
        self.assertEqual(evsk.met_ranks("REGIONAL", None, "DSQ", 20), [])
        self.assertEqual(evsk.met_ranks("REGIONAL", 12, "OK", 20), [])

    def test_classify_against_the_current_rank(self):
        self.assertEqual(evsk.classify("2", None), "assign")
        self.assertEqual(evsk.classify("1", {"code": "2", "state": "ok"}), "assign")
        self.assertEqual(evsk.classify("2", {"code": "2", "state": "expired"}), "confirm")
        self.assertEqual(evsk.classify("2", {"code": "2", "state": "ok"}), "have")
        self.assertEqual(evsk.classify("2", {"code": "КМС", "state": "expired"}), "have")


class _Sportsman:
    """Stub: реестр спортсменов из демо-ростера."""

    def __init__(self):
        s = Store()
        seed(s)
        self.persons = [s.present(p) for p in s.persons.values() if "athlete" in p.roles]

    async def get(self, path, **params):
        return self.persons

    async def aclose(self):
        pass


class EvskCandidatesTest(unittest.TestCase):
    def setUp(self):
        competition.store.__init__()
        self.ctx = TestClient(competition.app)
        self.api = self.ctx.__enter__()
        competition.clients["sportsman"] = _Sportsman()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_candidates_are_region_athletes_with_a_state_and_a_requirement(self):
        data = self.api.get("/api/v1/evsk/candidates").json()
        rows = data["rows"]
        self.assertTrue(data["ranks_checked"] and rows)
        self.assertTrue(all(r["fsp_id"].split("-")[1] == "56" for r in rows))
        self.assertIn("assign", {r["state"] for r in rows})
        self.assertNotIn(ZHUKOV, {r["fsp_id"] for r in rows})  # санкция
        self.assertNotIn(5, {r["competition_id"] for r in rows})  # ALG5 идёт сейчас — результаты не окончательны
        held = {p["fsp_id"]: p["rank"] for p in competition.clients["sportsman"].persons}
        for r in rows:
            self.assertEqual(r["state"], evsk.classify(r["target"]["code"], held[r["fsp_id"]]))

    def test_filter_by_competition_and_requirements_table(self):
        rows = self.api.get("/api/v1/evsk/candidates").json()["rows"]
        cid = rows[0]["competition_id"]
        only = self.api.get("/api/v1/evsk/candidates", params={"competition_id": cid}).json()["rows"]
        self.assertTrue(only and {r["competition_id"] for r in only} == {cid})
        table = self.api.get("/api/v1/evsk/requirements").json()
        self.assertEqual([t["code"] for t in table][0], "3Ю")
        self.assertTrue(all(t["rules"] for t in table))


if __name__ == "__main__":
    unittest.main()

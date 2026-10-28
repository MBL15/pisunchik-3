"""Рейтинг клубов: сумма баллов спортсменов организации, места при равенстве, фильтр «только клубы», эндпоинт."""
import unittest

from fastapi.testclient import TestClient

from competition_service import main as competition
from competition_service.rating import compute_org_rating
from sportsman_service.store import Store, seed

A, B, C, D = "FSP-56-25-00001-2", "FSP-56-25-00002-0", "FSP-56-25-00003-8", "FSP-56-25-00004-6"
PERSONS = {A: {"organization": "Клуб «Альфа»"}, B: {"organization": "Клуб «Альфа»"}, C: {"organization": "Школа 1"},
           D: {"organization": "Без организации"}, "FSP-56-25-00005-3": {"organization": "Школа 1"}}
ORGS = {"Клуб «Альфа»": {"type": "Клуб", "municipality": "г. Оренбург"}, "Школа 1": {"type": "Школа", "municipality": "г. Орск"}}


def row(fid, final, podiums=0, golds=0, starts=1, name=None):
    return {"fsp_id": fid, "full_name": name or fid, "final": final, "podiums": podiums, "golds": golds, "starts": starts}


class EngineTest(unittest.TestCase):
    def test_sums_points_per_organization_and_skips_those_without_one(self):
        rows = compute_org_rating([row(A, 40, 1, 1), row(B, 30, 1), row(C, 100, 2, 2), row(D, 500)], PERSONS, ORGS)
        self.assertEqual([r["name"] for r in rows], ["Школа 1", "Клуб «Альфа»"])
        alpha = rows[1]
        self.assertEqual((alpha["points"], alpha["athletes"], alpha["podiums"], alpha["golds"], alpha["is_club"]), (70.0, 2, 2, 1, True))
        self.assertEqual(alpha["best"]["fsp_id"], A)
        self.assertEqual((rows[0]["type"], rows[0]["municipality"], rows[0]["is_club"]), ("Школа", "г. Орск", False))

    def test_an_athlete_in_two_disciplines_brings_both_scores(self):
        rows = compute_org_rating([row(A, 40), row(A, 25), row(B, 10)], PERSONS, ORGS)
        self.assertEqual((rows[0]["points"], rows[0]["athletes"]), (75.0, 2))
        self.assertEqual(rows[0]["best"]["points"], 65.0)

    def test_ties_share_the_place_and_golds_break_them_first(self):
        tied = compute_org_rating([row(A, 50), row(C, 50)], PERSONS, ORGS)
        self.assertEqual([r["position"] for r in tied], [1, 1])
        won = compute_org_rating([row(A, 50, 1, 0), row(C, 50, 1, 1)], PERSONS, ORGS)
        self.assertEqual([(r["name"], r["position"]) for r in won], [("Школа 1", 1), ("Клуб «Альфа»", 2)])

    def test_only_clubs_ranks_inside_the_selection(self):
        rows = compute_org_rating([row(A, 10), row(C, 99)], PERSONS, ORGS, only_clubs=True)
        self.assertEqual([(r["name"], r["position"]) for r in rows], [("Клуб «Альфа»", 1)])

    def test_unknown_person_or_empty_input(self):
        self.assertEqual(compute_org_rating([row("FSP-56-25-09999-9", 5)], PERSONS, ORGS), [])
        self.assertEqual(compute_org_rating([], PERSONS, ORGS), [])


class _Sportsman:
    """Stub реестра на сиде sportsman_service: персоны и организации."""

    def __init__(self):
        s = Store()
        seed(s)
        self.persons = [s.present(p) for p in s.persons.values() if "athlete" in p.roles]
        self.orgs = [{"name": "ОГУ — Оренбургский государственный университет", "type": "Вуз", "municipality": "г. Оренбург"}]

    async def get(self, path, **params):
        return self.orgs if path == "/api/v1/organizations" else self.persons

    async def aclose(self):
        pass


class EndpointTest(unittest.TestCase):
    def setUp(self):
        competition.store.__init__()
        self.ctx = TestClient(competition.app)
        self.api = self.ctx.__enter__()
        competition.clients["sportsman"] = _Sportsman()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_all_disciplines_table_matches_the_sum_of_athlete_ratings(self):
        data = self.api.get("/api/v1/rating/organizations").json()
        self.assertEqual((data["discipline"], data["only_clubs"]), ("ALL", False))
        self.assertTrue(data["rows"] and all(r["name"] != "Без организации" for r in data["rows"]))
        persons = {p["fsp_id"]: p for p in competition.clients["sportsman"].persons}
        expected = {}
        for code in ("ALG", "PRD", "SEC", "AI", "ROB", "UAV"):
            for r in self.api.get("/api/v1/rating", params={"discipline": code, "season": data["season"]}).json()["rows"]:
                org = persons[r["fsp_id"]]["organization"]
                if org != "Без организации":
                    expected[org] = round(expected.get(org, 0) + r["final"], 1)
        self.assertEqual({r["name"]: r["points"] for r in data["rows"]}, expected)
        self.assertEqual([r["position"] for r in data["rows"]][0], 1)
        self.assertEqual([r["points"] for r in data["rows"]], sorted((r["points"] for r in data["rows"]), reverse=True))

    def test_single_discipline_filter_and_validation(self):
        alg = self.api.get("/api/v1/rating/organizations", params={"discipline": "ALG"}).json()
        everything = self.api.get("/api/v1/rating/organizations").json()
        self.assertLessEqual(sum(r["points"] for r in alg["rows"]), sum(r["points"] for r in everything["rows"]))
        self.assertEqual(self.api.get("/api/v1/rating/organizations", params={"discipline": "XXX"}).status_code, 422)

    def test_only_clubs_is_empty_without_clubs(self):
        data = self.api.get("/api/v1/rating/organizations", params={"only_clubs": "true"}).json()
        self.assertEqual((data["only_clubs"], data["rows"]), (True, []))


if __name__ == "__main__":
    unittest.main()

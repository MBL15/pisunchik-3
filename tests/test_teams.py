"""Сборная: ручной состав из отфильтрованного рейтинга, роли, утверждение приказом."""
import unittest

from fastapi.testclient import TestClient

from competition_service import main as competition
from shared.demo_roster import ROSTER_BY_SEQ
from sportsman_service.store import Store, seed

ADMIN = ROSTER_BY_SEQ[1]["fsp_id"]
ZHUKOV = ROSTER_BY_SEQ[12]["fsp_id"]  # действующая дисквалификация
ABRAMOV = ROSTER_BY_SEQ[8]["fsp_id"]  # тренер — Кузнецов (ALG, активная связь)


class _Sportsman:
    """Stub реестра: персоны и связи из демо-ростера."""

    def __init__(self):
        self.s = Store()
        seed(self.s)

    async def get(self, path, **params):
        if path == "/api/v1/persons":
            return [self.s.present(p) for p in self.s.persons.values() if "athlete" in p.roles]
        fid = path.split("/")[-2]
        return [self.s.present_link(lk) for lk in self.s.links if lk.athlete_fsp_id == fid]

    async def aclose(self):
        pass


class TeamTest(unittest.TestCase):
    def setUp(self):
        competition.store.__init__()
        self.ctx = TestClient(competition.app)
        self.api = self.ctx.__enter__()
        competition.clients["sportsman"] = _Sportsman()
        rating = self.api.get("/api/v1/rating", params={"discipline": "ALG"}).json()
        self.season, self.rows = rating["season"], rating["rows"]
        self.ids = [r["fsp_id"] for r in self.rows]

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def create(self, fsp_ids=None, **extra):
        body = {"title": "Сборная Оренбургской области по алгоритмике", "discipline": "ALG", "season": self.season,
                "purpose": "для участия во всероссийских соревнованиях", "fsp_ids": fsp_ids or self.ids[:4], "actor": ADMIN, **extra}
        return self.api.post("/api/v1/teams", json=body)

    def test_team_is_a_snapshot_of_rating_rows_in_rating_order(self):
        r = self.create(list(reversed(self.ids[:4])))
        self.assertEqual(r.status_code, 201)
        team = r.json()
        self.assertEqual([m["fsp_id"] for m in team["members"]], self.ids[:4])
        top = self.rows[0]
        first = team["members"][0]
        self.assertEqual((first["position"], first["points"], first["rank_short"]), (top["position"], top["final"], top["rank_short"]))
        self.assertEqual((team["status"], team["main_count"], team["reserve_count"]), ("DRAFT", 4, 0))
        self.assertEqual(self.api.get(f"/api/v1/teams/{team['id']}").json()["title"], team["title"])

    def test_coaches_come_from_active_links_in_the_discipline(self):
        self.assertIn(ABRAMOV, self.ids)
        team = self.create([ABRAMOV]).json()
        self.assertEqual(team["coaches"], ["Кузнецов Андрей Викторович"])

    def test_only_unsanctioned_athletes_from_the_filtered_rating(self):
        r = self.create([self.ids[0], ZHUKOV])
        self.assertEqual(r.status_code, 422)
        self.assertIn(ZHUKOV, r.json()["detail"])
        self.assertEqual(self.create(self.ids[:2], age_group="мальчики/девочки 10–13 лет").status_code, 422)
        self.assertEqual(self.create(self.ids[:2], discipline="XXX").status_code, 422)
        self.assertEqual(self.create([self.ids[0], self.ids[0]]).status_code, 422)

    def test_add_remove_and_roles(self):
        tid = self.create(self.ids[:2]).json()["id"]
        rest = next(f for f in self.ids if f not in self.ids[:2])
        added = self.api.post(f"/api/v1/teams/{tid}/members", json={"fsp_id": rest, "actor": ADMIN})
        self.assertEqual((added.status_code, added.json()["reserve_count"]), (201, 1))
        self.assertEqual(self.api.post(f"/api/v1/teams/{tid}/members", json={"fsp_id": rest, "actor": ADMIN}).status_code, 409)
        self.assertEqual(self.api.post(f"/api/v1/teams/{tid}/members", json={"fsp_id": ZHUKOV, "actor": ADMIN}).status_code, 422)
        role = f"/api/v1/teams/{tid}/members/{rest}/role"
        self.assertEqual(self.api.post(role, json={"role": "MAIN", "actor": ADMIN}).json()["main_count"], 3)
        demoted = [self.api.post(f"/api/v1/teams/{tid}/members/{fid}/role", json={"role": "RESERVE", "actor": ADMIN}).status_code
                   for fid in (rest, *self.ids[:2])]
        self.assertEqual(demoted, [200, 200, 422])  # последнего из основного состава перевести в резерв нельзя
        self.api.delete(f"/api/v1/teams/{tid}/members/{rest}", params={"actor": ADMIN})
        self.assertEqual(len(self.api.get(f"/api/v1/teams/{tid}").json()["members"]), 2)
        self.assertEqual(self.api.delete(f"/api/v1/teams/{tid}/members/{rest}", params={"actor": ADMIN}).status_code, 404)

    def test_approval_needs_an_order_number_and_locks_the_roster(self):
        tid = self.create(self.ids[:3]).json()["id"]
        bad = self.api.post(f"/api/v1/teams/{tid}/approve", json={"order_no": "без номера", "order_date": "2026-11-01", "actor": ADMIN})
        self.assertEqual(bad.status_code, 422)
        ok = self.api.post(f"/api/v1/teams/{tid}/approve", json={"order_no": "№ 412-р", "order_date": "2026-11-01", "actor": ADMIN})
        self.assertEqual((ok.json()["status"], ok.json()["order_no"], ok.json()["locked"]), ("APPROVED", "№ 412-р", True))
        self.assertEqual(self.api.post(f"/api/v1/teams/{tid}/members", json={"fsp_id": self.ids[5], "actor": ADMIN}).status_code, 409)
        self.assertEqual(self.api.put(f"/api/v1/teams/{tid}", json={"title": "Другое название сборной", "actor": ADMIN}).status_code, 409)
        self.assertEqual(self.api.delete(f"/api/v1/teams/{tid}", params={"actor": ADMIN}).status_code, 409)
        self.assertEqual([t["id"] for t in self.api.get("/api/v1/teams", params={"discipline": "ALG"}).json()], [tid])

    def test_draft_can_be_renamed_and_deleted(self):
        tid = self.create(self.ids[:2]).json()["id"]
        r = self.api.put(f"/api/v1/teams/{tid}", json={"title": "Сборная команда региона", "purpose": "сбор", "actor": ADMIN})
        self.assertEqual((r.json()["title"], r.json()["purpose"]), ("Сборная команда региона", "сбор"))
        self.assertEqual(self.api.delete(f"/api/v1/teams/{tid}", params={"actor": ADMIN}).status_code, 200)
        self.assertEqual(self.api.get(f"/api/v1/teams/{tid}").status_code, 404)


if __name__ == "__main__":
    unittest.main()

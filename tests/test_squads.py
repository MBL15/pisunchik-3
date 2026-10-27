"""Выбор дисциплин спортсменом и команды в командных дисциплинах (продуктовое программирование, CTF) — sportsman_service."""
import unittest

from fastapi.testclient import TestClient

from shared.demo_roster import ROSTER_BY_SEQ
from shared.domain import TEAM_MAX_SIZE
from sportsman_service import main as sportsman
from sportsman_service.store import seed

ADMIN = ROSTER_BY_SEQ[1]["fsp_id"]
SOKOLOV = ROSTER_BY_SEQ[3]["fsp_id"]  # ALG, AI — оба командные, но команд у него нет
VALIEVA = ROSTER_BY_SEQ[9]["fsp_id"]  # ALG, SEC — приглашена в «Null Pointers»
ZAYTSEVA = ROSTER_BY_SEQ[13]["fsp_id"]  # SEC, ALG — капитан «Null Pointers»
ISAEV = ROSTER_BY_SEQ[14]["fsp_id"]  # SEC — участник «Null Pointers»
KALININ = ROSTER_BY_SEQ[15]["fsp_id"]  # SEC, без команды
LEBEDEVA = ROSTER_BY_SEQ[16]["fsp_id"]  # PRD, AI — капитан «Квант-Продукт»
NAZAROVA = ROSTER_BY_SEQ[18]["fsp_id"]  # PRD, AI — участница «Квант-Продукт»
KUZNETSOV = ROSTER_BY_SEQ[2]["fsp_id"]  # тренер


class _Base(unittest.TestCase):
    def setUp(self):
        sportsman.store.__init__()
        seed(sportsman.store)
        self.api = TestClient(sportsman.app)

    def squad(self, title):
        return next(s for s in self.api.get("/api/v1/squads").json() if s["title"] == title)

    def set_disciplines(self, fid, codes, actor=None):
        return self.api.put(f"/api/v1/persons/{fid}/disciplines", json={"disciplines": codes, "actor": actor or fid})


class DisciplinesTest(_Base):
    def test_athlete_chooses_own_disciplines(self):
        r = self.set_disciplines(SOKOLOV, ["ALG", "PRD", "SEC"])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["disciplines"], ["ALG", "PRD", "SEC"])
        self.assertEqual(self.api.get(f"/api/v1/persons/{SOKOLOV}").json()["discipline_labels"][1:], ["Продукт", "Кибербезопасность"])

    def test_at_least_one_discipline_and_known_codes(self):
        self.assertEqual(self.set_disciplines(SOKOLOV, []).status_code, 422)
        self.assertEqual(self.set_disciplines(SOKOLOV, ["XXX"]).status_code, 422)

    def test_only_the_person_or_federation_may_change(self):
        self.assertEqual(self.set_disciplines(SOKOLOV, ["ALG"], actor=KALININ).status_code, 403)
        self.assertEqual(self.set_disciplines(SOKOLOV, ["ALG"], actor=ADMIN).status_code, 200)

    def test_duplicates_collapse(self):
        self.assertEqual(self.set_disciplines(SOKOLOV, ["AI", "AI", "ALG"]).json()["disciplines"], ["AI", "ALG"])

    def test_dropping_team_discipline_leaves_squad_and_passes_captaincy(self):
        self.assertEqual(self.set_disciplines(ZAYTSEVA, ["ALG"]).status_code, 200)
        s = self.squad("Null Pointers")
        self.assertEqual((s["captain"], [m["fsp_id"] for m in s["members"]]), (ISAEV, [ISAEV]))
        notes = self.api.get("/api/v1/notifications", params={"fsp_id": ISAEV}).json()["items"]
        self.assertTrue(any("капитан" in n["title"] for n in notes))

    def test_dropping_team_discipline_cancels_its_invites_and_disbands_empty_squad(self):
        self.set_disciplines(VALIEVA, ["ALG"])
        self.assertEqual(self.squad("Null Pointers")["invites"], [])
        self.set_disciplines(LEBEDEVA, ["AI"])
        self.set_disciplines(NAZAROVA, ["AI"])
        self.assertFalse(any(s["title"] == "Квант-Продукт" for s in self.api.get("/api/v1/squads").json()))


class SquadsTest(_Base):
    def create(self, actor, title, discipline):
        return self.api.post("/api/v1/squads", json={"title": title, "discipline": discipline, "actor": actor})

    def act(self, sid, action, **body):
        return self.api.post(f"/api/v1/squads/{sid}/{action}", json=body)

    def test_creator_becomes_captain(self):
        r = self.create(KALININ, "  Red   Team ", "SEC")
        self.assertEqual(r.status_code, 201)
        s = r.json()
        self.assertEqual((s["title"], s["captain"], s["size"], s["max_size"]), ("Red Team", KALININ, 1, TEAM_MAX_SIZE))

    def test_only_team_disciplines_the_athlete_has(self):
        self.assertEqual(self.create(KALININ, "Алгоритмисты", "ALG").status_code, 422)  # ALG нет в профиле
        self.assertEqual(self.create(SOKOLOV, "Продуктовые", "PRD").status_code, 422)  # PRD нет в профиле
        self.assertEqual(self.create(KUZNETSOV, "Тренерская", "SEC").status_code, 403)  # не спортсмен

    def test_algorithmic_and_ai_squads_work_like_the_others(self):
        for who, title, disc in ((SOKOLOV, "Алго-Дрим", "ALG"), (LEBEDEVA, "Нейросеть", "AI")):
            r = self.create(who, title, disc)
            self.assertEqual((r.status_code, r.json()["discipline"], r.json()["captain"]), (201, disc, who))
        self.assertEqual(self.create(SOKOLOV, "Второй состав", "ALG").status_code, 409)  # одна команда на дисциплину

    def test_one_squad_per_discipline_and_unique_title(self):
        self.assertEqual(self.create(ZAYTSEVA, "Вторая команда", "SEC").status_code, 409)
        self.assertEqual(self.create(KALININ, "null pointers", "SEC").status_code, 409)

    def test_invite_accept_flow(self):
        sid = self.squad("Null Pointers")["id"]
        self.assertEqual(self.act(sid, "invite", fsp_id=KALININ, actor=ZAYTSEVA).status_code, 201)
        inbox = self.api.get(f"/api/v1/athletes/{KALININ}/squads").json()
        self.assertEqual([i["squad"]["id"] for i in inbox["invites"]], [sid])
        self.assertTrue(any("Приглашение" in n["title"] for n in self.api.get("/api/v1/notifications", params={"fsp_id": KALININ}).json()["items"]))
        r = self.act(sid, "respond", accept=True, actor=KALININ)
        self.assertEqual(r.status_code, 200)
        self.assertEqual([m["fsp_id"] for m in r.json()["members"]], [ZAYTSEVA, ISAEV, KALININ])
        self.assertEqual(self.api.get(f"/api/v1/athletes/{KALININ}/squads").json()["invites"], [])

    def test_decline_removes_invite(self):
        sid = self.squad("Null Pointers")["id"]
        r = self.act(sid, "respond", accept=False, actor=VALIEVA)
        self.assertEqual((r.status_code, r.json()["invites"], r.json()["size"]), (200, [], 2))
        self.assertEqual(self.act(sid, "respond", accept=True, actor=VALIEVA).status_code, 404)

    def test_invite_rules(self):
        sid = self.squad("Null Pointers")["id"]
        self.assertEqual(self.act(sid, "invite", fsp_id=KALININ, actor=ISAEV).status_code, 403)  # не капитан
        self.assertEqual(self.act(sid, "invite", fsp_id=SOKOLOV, actor=ZAYTSEVA).status_code, 422)  # нет дисциплины SEC
        self.assertEqual(self.act(sid, "invite", fsp_id=KUZNETSOV, actor=ZAYTSEVA).status_code, 422)  # не спортсмен
        self.assertEqual(self.act(sid, "invite", fsp_id=ISAEV, actor=ZAYTSEVA).status_code, 409)  # уже в команде
        self.assertEqual(self.act(sid, "invite", fsp_id=VALIEVA, actor=ZAYTSEVA).status_code, 409)  # уже приглашена
        self.assertEqual(self.act(sid, "invite", fsp_id="FSP-56-26-00000-0", actor=ZAYTSEVA).status_code, 404)

    def test_cannot_join_second_squad_of_same_discipline(self):
        other = self.create(KALININ, "Red Team", "SEC").json()["id"]
        self.assertEqual(self.act(other, "invite", fsp_id=ISAEV, actor=KALININ).status_code, 409)
        sid = self.squad("Null Pointers")["id"]
        self.act(sid, "respond", accept=True, actor=VALIEVA)
        self.assertEqual(self.act(other, "invite", fsp_id=VALIEVA, actor=KALININ).status_code, 409)

    def test_accepting_one_invite_cancels_the_rest_of_that_discipline(self):
        other = self.create(KALININ, "Red Team", "SEC").json()["id"]
        self.act(other, "invite", fsp_id=VALIEVA, actor=KALININ)
        sid = self.squad("Null Pointers")["id"]
        self.act(sid, "respond", accept=True, actor=VALIEVA)
        self.assertEqual(self.api.get(f"/api/v1/squads/{other}").json()["invites"], [])

    def test_size_limit(self):
        sid = self.squad("Null Pointers")["id"]
        sportsman.store.ext.table("squads")[sid]["members"] = [ZAYTSEVA, ISAEV, "a", "b", "c"][:TEAM_MAX_SIZE]
        self.assertEqual(self.act(sid, "invite", fsp_id=KALININ, actor=ZAYTSEVA).status_code, 409)
        self.assertEqual(self.act(sid, "respond", accept=True, actor=VALIEVA).status_code, 409)

    def test_revoke_and_kick(self):
        sid = self.squad("Null Pointers")["id"]
        self.assertEqual(self.act(sid, "revoke", fsp_id=VALIEVA, actor=ISAEV).status_code, 403)
        self.assertEqual(self.act(sid, "revoke", fsp_id=VALIEVA, actor=ZAYTSEVA).json()["invites"], [])
        self.assertEqual(self.act(sid, "kick", fsp_id=ISAEV, actor=ISAEV).status_code, 403)
        self.assertEqual(self.act(sid, "kick", fsp_id=ZAYTSEVA, actor=ZAYTSEVA).status_code, 422)  # капитана не исключить
        self.assertEqual(self.act(sid, "kick", fsp_id=ISAEV, actor=ZAYTSEVA).json()["size"], 1)

    def test_leave_hands_captaincy_and_last_member_disbands(self):
        sid = self.squad("Null Pointers")["id"]
        self.assertEqual(self.act(sid, "leave", actor=KALININ).status_code, 404)
        self.assertEqual(self.act(sid, "leave", actor=ZAYTSEVA).json(), {"left": True, "disbanded": False})
        self.assertEqual(self.api.get(f"/api/v1/squads/{sid}").json()["captain"], ISAEV)
        self.assertEqual(self.act(sid, "leave", actor=ISAEV).json(), {"left": True, "disbanded": True})
        self.assertEqual(self.api.get(f"/api/v1/squads/{sid}").status_code, 404)

    def test_disband_by_captain_or_federation(self):
        sid = self.squad("Null Pointers")["id"]
        self.assertEqual(self.act(sid, "disband", actor=ISAEV).status_code, 403)
        self.assertEqual(self.act(sid, "disband", actor=ADMIN).status_code, 200)
        self.assertEqual(self.api.get(f"/api/v1/squads/{sid}").status_code, 404)
        self.assertEqual(self.create(ZAYTSEVA, "Null Pointers", "SEC").status_code, 201)  # название и дисциплина свободны

    def test_listing_filters_and_athlete_view(self):
        self.assertEqual([s["title"] for s in self.api.get("/api/v1/squads", params={"discipline": "PRD"}).json()], ["Квант-Продукт"])
        self.assertEqual([s["title"] for s in self.api.get("/api/v1/squads", params={"member": ISAEV}).json()], ["Null Pointers"])
        self.assertEqual([s["title"] for s in self.api.get("/api/v1/squads", params={"q": "null point"}).json()], ["Null Pointers"])
        self.assertEqual(self.api.get("/api/v1/squads", params={"discipline": "XXX"}).status_code, 422)
        mine = self.api.get(f"/api/v1/athletes/{KALININ}/squads").json()
        self.assertEqual((mine["squads"], mine["can_create"], mine["team_disciplines"]), ([], ["SEC"], ["SEC"]))
        self.assertEqual(self.api.get(f"/api/v1/athletes/{SOKOLOV}/squads").json()["team_disciplines"], ["ALG", "AI"])


if __name__ == "__main__":
    unittest.main()

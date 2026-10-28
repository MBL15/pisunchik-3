"""Клубы: тренер заводит организацию, основатель ведёт карточку (без лицензии), клуб попадает в общие списки."""
import unittest

from fastapi.testclient import TestClient

from shared import domain
from shared.demo_roster import ROSTER_BY_SEQ
from sportsman_service import clubs, main as sportsman

ADMIN = ROSTER_BY_SEQ[1]["fsp_id"]
TRAINER = ROSTER_BY_SEQ[2]["fsp_id"]    # Кузнецов, ОГУ
OTHER_TRAINER = ROSTER_BY_SEQ[4]["fsp_id"]
ATHLETE = ROSTER_BY_SEQ[8]["fsp_id"]
NAME = "Клуб «Алгоритм» г. Оренбург"


class ClubsTest(unittest.TestCase):
    def setUp(self):
        sportsman.store.__init__()
        self.ctx = TestClient(sportsman.app)
        self.api = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        sportsman.store.__init__()
        clubs.sync()  # клубов в store нет — общие списки возвращаются к справочнику

    def create(self, actor=TRAINER, name=NAME, **extra):
        return self.api.post("/api/v1/clubs", json={"name": name, "municipality": "г. Оренбург", "description": "Олимпиадники",
                                                    "actor": actor, **extra})

    def test_trainer_creates_a_club_that_appears_everywhere(self):
        r = self.create(OTHER_TRAINER)
        self.assertEqual((r.status_code, r.json()["founder_fsp_id"]), (201, OTHER_TRAINER))
        self.assertIn(NAME, self.api.get("/api/v1/organizations-names").json())
        self.assertEqual(self.api.get("/api/v1/organizations-names").json()[-1], domain.NO_ORGANIZATION)
        row = next(o for o in self.api.get("/api/v1/organizations").json() if o["name"] == NAME)
        self.assertEqual((row["type"], row["municipality"]), ("Клуб", "г. Оренбург"))
        card = self.api.get("/api/v1/organizations/" + NAME + "/card").json()
        self.assertEqual((card["club"], card["founder_fsp_id"], card["description"]), (True, OTHER_TRAINER, "Олимпиадники"))

    def test_founder_joins_only_from_no_organization(self):
        self.assertFalse(self.create(TRAINER).json()["joined"])  # у Кузнецова уже есть организация
        self.assertTrue(self.api.get(f"/api/v1/persons/{TRAINER}").json()["organization"].startswith("ОГУ"))
        form = {"last_name": "Новиков", "first_name": "Пётр", "middle_name": "Ильич", "gender": "М", "birth_date": "1990-05-05",
                "municipality": "г. Орск", "organization": domain.NO_ORGANIZATION, "roles": ["trainer"], "disciplines": ["ALG"],
                "email": "new.trainer@example.ru", "password": "secret-pass", "consent_pd": True}
        fid = self.api.post("/api/v1/persons", json=form).json()["fsp_id"]
        r = self.create(fid, name="Клуб «Новый старт»")
        self.assertEqual((r.status_code, r.json()["joined"]), (201, True))
        self.assertEqual(self.api.get(f"/api/v1/persons/{fid}").json()["organization"], "Клуб «Новый старт»")

    def test_only_trainers_and_federation_may_create(self):
        self.assertEqual(self.create(ATHLETE).status_code, 403)
        self.assertEqual(self.create(ADMIN, name="Клуб федерации").status_code, 201)

    def test_name_rules(self):
        self.assertEqual(self.create(name="ab").status_code, 422)
        self.assertEqual(self.create(name="<script>alert(1)</script>").status_code, 422)
        self.assertEqual(self.create(name="123456").status_code, 422)
        self.assertEqual(self.create(name="ОГУ — Оренбургский государственный университет").status_code, 409)
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(self.create(name=NAME.upper()).status_code, 409)  # регистр не обходит уникальность
        self.assertEqual(self.api.post("/api/v1/clubs", json={"name": "Клуб Тест", "municipality": "Нигде", "actor": TRAINER}).status_code, 422)

    def test_limit_per_trainer(self):
        for i in range(clubs.MAX_CLUBS_PER_TRAINER):
            self.assertEqual(self.create(OTHER_TRAINER, name=f"Клуб номер {chr(1040 + i)}").status_code, 201)
        self.assertEqual(self.create(OTHER_TRAINER, name="Клуб лишний").status_code, 409)

    def test_founder_edits_the_card_but_not_the_license(self):
        self.create(OTHER_TRAINER)
        url = f"/api/v1/organizations/{NAME}/card"
        lic = {"number": "ЛО-56-000001", "issued_at": "2026-01-01", "issuer": "Минспорта"}
        body = {"description": "Новое описание", "programs": [{"stage": "НП", "discipline": "ALG"}], "actor": OTHER_TRAINER}
        r = self.api.put(url, json={**body, "license": lic})
        self.assertEqual((r.status_code, r.json()["license"], r.json()["description"]), (200, None, "Новое описание"))
        self.assertEqual(self.api.put(url, json={**body, "actor": TRAINER}).status_code, 403)  # чужой тренер
        fed = self.api.put(url, json={**body, "actor": ADMIN, "license": lic})
        self.assertEqual(fed.json()["license"]["number"], "ЛО-56-000001")
        again = self.api.put(url, json={**body, "description": "Ещё", "license": None})  # основатель не стирает лицензию
        self.assertEqual(again.json()["license"]["number"], "ЛО-56-000001")

    def test_founder_can_open_a_group_in_the_club(self):
        self.create(OTHER_TRAINER)
        self.api.put(f"/api/v1/organizations/{NAME}/card", json={"programs": [{"stage": "НП", "discipline": "ALG"}], "actor": OTHER_TRAINER})
        # OTHER_TRAINER (Ермакова) ведёт ROB/UAV, но роль «Тренер» есть — группа создаётся
        r = self.api.post("/api/v1/groups", json={"org": NAME, "name": "Начинающие алгоритмисты", "stage": "НП", "discipline": "ALG",
                                                  "trainer_fsp_id": OTHER_TRAINER, "actor": OTHER_TRAINER})
        self.assertEqual(r.status_code, 201)

    def test_registration_accepts_a_club_as_organization(self):
        self.create(OTHER_TRAINER)
        form = {"last_name": "Клубов", "first_name": "Иван", "middle_name": "Петрович", "gender": "М", "birth_date": "2001-05-05",
                "municipality": "г. Оренбург", "organization": NAME, "roles": ["athlete"], "disciplines": ["ALG"],
                "email": "club.member@example.ru", "password": "secret-pass", "consent_pd": True}
        self.assertEqual(self.api.post("/api/v1/persons", json=form).status_code, 201)

    def test_clubs_disappear_from_lists_when_the_store_is_reset(self):
        self.create(OTHER_TRAINER)
        sportsman.store.__init__()
        clubs.sync()
        self.assertNotIn(NAME, domain.ORGANIZATIONS)
        self.assertNotIn(NAME, domain.ORGANIZATION_INFO)


if __name__ == "__main__":
    unittest.main()

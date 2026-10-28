"""Личные токены роли и API участника (`/api/me/v1`): выпуск и проверка токенов, права по ролям, пересылка действий, отсутствие лишних персональных данных."""
import unittest

import httpx
from fastapi.testclient import TestClient

from gateway_service import personal_api
from gateway_service.web import clients
from shared.demo_roster import ROSTER_BY_SEQ
from shared.http import ServiceError
from sportsman_service import main as sportsman, user_tokens

ADMIN, TRAINER, ATHLETE, MINISTRY = (ROSTER_BY_SEQ[s]["fsp_id"] for s in (1, 2, 8, 29))
PROTOCOL = {"competition": {"id": 5, "code": "ОРБ-ALG-2026-05", "title": "Кубок"}, "mode": "icpc", "published": False, "published_at": None,
            "entries": [{"fsp_id": ATHLETE, "place": 1}], "tasks": [], "warnings": [], "events": ["служебное"]}


class SportsmanAdapter:
    """Шлюз → настоящий sportsman_service (через TestClient) с теми же ошибками, что у ServiceClient."""

    def __init__(self, api: TestClient):
        self.api = api

    async def request(self, method, path, **kwargs):
        resp = self.api.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise ServiceError("sportsman_service", resp.status_code, resp.json().get("detail", resp.text))
        return resp.json() if resp.content else None

    async def get(self, path, **params):
        return await self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    async def post(self, path, json=None):
        return await self.request("POST", path, json=json)


class CompetitionStub:
    """Календарь: запоминает вызовы и отвечает заготовками — здесь проверяется шлюз, а не сервис соревнований."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.protocol = dict(PROTOCOL)

    async def get(self, path, **params):
        self.calls.append(("GET", path, {k: v for k, v in params.items() if v is not None}))
        if path.endswith("/protocol"):
            return self.protocol
        if path == "/api/v1/dashboard":
            return {"season": 2026, "competitions_season": 21, "finished": 15, "participants_unique": 22, "starts": 143,
                    "documents_issued": 3, "applications_pending": [{"athlete_name": "Секрет"}], "live": [], "upcoming": []}
        return []

    async def post(self, path, json=None):
        self.calls.append(("POST", path, json or {}))
        if path.endswith("/decision"):
            return {"id": 4, "status": json["status"], "status_label": "Одобрена", "athlete_fsp_id": ATHLETE, "submitted_by": TRAINER,
                    "competition_title": "Кубок", "comment": json["comment"]}
        if path == "/api/v1/applications":
            return {"id": 9, "athlete_fsp_id": json["athlete_fsp_id"], "athlete_name": "Абрамов", "competition_title": "Кубок"}
        if path.endswith("/protocol/results") or path.endswith("/protocol/publish"):
            return {**PROTOCOL, "published": bool(json.get("publish", True))}
        return {}

    def downstream(self, method: str) -> list:
        return [c for c in self.calls if c[0] == method]


class PersonalApiTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        sportsman.store.__init__()
        self.ctx = TestClient(sportsman.app)
        self.sp = self.ctx.__enter__()
        self.addCleanup(self.ctx.__exit__, None, None, None)
        self.comp = CompetitionStub()
        self._old = dict(clients)
        clients.update(sportsman=SportsmanAdapter(self.sp), competition=self.comp)
        self.addCleanup(lambda: (clients.clear(), clients.update(self._old)))
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=personal_api.api), base_url="http://api.test")
        self.addAsyncCleanup(self.http.aclose)

    def token(self, fid: str, role: str, name: str = "тест") -> str:
        resp = self.sp.post("/api/v1/user-tokens", json={"actor": fid, "role": role, "name": name})
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()["token"]

    async def get(self, path: str, role_token: str, **params):
        return await self.http.get(path, params=params, headers={"Authorization": f"Bearer {role_token}"})

    async def post(self, path: str, role_token: str, body: dict | None = None):
        return await self.http.post(path, json=body, headers={"Authorization": f"Bearer {role_token}"})

    # ------------------------------------------------------------ токены
    def test_token_is_shown_once_and_only_its_hash_is_stored(self):
        created = self.sp.post("/api/v1/user-tokens", json={"actor": TRAINER, "role": "trainer", "name": "Claude"}).json()
        secret = created["token"]
        self.assertTrue(secret.startswith("fspu_") and len(secret) > 30)
        self.assertEqual((created["role"], created["role_label"], created["active"]), ("trainer", "Тренер", True))
        listed = self.sp.get("/api/v1/user-tokens", params={"owner": TRAINER}).json()
        self.assertEqual(len(listed), 1)
        self.assertNotIn("token", listed[0])
        self.assertNotIn("digest", listed[0])
        stored = next(iter(sportsman.store.ext.table(user_tokens.TABLE).values()))
        self.assertNotIn(secret, str(stored))
        self.assertEqual(stored["digest"], user_tokens._digest(secret))

    def test_token_can_only_be_issued_in_a_role_the_person_has(self):
        self.assertEqual(self.sp.post("/api/v1/user-tokens", json={"actor": ATHLETE, "role": "admin", "name": "хочу"}).status_code, 403)
        self.assertEqual(self.sp.post("/api/v1/user-tokens", json={"actor": "FSP-56-00-99999-9", "role": "trainer", "name": "x1"}).status_code, 404)
        self.assertEqual(self.sp.post("/api/v1/user-tokens", json={"actor": TRAINER, "role": "trainer", "name": "x"}).status_code, 422)

    def test_five_active_tokens_at_most_and_only_the_owner_can_revoke(self):
        ids = [self.sp.post("/api/v1/user-tokens", json={"actor": TRAINER, "role": "trainer", "name": f"токен {i}"}).json()["id"] for i in range(5)]
        self.assertEqual(self.sp.post("/api/v1/user-tokens", json={"actor": TRAINER, "role": "trainer", "name": "шестой"}).status_code, 409)
        self.assertEqual(self.sp.post(f"/api/v1/user-tokens/{ids[0]}/revoke", json={"actor": ATHLETE}).status_code, 403)
        self.assertEqual(self.sp.post(f"/api/v1/user-tokens/{ids[0]}/revoke", json={"actor": TRAINER}).status_code, 200)
        self.assertEqual(self.sp.post("/api/v1/user-tokens", json={"actor": TRAINER, "role": "trainer", "name": "шестой"}).status_code, 201)

    def test_revoked_unknown_and_role_less_tokens_are_indistinguishable(self):
        t = self.token(ADMIN, "judge")
        tid = self.sp.get("/api/v1/user-tokens", params={"owner": ADMIN}).json()[0]["id"]
        self.assertEqual(self.sp.post("/api/v1/user-tokens/verify", json={"token": t}).json()["role"], "judge")
        sportsman.store.persons[ADMIN].roles.remove("judge")  # роль отобрали
        self.assertEqual(self.sp.post("/api/v1/user-tokens/verify", json={"token": t}).status_code, 401)
        sportsman.store.persons[ADMIN].roles.append("judge")
        self.assertEqual(self.sp.post("/api/v1/user-tokens/verify", json={"token": t}).status_code, 200)
        self.sp.post(f"/api/v1/user-tokens/{tid}/revoke", json={"actor": ADMIN})
        self.assertEqual(self.sp.post("/api/v1/user-tokens/verify", json={"token": t}).status_code, 401)
        self.assertEqual(self.sp.post("/api/v1/user-tokens/verify", json={"token": "fspu_" + "x" * 40}).status_code, 401)

    # ------------------------------------------------------------ вход в API
    async def test_missing_bad_and_revoked_tokens_get_401(self):
        self.assertEqual((await self.http.get("/me")).status_code, 401)
        self.assertEqual((await self.get("/me", "fspu_" + "z" * 40)).status_code, 401)
        t = self.token(TRAINER, "trainer")
        self.assertEqual((await self.get("/me", t)).status_code, 200)
        tid = self.sp.get("/api/v1/user-tokens", params={"owner": TRAINER}).json()[0]["id"]
        self.sp.post(f"/api/v1/user-tokens/{tid}/revoke", json={"actor": TRAINER})
        self.assertEqual((await self.get("/me", t)).status_code, 401)

    async def test_me_reports_role_and_capabilities(self):
        me = (await self.get("/me", self.token(MINISTRY, "ministry"))).json()
        self.assertEqual((me["fsp_id"], me["role"], me["role_label"]), (MINISTRY, "ministry", "Министерство спорта"))
        self.assertIn("headcount_report", me["can"])
        self.assertNotIn("decide_application", me["can"])
        self.assertNotIn("email", str(me))

    # ------------------------------------------------------------ права по ролям
    async def test_each_role_sees_only_its_own_operations(self):
        trainer, ministry, athlete, admin = (self.token(TRAINER, "trainer"), self.token(MINISTRY, "ministry"),
                                             self.token(ATHLETE, "athlete"), self.token(ADMIN, "admin"))
        checks = [  # (токен, метод, путь, ожидаемый статус)
            (trainer, "GET", "/reports/headcount", 403), (ministry, "GET", "/reports/headcount", 200), (admin, "GET", "/reports/headcount", 200),
            (athlete, "GET", "/trainer/athletes", 403), (ministry, "GET", "/trainer/athletes", 403), (trainer, "GET", "/trainer/athletes", 200),
            (trainer, "GET", "/teams", 403), (ministry, "GET", "/teams", 200),
            (ministry, "GET", "/admin/applications", 403), (trainer, "GET", "/admin/applications", 403), (admin, "GET", "/admin/applications", 200),
            (trainer, "GET", "/athlete/results", 403), (ministry, "GET", "/judge/assignments", 403),
        ]
        for token, method, path, expected in checks:
            resp = await (self.get(path, token) if method == "GET" else self.post(path, token))
            self.assertEqual(resp.status_code, expected, f"{method} {path}: {resp.text[:120]}")
        denied = (await self.get("/reports/headcount", trainer)).json()["detail"]
        self.assertIn("Министерство", denied)
        self.assertIn("«Тренер»", denied)

    async def test_ministry_cannot_change_anything(self):
        ministry = self.token(MINISTRY, "ministry")
        for path, body in [("/admin/applications/4/decision", {"status": "APPROVED"}), ("/applications", {"competition_id": 1, "athlete_fsp_id": ATHLETE}),
                           ("/admin/competitions/5/publish", None),
                           ("/admin/competitions/5/results", {"results": [{"fsp_id": ATHLETE}]})]:
            self.assertEqual((await self.post(path, ministry, body)).status_code, 403, path)
        self.assertEqual(self.comp.downstream("POST"), [])

    async def test_trainer_athletes_carry_no_contacts_or_birth_dates(self):
        resp = await self.get("/trainer/athletes", self.token(TRAINER, "trainer"))
        links = resp.json()
        self.assertTrue(links)
        for link in links:
            self.assertEqual(link["status"], "ACTIVE")
            a = link["athlete"]
            for hidden in ("email", "birth_date", "guardian", "pd_consent_at"):
                self.assertNotIn(hidden, a)
            self.assertIn("age", a)
            self.assertIn("performance", link)
        self.assertNotIn("@", resp.text)

    # ------------------------------------------------------------ действия
    async def test_trainer_application_is_submitted_on_behalf_of_the_trainer(self):
        resp = await self.post("/applications", self.token(TRAINER, "trainer"), {"competition_id": 3, "athlete_fsp_id": ATHLETE})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self.comp.downstream("POST")[0][2], {"competition_id": 3, "athlete_fsp_id": ATHLETE, "submitted_by": TRAINER})
        no_athlete = await self.post("/applications", self.token(TRAINER, "trainer", "второй"), {"competition_id": 3})
        self.assertEqual(no_athlete.status_code, 422)

    async def test_athlete_application_is_always_for_themselves(self):
        await self.post("/applications", self.token(ATHLETE, "athlete"), {"competition_id": 3, "athlete_fsp_id": "FSP-56-25-00009-5"})
        self.assertEqual(self.comp.downstream("POST")[0][2]["athlete_fsp_id"], ATHLETE)

    async def test_admin_decision_and_results_use_the_token_owner_as_actor(self):
        admin = self.token(ADMIN, "admin")
        resp = await self.post("/admin/applications/4/decision", admin, {"status": "APPROVED", "comment": "ок"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.comp.downstream("POST")[0], ("POST", "/api/v1/applications/4/decision", {"status": "APPROVED", "actor": ADMIN, "comment": "ок"}))
        self.assertEqual((await self.post("/admin/applications/4/decision", admin, {"status": "MAYBE"})).status_code, 422)
        draft = await self.post("/admin/competitions/5/results", admin, {"results": [{"fsp_id": ATHLETE, "solved": 3, "penalty": 90}]})
        self.assertEqual(draft.status_code, 200)
        call = self.comp.downstream("POST")[-1]
        self.assertEqual(call[1], "/api/v1/competitions/5/protocol/results")
        self.assertEqual((call[2]["actor"], call[2]["publish"]), (ADMIN, False))
        self.assertFalse(draft.json()["published"])

    async def test_publish_goes_to_the_publish_endpoint(self):
        resp = await self.post("/admin/competitions/5/publish", self.token(ADMIN, "admin"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["published"])
        self.assertEqual(self.comp.downstream("POST")[-1][1:], ("/api/v1/competitions/5/protocol/publish", {"actor": ADMIN}))

    async def test_draft_protocol_is_for_the_federation_only(self):
        for role_token in (self.token(TRAINER, "trainer"), self.token(MINISTRY, "ministry")):
            self.assertEqual((await self.get("/competitions/5/protocol", role_token)).status_code, 403)
        admin = await self.get("/competitions/5/protocol", self.token(ADMIN, "admin"))
        self.assertEqual(admin.status_code, 200)
        self.assertNotIn("events", admin.json())  # служебные поля не уходят наружу
        self.comp.protocol = {**PROTOCOL, "published": True}
        self.assertEqual((await self.get("/competitions/5/protocol", self.token(TRAINER, "trainer", "ещё"))).status_code, 200)

    async def test_dashboard_has_no_pending_applications_for_ministry(self):
        data = (await self.get("/dashboard", self.token(MINISTRY, "ministry"))).json()
        self.assertEqual(data["season"]["competitions_season"], 21)
        self.assertNotIn("Секрет", str(data))
        self.assertIn("athletes", data["registry"])


if __name__ == "__main__":
    unittest.main()

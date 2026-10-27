"""Д5 апелляции и нарушения, Д6 показатели и вложения, Д8 премии Губернатора, Д2 интеграции — на competition_service."""
import base64
import hashlib
import hmac
import time
import unittest

import httpx
from fastapi.testclient import TestClient

from competition_service import integrations, main as competition
from shared.demo_roster import ROSTER_BY_SEQ
from sportsman_service.store import Store, seed

ADMIN = ROSTER_BY_SEQ[1]["fsp_id"]
TRAINER = ROSTER_BY_SEQ[2]["fsp_id"]
ABRAMOV = ROSTER_BY_SEQ[8]["fsp_id"]
SEC4_WITH_APPROVED = 22  # соревнование, прошедшее без протокола: у 4 спортсменов одобрены заявки


class _Sportsman:
    """Stub реестра на реальном сиде sportsman_service; запоминает вызовы записи (санкции)."""

    def __init__(self):
        self.s = Store()
        seed(self.s)
        self.posted: list[tuple[str, dict]] = []

    async def get(self, path, **params):
        if path == "/api/v1/persons":
            return [self.s.present(p) for p in self.s.persons.values() if params.get("role") in p.roles]
        fid = path.split("/")[-2]
        return [self.s.present_link(lk) for lk in self.s.links if lk.athlete_fsp_id == fid]

    async def post(self, path, json=None):
        self.posted.append((path, json))
        return {}

    async def aclose(self):
        pass


class _Base(unittest.TestCase):
    def setUp(self):
        competition.store.__init__()
        self.ctx = TestClient(competition.app)
        self.api = self.ctx.__enter__()
        self.reg = _Sportsman()
        competition.clients["sportsman"] = self.reg

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def protocol(self, cid):
        return self.api.get(f"/api/v1/competitions/{cid}/protocol").json()

    def published_with(self, fid):
        return next(c["id"] for c in self.api.get("/api/v1/competitions").json()
                    if c["protocol_published"] and any(e["fsp_id"] == fid for e in self.protocol(c["id"])["entries"]))


class AwardsTest(_Base):
    def test_candidates_are_podium_results_priced_by_the_directory(self):
        data = self.api.get("/api/v1/awards/candidates").json()
        rates = data["rates"]
        self.assertTrue(data["items"])
        for item in data["items"]:
            self.assertTrue(all(r["place"] in (1, 2, 3) and r["amount"] == rates[r["level"]][r["place"] - 1] for r in item["results"]))
            self.assertEqual(item["amount"], sum(r["amount"] for r in item["results"]))
        self.assertEqual(data["total"], sum(i["amount"] for i in data["items"]))
        self.assertEqual(data["deadline"]["date"], f"{data['year'] + 1}-02-01")
        self.assertNotIn("Жуков", {i["full_name"].split()[0] for i in data["items"]})  # действующая дисквалификация

    def test_changing_the_directory_changes_amounts_and_is_validated(self):
        before = self.api.get("/api/v1/awards/candidates").json()["total"]
        doubled = {k: [x * 2 for x in v] for k, v in self.api.get("/api/v1/awards/rates").json()["rates"].items()}
        self.assertEqual(self.api.put("/api/v1/awards/rates", json={"rates": doubled, "actor": ADMIN}).status_code, 200)
        self.assertEqual(self.api.get("/api/v1/awards/candidates").json()["total"], before * 2)
        broken = {**doubled, "REGIONAL": [1000, 5000, 100]}
        self.assertEqual(self.api.put("/api/v1/awards/rates", json={"rates": broken, "actor": ADMIN}).status_code, 422)
        self.assertEqual(self.api.put("/api/v1/awards/rates", json={"rates": {"REGIONAL": [1, 1, 1]}, "actor": ADMIN}).status_code, 422)

    def test_petition_lifecycle_masks_requisites_and_closes_the_year(self):
        data = self.api.get("/api/v1/awards/candidates").json()
        drop = data["items"][0]["fsp_id"]
        p = self.api.post("/api/v1/awards/petitions", json={"year": data["year"], "exclude": [drop], "actor": ADMIN}).json()
        self.assertEqual(p["status"], "PREPARED")
        self.assertNotIn(drop, {i["fsp_id"] for i in p["items"]})
        self.assertEqual(p["total"], data["total"] - data["items"][0]["amount"])
        self.assertTrue(all(i["passport"].startswith("**") and set(i["inn"]) == {"*"} and "***" in i["snils"] for i in p["items"]))
        self.assertEqual(self.api.post("/api/v1/awards/petitions", json={"year": data["year"], "actor": ADMIN}).status_code, 409)
        step = lambda status, **kw: self.api.post(f"/api/v1/awards/petitions/{p['id']}/status", json={"status": status, "actor": ADMIN, **kw})  # noqa: E731
        self.assertEqual(step("AWARDED", order_no="№ 1").status_code, 409)  # нельзя перепрыгнуть «направлено»
        self.assertEqual(step("SENT").json()["status"], "SENT")
        self.assertEqual(step("AWARDED").status_code, 422)  # нужны реквизиты акта
        done = step("AWARDED", order_no="Распоряжение № 15-р").json()
        self.assertEqual((done["status"], done["order_no"], done["status_label"]), ("AWARDED", "Распоряжение № 15-р", "Премия назначена"))
        self.assertEqual(step("SENT").status_code, 409)
        self.assertEqual(self.api.get("/api/v1/awards/candidates").json()["petition"]["status"], "AWARDED")


class AppealsTest(_Base):
    def appeal(self, cid, fid=ABRAMOV, actor=None, **kw):
        body = {"competition_id": cid, "athlete_fsp_id": fid, "actor": actor or fid, "subject": "Не засчитана задача C",
                "description": "Решение прошло все тесты, посылка 42 отмечена как неверная — прошу проверить журнал проверок.",
                "evidence": [{"kind": "LOG", "title": "Лог проверки", "url": "https://logs.example.org/42"}], **kw}
        return self.api.post("/api/v1/appeals", json=body)

    def test_submission_rules(self):
        cid = self.published_with(ABRAMOV)
        ok = self.appeal(cid)
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["history"][0]["status"], "SUBMITTED")
        self.assertEqual(self.appeal(cid).status_code, 409)  # такая же апелляция уже рассматривается
        self.assertEqual(self.appeal(cid, evidence=[{"kind": "LOG", "title": "Лог", "url": "javascript:1"}]).status_code, 422)
        self.assertEqual(self.appeal(SEC4_WITH_APPROVED).status_code, 422)  # у соревнования нет протокола
        self.assertEqual(self.appeal(cid, actor=ROSTER_BY_SEQ[5]["fsp_id"]).status_code, 403)  # тренер без привязки к спортсмену

    def test_trainer_with_active_link_may_file_and_author_may_withdraw_only_new(self):
        cid = self.published_with(ABRAMOV)
        made = self.appeal(cid, actor=TRAINER)
        self.assertEqual(made.status_code, 201)
        aid = made.json()["id"]
        self.assertEqual(self.api.post(f"/api/v1/appeals/{aid}/action", json={"actor": ROSTER_BY_SEQ[9]["fsp_id"], "action": "withdraw"}).status_code, 403)
        self.assertEqual(self.api.post(f"/api/v1/appeals/{aid}/action", json={"actor": ADMIN, "action": "review"}).json()["status"], "IN_REVIEW")
        self.assertEqual(self.api.post(f"/api/v1/appeals/{aid}/action", json={"actor": ABRAMOV, "action": "withdraw"}).status_code, 409)

    def test_accepted_appeal_corrects_the_protocol_and_recalculates_places(self):
        cid = self.published_with(ABRAMOV)
        aid = self.appeal(cid).json()["id"]
        decision = {"actor": ADMIN, "accept": True, "rationale": "Проверка журнала подтвердила ошибку системы проверки.",
                    "correction": {"action": "RESCORE", "solved": 26, "penalty": 0, "score": 100}}
        done = self.api.post(f"/api/v1/appeals/{aid}/decision", json=decision).json()
        self.assertEqual(done["status"], "ACCEPTED")
        p = self.protocol(cid)
        after = {e["fsp_id"]: e for e in p["entries"]}[ABRAMOV]
        self.assertEqual((after["place"], p["corrections"][0]["fsp_id"]), (1, ABRAMOV))
        self.assertEqual(p["corrections"][0]["ref"], f"апелляция №{aid}")
        self.assertEqual(self.api.post(f"/api/v1/appeals/{aid}/decision", json=decision).status_code, 409)

    def test_rejection_with_correction_is_refused_and_live_protocols_are_locked(self):
        cid = self.published_with(ABRAMOV)
        aid = self.appeal(cid).json()["id"]
        bad = {"actor": ADMIN, "accept": False, "rationale": "Оснований для пересмотра нет.", "correction": {"action": "DSQ"}}
        self.assertEqual(self.api.post(f"/api/v1/appeals/{aid}/decision", json=bad).status_code, 422)
        rejected = self.api.post(f"/api/v1/appeals/{aid}/decision", json={"actor": ADMIN, "accept": False, "rationale": "Оснований для пересмотра нет."})
        self.assertEqual(rejected.json()["status"], "REJECTED")
        self.assertEqual(self.protocol(cid)["corrections"], [])
        live = next(c["id"] for c in self.api.get("/api/v1/competitions", params={"status": "LIVE"}).json())
        entry = self.protocol(live)["entries"][0]["fsp_id"]
        report = self.api.post("/api/v1/violations", json={"competition_id": live, "subject_fsp_id": entry, "actor": ADMIN,
                                                           "description": "Использование запрещённых библиотек в решении."})
        self.assertEqual(report.status_code, 201)
        r = self.api.post(f"/api/v1/violations/{report.json()['id']}/decision", json={"actor": ADMIN, "decision": "DSQ", "rationale": "Нарушение подтверждено."})
        self.assertEqual(r.status_code, 409)  # live-протокол корректируют после завершения

    def test_violation_decisions_disqualify_zero_out_or_clear(self):
        cid = self.published_with(ABRAMOV)
        entries = [e for e in self.protocol(cid)["entries"] if e["status"] == "OK"]
        dsq, tech, clean = (e["fsp_id"] for e in entries[:3])
        report = lambda fid: self.api.post("/api/v1/violations", json={  # noqa: E731
            "competition_id": cid, "subject_fsp_id": fid, "actor": ADMIN, "description": "Передача решений третьим лицам во время тура."}).json()
        v = report(dsq)
        self.assertEqual(self.api.post(f"/api/v1/violations/{v['id']}/action", json={"actor": ADMIN, "action": "review"}).json()["status"], "IN_REVIEW")
        res = self.api.post(f"/api/v1/violations/{v['id']}/decision", json={"actor": ADMIN, "decision": "DSQ", "sanction_days": 90,
                                                                           "rationale": "Нарушение подтверждено логами.", "reason": "Передача решений"})
        self.assertEqual(res.json()["decision_label"], "Дисквалификация")
        entry = {e["fsp_id"]: e for e in self.protocol(cid)["entries"]}[dsq]
        self.assertEqual((entry["status"], entry["place"], entry["dsq_reason"]), ("DSQ", None, "Передача решений"))
        path, body = self.reg.posted[-1]
        self.assertEqual((path, body["type"], body["actor"]), (f"/api/v1/persons/{dsq}/sanctions", "DISQUALIFICATION", ADMIN))

        t = report(tech)
        self.api.post(f"/api/v1/violations/{t['id']}/decision", json={"actor": ADMIN, "decision": "TECH_DEFEAT", "rationale": "Опоздание на старт тура."})
        e = {x["fsp_id"]: x for x in self.protocol(cid)["entries"]}[tech]
        self.assertEqual((e["status"], e["solved"], e["score"]), ("OK", 0, 0))

        c = report(clean)
        self.api.post(f"/api/v1/violations/{c['id']}/decision", json={"actor": ADMIN, "decision": "NO_VIOLATION", "rationale": "Доказательств недостаточно."})
        self.assertEqual({x["fsp_id"]: x for x in self.protocol(cid)["entries"]}[clean]["status"], "OK")
        self.assertEqual(len(self.protocol(cid)["corrections"]), 2)  # нарушения нет → протокол не менялся
        self.assertEqual(self.api.post(f"/api/v1/violations/{c['id']}/decision", json={"actor": ADMIN, "decision": "DSQ", "rationale": "Повторное решение."}).status_code, 409)


class PublishedLiveProtocolTest(_Base):
    def test_a_finished_live_competition_can_be_corrected_after_publication(self):
        live = 5
        self.api.post(f"/api/v1/competitions/{live}/protocol/publish", json={"actor": ADMIN})
        fid = next(e["fsp_id"] for e in self.protocol(live)["entries"] if e["status"] == "OK")
        v = self.api.post("/api/v1/violations", json={"competition_id": live, "subject_fsp_id": fid, "actor": ADMIN,
                                                      "description": "Использование запрещённых библиотек в решении."}).json()
        r = self.api.post(f"/api/v1/violations/{v['id']}/decision", json={"actor": ADMIN, "decision": "DSQ", "rationale": "Нарушение подтверждено."})
        self.assertEqual(r.status_code, 200)
        self.assertEqual({e["fsp_id"]: e for e in self.protocol(live)["entries"]}[fid]["status"], "DSQ")


class DevelopmentTest(_Base):
    def attach(self, **kw):
        return self.api.post("/api/v1/attachments", json={"indicator": "headcount", "year": 2026, "title": "Справка организации", "actor": ADMIN, **kw})

    def test_competitions_of_the_year_with_plans_and_statuses(self):
        d = self.api.get("/api/v1/development/competitions", params={"year": 2026}).json()
        self.assertEqual(d["kpi"]["total"], len(d["items"]))
        self.assertTrue(d["kpi"]["held"] > 0 and d["kpi"]["participants"] > 0)
        self.assertTrue(all(r["plan_number"] and r["plan_label"] for r in d["items"]))
        self.assertIn(2026, d["years"])

    def test_attachments_validate_type_size_and_exclusivity(self):
        pdf = base64.b64encode(b"%PDF-1.4 demo").decode()
        self.assertEqual(self.attach(url="https://docs.example.org/a.pdf").status_code, 201)
        self.assertEqual(self.attach(url="javascript:alert(1)").status_code, 422)
        self.assertEqual(self.attach().status_code, 422)  # ни файла, ни ссылки
        self.assertEqual(self.attach(url="https://x.example", filename="a.pdf", content_b64=pdf).status_code, 422)  # и то и другое
        self.assertEqual(self.attach(filename="virus.exe", content_b64=pdf).status_code, 422)
        self.assertEqual(self.attach(filename="big.pdf", content_b64=base64.b64encode(b"0" * (2 * 1024 * 1024 + 1)).decode()).status_code, 422)
        self.assertEqual(self.attach(filename="a.pdf", content_b64="!!!не base64").status_code, 422)
        made = self.attach(filename="Справка.pdf", content_b64=pdf)
        self.assertEqual(made.status_code, 201)
        self.assertNotIn("content_b64", made.json())
        got = self.api.get(f"/api/v1/attachments/{made.json()['id']}/file").json()
        self.assertEqual((got["media_type"], base64.b64decode(got["content_b64"])), ("application/pdf", b"%PDF-1.4 demo"))
        listed = self.api.get("/api/v1/attachments", params={"indicator": "headcount", "year": 2026}).json()
        self.assertEqual(len(listed), 2)
        self.assertEqual(self.api.delete(f"/api/v1/attachments/{made.json()['id']}", params={"actor": ADMIN}).status_code, 200)


class IntegrationsTest(_Base):
    def setUp(self):
        super().setUp()
        self.sent: list[httpx.Request] = []
        integrations._transport = httpx.MockTransport(lambda req: (self.sent.append(req), httpx.Response(200, json={"ok": True}))[1])
        self.addCleanup(setattr, integrations, "_transport", None)

    def system(self, scopes=("competitions:read",)):
        return self.api.post("/api/v1/integrations/clients", json={"name": "Партнёр", "scopes": list(scopes), "actor": ADMIN}).json()

    def test_token_is_shown_once_verified_scoped_and_revocable(self):
        made = self.system(("competitions:read", "results:write"))
        self.assertTrue(made["token"].startswith("fspi_") and "digest" not in made)
        self.assertNotIn(made["token"], str(self.api.get("/api/v1/integrations/clients").json()))
        ok = self.api.post("/api/v1/integrations/verify", json={"token": made["token"]}).json()
        self.assertEqual((ok["scopes"], ok["client_id"]), (["competitions:read", "results:write"], made["id"]))
        self.assertEqual(self.api.post("/api/v1/integrations/verify", json={"token": "fspi_wrong"}).status_code, 401)
        self.assertEqual(self.api.post("/api/v1/integrations/clients", json={"name": "Партнёр", "scopes": ["admin:all"], "actor": ADMIN}).status_code, 422)
        self.api.post(f"/api/v1/integrations/clients/{made['id']}/revoke", params={"actor": ADMIN})
        self.assertEqual(self.api.post("/api/v1/integrations/verify", json={"token": made["token"]}).status_code, 401)

    def test_webhook_url_and_events_are_validated(self):
        sys = self.system()
        hook = lambda **kw: self.api.post("/api/v1/integrations/webhooks", json={  # noqa: E731
            "client_id": sys["id"], "url": "https://partner.example.org/hook", "events": ["protocol.published"], "actor": ADMIN, **kw})
        self.assertEqual(hook().status_code, 201)
        self.assertEqual(hook(url="file:///etc/passwd").status_code, 422)
        self.assertEqual(hook(url="http://169.254.169.254/latest/meta-data").status_code, 422)
        self.assertEqual(hook(events=["user.deleted"]).status_code, 422)
        self.assertNotIn("secret", self.api.get("/api/v1/integrations/webhooks").json()[0])

    def wait_for(self, n, seconds=3.0):
        end = time.time() + seconds
        while len(self.sent) < n and time.time() < end:
            time.sleep(0.05)

    def test_events_are_delivered_signed_and_logged(self):
        sys = self.system()
        hook = self.api.post("/api/v1/integrations/webhooks", json={"client_id": sys["id"], "url": "https://partner.example.org/hook",
                                                                    "events": ["protocol.published", "application.approved"], "actor": ADMIN}).json()
        proto_comp = SEC4_WITH_APPROVED
        # одобрение заявки → application.approved
        pending = self.api.get("/api/v1/applications", params={"status": "PENDING"}).json()[0]
        self.api.post(f"/api/v1/applications/{pending['id']}/decision", json={"status": "APPROVED", "actor": ADMIN, "comment": ""})
        self.wait_for(1)
        req = self.sent[0]
        body = req.content
        self.assertEqual(req.headers["x-fsp-event"], "application.approved")
        self.assertEqual(req.headers["x-fsp-signature"], "sha256=" + hmac.new(hook["secret"].encode(), body, hashlib.sha256).hexdigest())
        payload = httpx.Response(200, content=body).json()
        self.assertEqual(payload["data"]["athlete_fsp_id"], pending["athlete_fsp_id"])
        self.assertNotIn("athlete_name", payload["data"])  # в вебхуке только идентификаторы, без ФИО
        # публикация протокола → protocol.published
        self.api.post(f"/api/v1/competitions/{proto_comp}/protocol/results", json={"actor": ADMIN, "publish": True, "results": [
            {"fsp_id": a["athlete_fsp_id"], "solved": 3, "penalty": 100 + i} for i, a in enumerate(
                x for x in self.api.get("/api/v1/applications", params={"competition_id": proto_comp}).json() if x["status"] == "APPROVED")]})
        self.wait_for(2)
        self.assertEqual([r.headers["x-fsp-event"] for r in self.sent], ["application.approved", "protocol.published"])
        log = self.api.get("/api/v1/integrations/deliveries").json()
        self.assertTrue(len(log) == 2 and all(d["ok"] and d["status_code"] == 200 for d in log))
        # вебхук, подписанный на другое событие, не получает лишнего
        self.api.post("/api/v1/competitions", json={
            "title": "Открытый кубок проверки", "discipline": "ALG", "level": "REGIONAL", "type": "Кубок", "format": "ONLINE", "calendar_plan": "REGIONAL",
            "plan_number": "№ 56-300", "start_date": "2027-03-01", "end_date": "2027-03-01", "venue": "онлайн", "age_group": "без ограничений 12+",
            "registration_deadline": "2027-02-20", "actor": ADMIN})
        time.sleep(0.3)
        self.assertEqual(len(self.sent), 2)

    def test_failed_delivery_is_logged_and_test_event_is_available(self):
        sys = self.system()
        hook = self.api.post("/api/v1/integrations/webhooks", json={"client_id": sys["id"], "url": "https://partner.example.org/hook",
                                                                    "events": ["protocol.published"], "actor": ADMIN}).json()
        integrations._transport = httpx.MockTransport(lambda req: httpx.Response(500))
        d = self.api.post(f"/api/v1/integrations/webhooks/{hook['id']}/test", params={"actor": ADMIN}).json()
        self.assertEqual((d["ok"], d["status_code"], d["event"]), (False, 500, "ping"))

        def boom(req):
            raise httpx.ConnectError("нет соединения")
        integrations._transport = httpx.MockTransport(boom)
        d = self.api.post(f"/api/v1/integrations/webhooks/{hook['id']}/test", params={"actor": ADMIN}).json()
        self.assertEqual((d["ok"], d["error"]), (False, "ConnectError"))

    def test_import_creates_a_draft_protocol_only_for_approved_participants(self):
        full = self.system(("competitions:read", "results:write"))
        read_only = self.system()
        approved = [a["athlete_fsp_id"] for a in self.api.get("/api/v1/applications", params={"competition_id": SEC4_WITH_APPROVED}).json()
                    if a["status"] == "APPROVED"]
        rows = [{"fsp_id": fid, "solved": 5 - i, "penalty": 200 + i * 10} for i, fid in enumerate(approved)]
        url = f"/api/v1/integrations/import/{SEC4_WITH_APPROVED}"
        self.assertEqual(self.api.post(url, json={"client_id": read_only["id"], "results": rows}).status_code, 403)
        stranger = self.api.post(url, json={"client_id": full["id"], "results": rows + [{"fsp_id": ROSTER_BY_SEQ[3]["fsp_id"], "solved": 9}]})
        self.assertEqual(stranger.status_code, 422)
        ok = self.api.post(url, json={"client_id": full["id"], "system": "Партнёр", "results": rows})
        self.assertEqual(ok.status_code, 200)
        p = ok.json()
        self.assertEqual((p["published"], len(p["entries"]), p["entries"][0]["place"]), (False, len(approved), 1))
        self.assertTrue(any("ext:Партнёр (Партнёр)" in a["actor"] for a in self.api.get("/api/v1/audit").json()))


if __name__ == "__main__":
    unittest.main()

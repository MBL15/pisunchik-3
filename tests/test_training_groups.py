"""Д3 организации, группы, журнал; Д4 нормативы и аттестация; Д1 реестр клиентов ФСП ID; роль министерства — на реестре sportsman_service."""
import unittest
from datetime import timedelta

from fastapi.testclient import TestClient

from shared import config
from shared.demo_roster import ROSTER_BY_SEQ
from shared.training import evaluate, next_stage
from sportsman_service import main as sportsman
from sportsman_service.store import seed

ADMIN = ROSTER_BY_SEQ[1]["fsp_id"]
KUZNETSOV = ROSTER_BY_SEQ[2]["fsp_id"]  # тренер ОГУ, подопечные: Абрамов, Валиева, Григорьев, Дьяченко, Зайцева…
SOKOLOV = ROSTER_BY_SEQ[3]["fsp_id"]
ABRAMOV = ROSTER_BY_SEQ[8]["fsp_id"]  # сдал все нормативы этапа (seq % 4 == 0)
GRIGORIEV = ROSTER_BY_SEQ[10]["fsp_id"]  # не сдан норматив «скорость печати» (seq % 4 == 2)
DYACHENKO = ROSTER_BY_SEQ[11]["fsp_id"]  # нормативы ещё не сдавал (seq % 4 == 3)
ZHUKOV = ROSTER_BY_SEQ[12]["fsp_id"]  # без организации и этапа подготовки
MINISTRY = ROSTER_BY_SEQ[29]["fsp_id"]


class _Base(unittest.TestCase):
    def setUp(self):
        sportsman.store.__init__()
        seed(sportsman.store)
        self.api = TestClient(sportsman.app)

    def norm_id(self, fid, title_part):
        rows = self.api.get(f"/api/v1/athletes/{fid}/training").json()["norms"]
        return next(r["norm"]["id"] for r in rows if title_part in r["norm"]["title"])


class NormsTest(_Base):
    def test_directory_covers_stages_and_includes_typing_speed(self):
        for stage in ("НП", "УТ", "ССМ", "ВСМ"):
            titles = {n["title"] for n in self.api.get("/api/v1/norms", params={"stage": stage}).json()}
            self.assertIn("Скорость печати на клавиатуре", titles)
        self.assertEqual((next_stage("НП"), next_stage("ВСМ"), next_stage(None)), ("УТ", None, None))
        self.assertTrue(evaluate("MIN", 180, 181) and not evaluate("MIN", 180, 179) and evaluate("MAX", 6.0, 5.9) and not evaluate("MAX", 6.0, 6.1))

    def test_platform_decides_whether_a_norm_is_passed(self):
        nid = self.norm_id(GRIGORIEV, "Скорость печати")
        row = next(r for r in self.api.get(f"/api/v1/athletes/{GRIGORIEV}/training").json()["norms"] if r["norm"]["id"] == nid)
        self.assertFalse(row["passed"])
        thr = row["norm"]["threshold"]
        bad = self.api.post("/api/v1/norm-results", json={"norm_id": nid, "athlete_fsp_id": GRIGORIEV, "value": thr - 1, "actor": KUZNETSOV})
        self.assertFalse(bad.json()["passed"])
        good = self.api.post("/api/v1/norm-results", json={"norm_id": nid, "athlete_fsp_id": GRIGORIEV, "value": thr + 5, "actor": KUZNETSOV})
        self.assertTrue(good.json()["passed"])
        self.assertTrue(good.json()["ready"])  # последняя попытка зачётная → все обязательные нормативы закрыты

    def test_only_federation_or_active_trainer_enter_results_and_only_applicable_norms(self):
        nid = self.norm_id(ABRAMOV, "Скорость печати")
        body = {"norm_id": nid, "athlete_fsp_id": ABRAMOV, "value": 999}
        self.assertEqual(self.api.post("/api/v1/norm-results", json={**body, "actor": ROSTER_BY_SEQ[5]["fsp_id"]}).status_code, 403)
        self.assertEqual(self.api.post("/api/v1/norm-results", json={**body, "actor": ABRAMOV}).status_code, 403)
        self.assertEqual(self.api.post("/api/v1/norm-results", json={**body, "actor": ADMIN}).status_code, 201)
        other_stage = next(n["id"] for n in self.api.get("/api/v1/norms", params={"stage": "ВСМ"}).json() if n["stage"] != self.stage(ABRAMOV))
        self.assertEqual(self.api.post("/api/v1/norm-results", json={**body, "norm_id": other_stage, "actor": ADMIN}).status_code, 422)
        future = (config.today() + timedelta(days=1)).isoformat()
        self.assertEqual(self.api.post("/api/v1/norm-results", json={**body, "performed_on": future, "actor": ADMIN}).status_code, 422)

    def stage(self, fid):
        return self.api.get(f"/api/v1/athletes/{fid}/training").json()["stage"]

    def test_attestation_moves_to_next_stage_only_when_ready(self):
        self.assertEqual(self.api.post(f"/api/v1/athletes/{GRIGORIEV}/attestation", json={"actor": KUZNETSOV}).status_code, 409)
        before = self.stage(ABRAMOV)
        r = self.api.post(f"/api/v1/athletes/{ABRAMOV}/attestation", json={"actor": KUZNETSOV, "note": "сдал"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.json()["from_stage"], r.json()["to_stage"]), (before, next_stage(before)))
        state = self.api.get(f"/api/v1/athletes/{ABRAMOV}/training").json()
        self.assertEqual(state["stage"], next_stage(before))
        self.assertFalse(state["ready"])  # на новом этапе нормативы ещё не сданы
        self.assertEqual(state["attestations"][0]["to_stage"], next_stage(before))
        self.assertEqual(self.api.post(f"/api/v1/athletes/{ABRAMOV}/attestation", json={"actor": KUZNETSOV}).status_code, 409)

    def test_threshold_change_does_not_rewrite_past_attempts(self):
        nid = self.norm_id(ABRAMOV, "Скорость печати")
        norm = next(n for n in self.api.get("/api/v1/norms").json() if n["id"] == nid)
        body = {k: norm[k] for k in ("title", "kind", "stage", "discipline", "gender", "unit", "direction", "required", "active", "note")}
        r = self.api.put(f"/api/v1/norms/{nid}", json={**body, "threshold": norm["threshold"] * 10, "actor": ADMIN})
        self.assertEqual(r.status_code, 200)
        past = next(r for r in self.api.get(f"/api/v1/athletes/{ABRAMOV}/training").json()["norms"] if r["norm"]["id"] == nid)
        self.assertTrue(past["passed"])  # попытка оценена по порогу своего времени

    def test_stage_is_assigned_by_federation_only_when_missing(self):
        self.assertEqual(self.api.post(f"/api/v1/athletes/{ZHUKOV}/stage", json={"stage": "НП", "actor": KUZNETSOV}).status_code, 403)
        self.assertEqual(self.api.post(f"/api/v1/athletes/{ZHUKOV}/stage", json={"stage": "НП", "actor": ADMIN}).status_code, 200)
        self.assertEqual(self.api.post(f"/api/v1/athletes/{ZHUKOV}/stage", json={"stage": "УТ", "actor": ADMIN}).status_code, 409)

    def test_overview_filters_by_trainer_and_readiness(self):
        mine = self.api.get("/api/v1/training/overview", params={"trainer_fsp_id": KUZNETSOV}).json()
        self.assertTrue(mine["total"] >= 5 and all(a["fsp_id"] != SOKOLOV for a in mine["items"]))
        ready = self.api.get("/api/v1/training/overview", params={"ready": True}).json()
        self.assertTrue(ready["total"] >= 1 and all(a["ready"] for a in ready["items"]))
        self.assertIn(ABRAMOV, {a["fsp_id"] for a in ready["items"]})


class GroupsTest(_Base):
    def first_group(self):
        return next(g for g in self.api.get("/api/v1/groups").json() if g["org"].startswith("Лицей"))

    def test_seeded_cards_groups_and_journal(self):
        card = self.api.get("/api/v1/organizations/ОГУ — Оренбургский государственный университет/card").json()
        self.assertTrue(card["license"] and card["programs"] and card["groups"] and card["sessions"]["total"] > 0)
        self.assertTrue(any(t["fsp_id"] == KUZNETSOV for t in card["trainers"]))
        self.assertEqual(self.api.get("/api/v1/organizations/Без организации/card").status_code, 404)
        states = {self.api.get("/api/v1/organizations/" + o["name"] + "/card").json()["license_state"]["state"] for o in
                  self.api.get("/api/v1/organizations").json() if o["name"] in sportsman.ORGANIZATION_INFO}
        self.assertIn("warning", states)  # одна лицензия скоро истекает

    def test_enrollment_rules(self):
        g = self.first_group()
        self.assertEqual(self.api.post(f"/api/v1/groups/{g['id']}/members", json={"fsp_id": ZHUKOV, "actor": ADMIN}).status_code, 422)  # другая организация
        self.assertEqual(self.api.post(f"/api/v1/groups/{g['id']}/members", json={"fsp_id": ABRAMOV, "actor": ROSTER_BY_SEQ[5]["fsp_id"]}).status_code, 403)
        self.assertEqual(self.api.post(f"/api/v1/groups/{g['id']}/members", json={"fsp_id": ABRAMOV, "actor": ADMIN}).status_code, 409)  # уже в группе

    def test_leave_and_reenroll_then_transfer_needs_attestation(self):
        src = self.first_group()
        members = [m for m in self.api.get(f"/api/v1/groups/{src['id']}").json()["members"] if m["active"]]
        athlete = next(m for m in members if m["ready"])["fsp_id"]
        card = self.api.get(f"/api/v1/organizations/{src['org']}/card").json()
        programs = [{"stage": p["stage"], "discipline": p["discipline"]} for p in card["programs"]]
        nxt = next_stage(src["stage"])
        programs.append({"stage": nxt, "discipline": src["discipline"]})
        self.assertEqual(self.api.put(f"/api/v1/organizations/{src['org']}/card", json={"programs": programs, "actor": ADMIN}).status_code, 200)
        dst = self.api.post("/api/v1/groups", json={"org": src["org"], "name": "Новая группа следующего этапа", "stage": nxt,
                                                    "discipline": src["discipline"], "trainer_fsp_id": src["trainer_fsp_id"], "actor": ADMIN}).json()
        # не готовый к переводу спортсмен в группу следующего этапа не попадает
        unready = next(m["fsp_id"] for m in members if not m["ready"]) if any(not m["ready"] for m in members) else None
        if unready:
            r = self.api.post(f"/api/v1/groups/{src['id']}/transfer", json={"fsp_id": unready, "to_group_id": dst["id"], "actor": ADMIN})
            self.assertEqual(r.status_code, 409)
        ok = self.api.post(f"/api/v1/groups/{src['id']}/transfer", json={"fsp_id": athlete, "to_group_id": dst["id"], "actor": ADMIN})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["attestation"]["to_stage"], nxt)
        self.assertEqual(self.api.get(f"/api/v1/athletes/{athlete}/training").json()["stage"], nxt)
        in_src = next(m for m in self.api.get(f"/api/v1/groups/{src['id']}").json()["members"] if m["fsp_id"] == athlete)
        self.assertFalse(in_src["active"])

    def test_session_journal_rules(self):
        g = self.first_group()
        present = [m["fsp_id"] for m in self.api.get(f"/api/v1/groups/{g['id']}").json()["members"] if m["active"]]
        base = {"on": config.today().isoformat(), "topic": "Разбор контеста", "duration_min": 90, "attendees": present, "actor": g["trainer_fsp_id"]}
        post = lambda **kw: self.api.post(f"/api/v1/groups/{g['id']}/sessions", json={**base, **kw})  # noqa: E731
        self.assertEqual(post(format="ONLINE").status_code, 422)  # онлайн без подтверждения
        self.assertEqual(post(format="ONLINE", confirmation_kind="RECORDING", confirmation_ref="не-ссылка").status_code, 422)
        self.assertEqual(post(format="OFFLINE", confirmation_kind="RECORDING", confirmation_ref="https://x.example/r").status_code, 422)
        self.assertEqual(post(format="ONLINE", confirmation_kind="PLATFORM_LOG", confirmation_ref="LOG-77").status_code, 201)
        self.assertEqual(post(format="OFFLINE").status_code, 201)
        self.assertEqual(post(format="OFFLINE", on=(config.today() + timedelta(days=1)).isoformat()).status_code, 422)
        self.assertEqual(post(format="OFFLINE", attendees=[ZHUKOV]).status_code, 422)
        self.assertEqual(post(format="OFFLINE", actor=ROSTER_BY_SEQ[5]["fsp_id"]).status_code, 403)

    def test_groups_and_journal_feed_the_headcount_report(self):
        report = self.api.get("/api/v1/reports/headcount", params={"years": 2}).json()
        keys = {c["key"] for c in report["cuts"]}
        self.assertTrue({"groups", "stage_groups", "sessions"} <= keys)
        cut = next(c for c in report["cuts"] if c["key"] == "sessions")
        self.assertEqual(len(cut["rows"]), 3)
        self.assertTrue(all(len(r["counts"]) == 2 for r in cut["rows"]))
        self.assertTrue(cut["rows"][0]["counts"][-1] > 0)
        grouped = next(c for c in report["cuts"] if c["key"] == "groups")["rows"][0]["counts"]
        self.assertGreater(grouped[-1], 0)


class OidcClientsTest(_Base):
    def test_registration_verification_and_revocation(self):
        body = {"name": "Сайт партнёра", "redirect_uris": ["https://partner.example.org/cb"], "actor": ADMIN}
        self.assertEqual(self.api.post("/api/v1/oidc/clients", json={**body, "actor": KUZNETSOV}).status_code, 403)
        for bad in ("http://partner.example.org/cb", "https://partner.example.org/cb#frag", "ftp://x/cb"):
            self.assertEqual(self.api.post("/api/v1/oidc/clients", json={**body, "redirect_uris": [bad]}).status_code, 422)
        self.assertEqual(self.api.post("/api/v1/oidc/clients", json={**body, "redirect_uris": ["http://localhost:3000/cb"]}).status_code, 201)
        made = self.api.post("/api/v1/oidc/clients", json=body).json()
        self.assertTrue(made["client_secret"].startswith("fspcs_") and "secret_digest" not in made)
        verify = lambda secret: self.api.post("/api/v1/oidc/clients/verify", json={"client_id": made["client_id"], "secret": secret})  # noqa: E731
        self.assertEqual(verify(made["client_secret"]).status_code, 200)
        self.assertEqual(verify("wrong").status_code, 401)
        self.assertEqual(verify(None).status_code, 401)
        self.api.post(f"/api/v1/oidc/clients/{made['id']}/revoke", params={"actor": ADMIN})
        self.assertEqual(verify(made["client_secret"]).status_code, 401)
        self.assertEqual(self.api.get(f"/api/v1/oidc/clients/by-id/{made['client_id']}").status_code, 404)

    def test_public_client_has_no_secret_and_demo_client_is_seeded(self):
        pub = self.api.post("/api/v1/oidc/clients", json={"name": "SPA партнёра", "redirect_uris": ["https://spa.example.org/cb"], "public": True,
                                                         "actor": ADMIN}).json()
        self.assertIsNone(pub["client_secret"])
        self.assertEqual(self.api.post("/api/v1/oidc/clients/verify", json={"client_id": pub["client_id"]}).status_code, 200)
        self.assertEqual(self.api.post("/api/v1/oidc/clients/verify", json={"client_id": "fsp-demo-client", "secret": "fsp-demo-secret"}).status_code, 200)


class SanctionsTest(_Base):
    def test_federation_imposes_a_sanction_that_excludes_the_athlete(self):
        body = {"type": "DISQUALIFICATION", "date_from": config.today().isoformat(), "date_to": (config.today() + timedelta(days=30)).isoformat(),
                "reason": "Решение по рапорту о нарушении", "actor": ADMIN}
        r = self.api.post(f"/api/v1/persons/{ABRAMOV}/sanctions", json=body)
        self.assertEqual(r.status_code, 201)
        self.assertTrue(r.json()["is_sanctioned"])
        self.assertEqual(self.api.post(f"/api/v1/persons/{ABRAMOV}/sanctions", json={**body, "date_to": "2020-01-01"}).status_code, 422)
        self.assertEqual(self.api.post("/api/v1/persons/FSP-56-00-00000-0/sanctions", json=body).status_code, 404)


class MinistryRoleTest(_Base):
    def test_ministry_person_is_in_the_registry_with_its_own_role(self):
        p = self.api.get(f"/api/v1/persons/{MINISTRY}").json()
        self.assertEqual((p["roles"], p["role_labels"]), (["ministry"], ["Министерство спорта"]))
        self.assertNotIn(MINISTRY, {x["fsp_id"] for x in self.api.get("/api/v1/persons", params={"role": "athlete"}).json()})
        self.assertEqual(self.api.post("/api/v1/auth/verify", json={"email": "ministry@fsp56.ru", "password": "fsp2026"}).status_code, 200)


if __name__ == "__main__":
    unittest.main()

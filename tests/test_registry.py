"""Реестр с фильтрами и организации, антидопинг, лента уведомлений, сроки отчётов и пороги напоминаний."""
import asyncio
import unittest
from datetime import date

from fastapi.testclient import TestClient

from gateway_service import notify
from shared.demo_roster import ROSTER_BY_SEQ
from shared.domain import report_deadlines
from sportsman_service import main as sportsman

ADMIN, VALIEVA, ZHUKOV = (ROSTER_BY_SEQ[s]["fsp_id"] for s in (1, 9, 12))


class RegistryTest(unittest.TestCase):
    def setUp(self):
        sportsman.store.__init__()
        self.ctx = TestClient(sportsman.app)
        self.api = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def persons(self, **q):
        r = self.api.get("/api/v1/persons", params={"role": "athlete", **q})
        self.assertEqual(r.status_code, 200, r.text)
        return {p["fsp_id"] for p in r.json()}

    def test_filters_by_organization_rank_age_and_stage(self):
        lyceum = self.persons(organization="Лицей № 1 г. Оренбурга")
        self.assertIn(VALIEVA, lyceum)
        self.assertTrue(all(sportsman.store.persons[f].organization == "Лицей № 1 г. Оренбурга" for f in lyceum))
        self.assertEqual(self.persons(rank="КМС") & {ZHUKOV}, {ZHUKOV})
        no_rank = self.persons(rank="none")
        self.assertTrue(no_rank and all(sportsman.store.persons[f].rank is None for f in no_rank))
        teens = self.persons(age_group="юноши/девушки 14–17 лет")
        self.assertIn(VALIEVA, teens)
        self.assertNotIn(ROSTER_BY_SEQ[19]["fsp_id"], teens)  # 2003 г.р.
        self.assertLessEqual(self.persons(organization="Без организации"), self.persons(stage="none"))
        self.assertTrue(self.persons(stage="УТ"))
        self.assertEqual(self.api.get("/api/v1/persons", params={"age_group": "xx"}).status_code, 422)

    def test_organizations_have_cards_and_counts(self):
        orgs = {o["name"]: o for o in self.api.get("/api/v1/organizations").json()}
        self.assertNotIn("Без организации", orgs)
        ogu = orgs["ОГУ — Оренбургский государственный университет"]
        self.assertEqual((ogu["type"], ogu["municipality"]), ("Вуз", "г. Оренбург"))
        self.assertGreater(ogu["athletes"], 0)
        self.assertEqual(ogu["athletes"], len(self.persons(organization=ogu["name"])))

    def test_antidoping_mark(self):
        url = f"/api/v1/persons/{VALIEVA}/antidoping"
        self.assertEqual(self.api.post(url, json={"course_date": "2099-01-01", "certificate_url": "https://x.ru/c/1"}).status_code, 422)
        self.assertEqual(self.api.post(url, json={"course_date": "2026-01-10", "certificate_url": "ftp://x"}).status_code, 422)
        r = self.api.post(url, json={"course_date": "2026-01-10", "certificate_url": "https://course.rusada.ru/c/1"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["antidoping"]["course_date"], "2026-01-10")

    def test_notification_feed_and_dedup_by_key(self):
        body = {"fsp_ids": [VALIEVA, VALIEVA, "FSP-56-00-99999-0"], "title": "Срок", "key": "rank:2026|d7"}
        self.assertEqual(self.api.post("/api/v1/notifications", json=body).json()["delivered"], [VALIEVA])
        self.assertEqual(self.api.post("/api/v1/notifications", json=body).json()["delivered"], [])
        self.api.post("/api/v1/notifications", json={"fsp_ids": [VALIEVA], "title": "Протокол"})
        feed = self.api.get("/api/v1/notifications", params={"fsp_id": VALIEVA}).json()
        self.assertEqual((feed["unread"], feed["items"][0]["title"]), (2, "Протокол"))
        self.api.post("/api/v1/notifications/read", params={"fsp_id": VALIEVA})
        self.assertEqual(self.api.get("/api/v1/notifications", params={"fsp_id": VALIEVA}).json()["unread"], 0)
        self.assertGreaterEqual(self.api.get("/api/v1/notifications", params={"fsp_id": ADMIN}).json()["unread"], 1)  # сид

    def test_read_marks_only_shown_notifications(self):
        for title in ("A", "B", "C"):
            self.api.post("/api/v1/notifications", json={"fsp_ids": [VALIEVA], "title": title})
        items = self.api.get("/api/v1/notifications", params={"fsp_id": VALIEVA}).json()["items"]
        shown = [n["id"] for n in items if n["title"] == "B"]
        r = self.api.post("/api/v1/notifications/read", params={"fsp_id": VALIEVA}, json={"ids": shown})
        self.assertEqual(r.json()["unread"], 2)
        self.assertEqual(self.api.post("/api/v1/notifications/read", params={"fsp_id": VALIEVA}).json()["unread"], 0)

    def test_organization_filters(self):
        orsk = self.api.get("/api/v1/organizations", params={"municipality": "г. Орск"}).json()
        self.assertTrue(orsk and all(o["municipality"] == "г. Орск" for o in orsk))
        rob = self.api.get("/api/v1/organizations", params={"discipline": "ROB"}).json()
        self.assertTrue(rob and all("ROB" in o["disciplines"] for o in rob))
        self.assertEqual([o["type"] for o in self.api.get("/api/v1/organizations", params={"q": "вуз оренб"}).json()], ["Вуз"])

    def test_headcount_has_stage_cut(self):
        cuts = {c["key"]: c for c in self.api.get("/api/v1/reports/headcount").json()["cuts"]}
        self.assertIn("stage", cuts)
        self.assertTrue(any(row["label"] == "этап не указан" for row in cuts["stage"]["rows"]))


class DeadlinesTest(unittest.TestCase):
    def test_report_deadlines_roll_over_to_next_year(self):
        out = report_deadlines(date(2026, 10, 27))
        self.assertEqual(out[0]["date"], "2026-11-01")
        self.assertEqual({d["date"] for d in out if d["title"].startswith("Ходатайства")}, {"2027-02-01"})
        self.assertTrue(all(0 <= d["days_left"] <= 366 for d in out))
        self.assertEqual(report_deadlines(date(2027, 2, 1))[0]["days_left"], 0)

    def test_remind_buckets(self):
        self.assertEqual([notify.remind_bucket(d) for d in (-3, 0, 1, 5, 7, 20, 30, 31)],
                         ["expired", "d1", "d1", "d7", "d7", "d30", "d30", ""])

    def test_remind_pass_records_with_bucket_key(self):
        sent = []

        async def record(ids, title, text, link, key):
            sent.append((ids, key))
            return ids

        async def deadlines():
            return [("FSP-1", "rank:2026-11-08", "Срок", "", 12, "/cabinet"), ("FSP-2", "judge:x", "Срок", "", 90, "/cabinet"),
                    ("FSP-3", "registration:5", "Приём", "", 3, "/c"), ("FSP-4", "registration:5", "Приём", "", 3, "/c")]

        old, notify._record = notify._record, record
        try:
            asyncio.run(notify.remind_pass(deadlines))
        finally:
            notify._record = old
        self.assertEqual(sent, [(["FSP-1"], "rank:2026-11-08|d30"), (["FSP-3", "FSP-4"], "registration:5|d7")])  # общий срок — одной рассылкой


if __name__ == "__main__":
    unittest.main()

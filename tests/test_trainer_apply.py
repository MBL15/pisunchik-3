"""Заявка за учеников: статус документов, блокировки в списке и напоминание про сертификат РУСАДА."""
import asyncio
import unittest
from unittest import mock

from gateway_service import apply

COMP = {"id": 7, "discipline": "ALG", "age_group": "юноши/девушки 14–17 лет", "start_date": "2026-11-20", "title": "Кубок ALG"}


def athlete(fid="FSP-1", birth="2010-05-01", **kw):
    base = {"fsp_id": fid, "full_name": f"Спортсмен {fid}", "birth_date": birth, "is_minor": True, "is_sanctioned": False,
            "antidoping": None, "pd_consent_at": "2026-01-10T09:00:00", "guardian": {"consent_at": "2026-01-10T09:00:00"}, "rank": None}
    return {**base, **kw}


def link(a, discipline="ALG", status="ACTIVE"):
    return {"athlete_fsp_id": a["fsp_id"], "athlete": a, "discipline": discipline, "status": status}


def tones(a):
    return {d["key"]: d["tone"] for d in apply.docs_status(a)}


class DocsStatusTest(unittest.TestCase):
    def test_rusada_missing_is_a_warning_not_a_block(self):
        self.assertEqual(tones(athlete())["rusada"], "warn")
        done = athlete(antidoping={"course_date": "2026-02-01", "certificate_url": "https://course.rusada.ru/c/1"})
        self.assertEqual(tones(done)["rusada"], "ok")

    def test_minor_needs_guardian_consent(self):
        self.assertEqual(tones(athlete(guardian=None))["guardian"], "bad")
        self.assertNotIn("guardian", tones(athlete(is_minor=False, guardian=None)))

    def test_rank_and_sanction_are_shown(self):
        expired = {"short": "II р.", "state": "expired", "days_left": -8}
        self.assertEqual(tones(athlete(rank=expired))["rank"], "bad")
        self.assertEqual(tones(athlete(rank={"short": "I р.", "state": "warning", "days_left": 55}))["rank"], "warn")
        self.assertEqual(tones(athlete(rank={"short": "КМС", "state": "ok", "days_left": 300}))["rank"], "ok")
        self.assertEqual(tones(athlete(is_sanctioned=True))["sanction"], "bad")


class RosterTest(unittest.TestCase):
    def rows(self, links, apps=()):
        (item,) = apply.roster([COMP], links, list(apps))
        return {r["athlete_fsp_id"]: r for r in item["rows"]}, item

    def test_only_active_links_of_the_competition_discipline(self):
        ok, other, ended = athlete("A"), athlete("B"), athlete("C")
        rows, item = self.rows([link(ok), link(other, "SEC"), link(ended, status="ENDED")])
        self.assertEqual(list(rows), ["A"])
        self.assertEqual(item["free"], 1)

    def test_blocked_reasons_and_order(self):
        old = athlete("OLD", birth="2000-01-01")  # 20 лет к старту — вне группы 14–17
        sanctioned = athlete("SAN", is_sanctioned=True)
        applied = athlete("APP")
        free = athlete("FREE")
        existing = {"competition_id": 7, "athlete_fsp_id": "APP", "status": "PENDING", "status_label": "На рассмотрении"}
        rows, item = self.rows([link(old), link(sanctioned), link(applied), link(free)], [existing])
        self.assertIsNone(rows["FREE"]["blocked"])
        self.assertIn("возраст", rows["OLD"]["blocked"])
        self.assertIn("санкция", rows["SAN"]["blocked"])
        self.assertIn("уже подана", rows["APP"]["blocked"])
        self.assertEqual(item["rows"][0]["athlete_fsp_id"], "FREE")  # свободные — выше заблокированных
        self.assertEqual(item["free"], 1)

    def test_rejected_application_does_not_block(self):
        rejected = {"competition_id": 7, "athlete_fsp_id": "A", "status": "REJECTED", "status_label": "Отклонена"}
        rows, _ = self.rows([link(athlete("A"))], [rejected])
        self.assertIsNone(rows["A"]["blocked"])


class AnnounceTest(unittest.TestCase):
    APP = {"athlete_fsp_id": "FSP-1", "competition_title": "Кубок ALG"}

    def run_announce(self, person, submitter="TRAINER"):
        sent = []

        async def fake_notify(fid, title, text, link=None, *a, **kw):
            sent.append((fid, title, link))

        client = mock.Mock(get=mock.AsyncMock(return_value=person))
        with mock.patch.object(apply.notify, "notify", fake_notify), mock.patch.dict(apply.clients, {"sportsman": client}):
            asyncio.run(apply.announce(self.APP, submitter))
        return sent

    def test_trainer_application_without_rusada_sends_two_notices(self):
        sent = self.run_announce({"antidoping": None})
        self.assertEqual([t for _, t, _ in sent], ["Тренер подал заявку за вас", "Приложите сертификат РУСАДА"])
        self.assertEqual(sent[1][2], "/cabinet#antidoping")

    def test_with_rusada_only_the_application_notice(self):
        sent = self.run_announce({"antidoping": {"course_date": "2026-02-01"}})
        self.assertEqual([t for _, t, _ in sent], ["Тренер подал заявку за вас"])

    def test_own_application_is_silent(self):
        self.assertEqual(self.run_announce({"antidoping": None}, submitter="FSP-1"), [])


if __name__ == "__main__":
    unittest.main()

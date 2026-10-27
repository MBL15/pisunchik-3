"""Правки по код-ревью: возраст на дату старта, служебный токен, безопасный live-GET, напоминания тренеру и федерации."""
import asyncio
import unittest
from datetime import date, timedelta
from unittest import mock

from fastapi.testclient import TestClient

from competition_service import main as competition
from gateway_service.routes import notifications
from gateway_service.web import clients
from shared import config, domain
from shared.http import ServiceClient

ALG6_OPEN, ALG5_LIVE = 6, 5
ATHLETE = "FSP-56-25-00008-3"
TRAINER = "FSP-56-25-00002-9"
ADMIN = "FSP-56-25-00001-6"


def years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 февраля
        return d.replace(year=d.year - years, day=28)


class AgeOnStartDateTest(unittest.TestCase):
    def test_age_is_counted_on_the_start_date(self):
        start = date(2026, 6, 1)
        turns_14_on_start = date(2012, 6, 1)
        self.assertEqual(domain.age_on(turns_14_on_start, date(2026, 5, 31)), 13)
        self.assertEqual(domain.age_group_fit(turns_14_on_start, "юноши/девушки 14–17 лет", start), (14, True))
        turns_18_on_start = date(2008, 6, 1)
        self.assertEqual(domain.age_group_fit(turns_18_on_start, "юноши/девушки 14–17 лет", start), (18, False))
        self.assertEqual(domain.age_group_fit("2008-06-02", "юноши/девушки 14–17 лет", "2026-06-01"), (17, True))


class _Sportsman:
    """Stub реестра: один спортсмен с заданной датой рождения."""

    def __init__(self, birth: date):
        self.birth = birth

    async def get(self, path, **params):
        if path.endswith("/links"):
            return []
        return {"fsp_id": ATHLETE, "full_name": "Абрамов Кирилл", "roles": ["athlete"], "disciplines": ["ALG"],
                "is_sanctioned": False, "age": 99, "birth_date": self.birth.isoformat()}

    async def aclose(self):
        pass


class ApplicationAgeTest(unittest.TestCase):
    def setUp(self):
        competition.store.__init__()
        self.ctx = TestClient(competition.app)
        self.api = self.ctx.__enter__()
        c = competition.store.competitions[ALG6_OPEN]
        c.age_group, c.start_date = "юноши/девушки 14–17 лет", config.today() + timedelta(days=30)
        c.end_date = c.start_date
        self.start = c.start_date

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def apply(self, birth: date):
        competition.clients["sportsman"] = _Sportsman(birth)
        return self.api.post("/api/v1/applications", json={"competition_id": ALG6_OPEN, "athlete_fsp_id": ATHLETE,
                                                           "submitted_by": ATHLETE})

    def test_athlete_who_turns_14_by_the_start_is_admitted_though_13_today(self):
        birth = years_before(self.start, 14)
        self.assertEqual(domain.age_on(birth, config.today()), 13)
        self.assertEqual(self.apply(birth).status_code, 201)

    def test_athlete_who_turns_18_by_the_start_is_rejected_though_17_today(self):
        birth = years_before(self.start, 18)
        self.assertEqual(domain.age_on(birth, config.today()), 17)
        r = self.apply(birth)
        self.assertEqual(r.status_code, 422)
        self.assertIn("на дату старта (18)", r.json()["detail"])


class LiveStreamTest(unittest.TestCase):
    def setUp(self):
        competition.store.__init__()
        self.ctx = TestClient(competition.app)
        self.api = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)

    def test_reading_the_protocol_does_not_advance_the_stream(self):
        first = self.api.get(f"/api/v1/competitions/{ALG5_LIVE}/protocol").json()
        competition.store.last_tick[ALG5_LIVE] = 0  # интервал давно прошёл: прежний GET-«тик» сдвинул бы поток
        again = self.api.get(f"/api/v1/competitions/{ALG5_LIVE}/protocol").json()
        self.assertEqual(first["live_remaining"], again["live_remaining"])

    def test_explicit_tick_and_background_driver_advance_it(self):
        before = len(competition.store.live_queue[ALG5_LIVE])
        competition.store.last_tick[ALG5_LIVE] = 0
        self.api.post(f"/api/v1/competitions/{ALG5_LIVE}/live/tick")
        self.assertLess(len(competition.store.live_queue[ALG5_LIVE]), before)

        competition.store.last_tick[ALG5_LIVE] = 0
        left = len(competition.store.live_queue[ALG5_LIVE])
        with mock.patch.object(competition, "LIVE_TICK_MIN_INTERVAL", 0.01):  # очередь велика: до пересева не доходит
            async def run():
                task = asyncio.create_task(competition._live_driver())
                await asyncio.sleep(0.08)
                task.cancel()
            asyncio.run(run())
        self.assertLess(len(competition.store.live_queue.get(ALG5_LIVE, [])), left)


class ServiceTokenTest(unittest.TestCase):
    def setUp(self):
        self.api = TestClient(competition.app)

    def test_without_a_configured_token_services_stay_open(self):
        self.assertEqual(self.api.get("/api/v1/audit").status_code, 200)

    def test_configured_token_is_required_except_for_health(self):
        with mock.patch.object(config, "SERVICE_TOKEN", "s3cret"):
            self.assertEqual(self.api.get("/api/v1/audit").status_code, 401)
            self.assertEqual(self.api.get("/api/v1/audit", headers={"X-FSP-Service-Token": "wrong"}).status_code, 401)
            self.assertEqual(self.api.get("/api/v1/audit", headers={"X-FSP-Service-Token": "s3cret"}).status_code, 200)
            self.assertEqual(self.api.get("/health").status_code, 200)

    def test_service_client_sends_the_token(self):
        with mock.patch.object(config, "SERVICE_TOKEN", "s3cret"):
            client = ServiceClient("x", "http://x", caller="gateway")
        self.assertEqual(client._client.headers["X-FSP-Service-Token"], "s3cret")
        asyncio.run(client.aclose())


class _Reg:
    """Stub реестра для напоминаний: спортсмен с разрядом, истекающим через 10 дней, тренер по активной привязке, админ."""

    async def get(self, path, **params):
        rank = {"title": "I разряд", "expires_at": (config.today() + timedelta(days=10)).isoformat(), "days_left": 10}
        if path == "/api/v1/alerts/ranks":
            return [{"fsp_id": ATHLETE, "full_name": "Абрамов Кирилл", "rank": rank}]
        if path == f"/api/v1/athletes/{ATHLETE}/links":
            return [{"trainer_fsp_id": TRAINER, "status": "ACTIVE"}, {"trainer_fsp_id": "FSP-56-25-00004-1", "status": "ENDED"},
                    {"trainer_fsp_id": ATHLETE, "status": "ACTIVE"}]
        if path == "/api/v1/persons" and params.get("role") == "admin":
            return [{"fsp_id": ADMIN}]
        return []


class _Comp:
    async def get(self, path, **params):
        return []


class RankReminderAudienceTest(unittest.TestCase):
    def test_athlete_trainer_and_federation_are_warned(self):
        with mock.patch.dict(clients, {"sportsman": _Reg(), "competition": _Comp()}):
            out = asyncio.run(notifications.deadline_reminders())
        rank_items = [r for r in out if "азряд" in r[2]]
        by_person = {r[0]: r for r in rank_items}
        self.assertEqual(set(by_person), {ATHLETE, TRAINER, ADMIN})  # завершённая привязка и самопривязка не в счёт
        self.assertIn("Абрамов Кирилл", by_person[TRAINER][3])
        self.assertTrue(by_person[TRAINER][1].startswith(f"rank:{ATHLETE}:"))
        self.assertIn("1 чел.", by_person[ADMIN][3])
        self.assertTrue(by_person[ADMIN][1].startswith("rank-digest:"))


if __name__ == "__main__":
    unittest.main()

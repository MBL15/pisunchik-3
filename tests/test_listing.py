"""Большие списки: поиск по словам, фасеты без собственного фильтра, сортировка и страницы, массовая модерация, журнал."""
import unittest
from datetime import date

from fastapi import Request
from fastapi.testclient import TestClient

from competition_service import main as competition
from gateway_service import listing_ui
from gateway_service.routes import registry
from shared import config, demo_scale, listing
from sportsman_service import main as sportsman


class ListingHelpersTest(unittest.TestCase):
    def test_word_search_ignores_case_and_yo(self):
        words = listing.tokens("  фЁдоров  оренб ")
        self.assertTrue(listing.matches(words, "Федоров Иван", "г. Оренбург"))
        self.assertFalse(listing.matches(words, "Федоров Иван", "г. Орск"))
        self.assertTrue(listing.matches([], "что угодно"))

    def test_page_clamps_limit_and_reports_total(self):
        res = listing.page(list(range(1234)), 1200, 10_000)  # смещение не на границе страницы → последняя страница
        self.assertEqual((res["total"], res["limit"], res["offset"], len(res["items"])), (1234, listing.MAX_LIMIT, 1000, 234))
        far = listing.page(list(range(120)), 5000, 50)  # номер страницы за концом (сузили фильтр)
        self.assertEqual((far["offset"], far["items"][0], len(far["items"])), (100, 100, 20))
        self.assertEqual(listing.page([], 300, 50)["offset"], 0)

    def test_audit_search(self):
        rows = [{"at": "2026-10-03T10:00:00", "actor": "B", "action": "Регистрация", "object": "FSP-1"},  # новые сверху
                {"at": "2026-10-01T10:00:00", "actor": "A", "action": "Модерация заявки", "object": "#5 Иванов"}]
        self.assertEqual(len(listing.search_audit(rows, q="иванов модерация")), 1)
        self.assertEqual(len(listing.search_audit(rows, actor="B")), 1)
        self.assertEqual(len(listing.search_audit(rows, date_from=date(2026, 10, 2))), 1)
        oldest_first = listing.audit_page(rows, order="asc", limit=1)
        self.assertEqual((oldest_first["total"], oldest_first["items"][0]["actor"]), (2, "A"))

    def test_scale_generator_is_deterministic(self):
        a, b = demo_scale.generate(200), demo_scale.generate(200)
        self.assertEqual(a, b)
        self.assertEqual(len({p["fsp_id"] for p in a}), 200)
        self.assertEqual(demo_scale.generate(0), [])


def _request(query: str, path: str = "/registry"):
    return Request({"type": "http", "method": "GET", "path": path, "query_string": query.encode(), "headers": []})


class ListingUiTest(unittest.TestCase):
    def test_chips_skip_defaults_and_reset(self):
        req = _request("status=PENDING&q=%D0%B8%D0%B2%D0%B0%D0%BD&page=3")
        items, reset = listing_ui.chips(req, {"q": "иван", "status": "PENDING", "by": ""}, {"q": lambda v: f"«{v}»"},
                                        defaults={"status": "all"})
        self.assertEqual([label for label, _ in items], ["«иван»", "PENDING"])
        self.assertEqual(items[1][1], "/registry?status=all&q=%D0%B8%D0%B2%D0%B0%D0%BD")  # снятие статуса → «все», страница сброшена
        self.assertEqual(reset, "/registry?status=all")

    def test_registry_tabs_drop_filters_they_cannot_show(self):
        tab, filters = registry._registry_args(_request("tab=trainer&rank=%D0%9A%D0%9C%D0%A1&discipline=ALG"))
        self.assertEqual((tab, "rank" in filters, filters["discipline"]), ("trainer", False, "ALG"))
        links = registry._tab_links({"rank": "КМС", "stage": "УТ", "discipline": "ALG", "organization": "ОГУ"})
        self.assertIn("rank=", links["athlete"])
        self.assertNotIn("rank=", links["trainer"])
        self.assertNotIn("organization=", links["org"])
        self.assertIn("discipline=ALG", links["judge"])


class ScaledRegistryTest(unittest.TestCase):
    def setUp(self):
        self.old = config.DEMO_SCALE
        config.DEMO_SCALE = 1500
        sportsman.store.__init__()
        self.ctx = TestClient(sportsman.app)
        self.api = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        config.DEMO_SCALE = self.old

    def get(self, **params):
        r = self.api.get("/api/v1/registry/persons", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_pages_cover_everything_once(self):
        first = self.get(role="athlete", limit=500)
        self.assertGreater(first["total"], 1000)
        seen = [p["fsp_id"] for off in range(0, first["total"], 500)
                for p in self.get(role="athlete", limit=500, offset=off)["items"]]
        self.assertEqual(len(seen), first["total"])
        self.assertEqual(len(set(seen)), first["total"])

    def test_facets_ignore_their_own_filter(self):
        everyone = self.get(role="athlete")
        alg = self.get(role="athlete", discipline="ALG")
        self.assertEqual(alg["facets"]["discipline"], everyone["facets"]["discipline"])  # можно переключиться на любую
        self.assertEqual(alg["total"], everyone["facets"]["discipline"]["ALG"])
        self.assertEqual(sum(alg["facets"]["stage"].values()), alg["total"])

    def test_sorting(self):
        by_age = self.get(role="athlete", sort="age", limit=200)["items"]
        self.assertEqual([p["age"] for p in by_age], sorted(p["age"] for p in by_age))
        ranks = self.get(role="athlete", sort="rank", order="desc", limit=5)["items"]
        self.assertTrue(all(p["rank"] for p in ranks))
        self.assertEqual(self.api.get("/api/v1/registry/persons", params={"sort": "bogus"}).status_code, 422)

    def test_word_search_spans_fields(self):
        res = self.get(role="athlete", q="орск it-куб")
        self.assertTrue(res["total"])
        self.assertTrue(all(p["municipality"] == "г. Орск" or "IT-куб" in p["organization"] for p in res["items"]))


class ApplicationsModerationTest(unittest.TestCase):
    def setUp(self):
        self.old = config.DEMO_SCALE
        config.DEMO_SCALE = 1500
        competition.store.__init__()
        self.ctx = TestClient(competition.app)
        self.api = self.ctx.__enter__()

    def tearDown(self):
        self.ctx.__exit__(None, None, None)
        config.DEMO_SCALE = self.old

    def test_search_facets_and_batch_decision(self):
        res = self.api.get("/api/v1/applications/search", params={"status": "PENDING", "limit": 100}).json()
        self.assertGreater(res["total"], 100)
        self.assertIn("APPROVED", res["facets"]["status"])  # фасет статуса не сужен своим фильтром
        ids = [a["id"] for a in res["items"][:30]]
        r = self.api.post("/api/v1/applications/decisions", json={"ids": ids + [ids[0], 10**6], "status": "APPROVED", "actor": "t"})
        body = r.json()
        self.assertEqual((len(body["done"]), len(body["failed"])), (30, 1))
        again = self.api.post("/api/v1/applications/decisions", json={"ids": ids[:3], "status": "REJECTED", "actor": "t"}).json()
        self.assertEqual(len(again["failed"]), 3)
        after = self.api.get("/api/v1/applications/search", params={"status": "PENDING"}).json()
        self.assertEqual(after["total"], res["total"] - 30)
        logged = self.api.get("/api/v1/audit", params={"q": "пакетно", "limit": 1000}).json()
        self.assertEqual(len(logged), 30)

    def test_search_by_competition_code_and_sort(self):
        res = self.api.get("/api/v1/applications/search", params={"q": "орб alg", "sort": "athlete", "order": "asc"}).json()
        names = [a["athlete_name"].lower() for a in res["items"]]
        self.assertEqual(names, sorted(names))
        self.assertTrue(all(a["discipline"] == "ALG" for a in res["items"]))


if __name__ == "__main__":
    unittest.main()

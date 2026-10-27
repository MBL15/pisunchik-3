"""Инфографика кабинета министерства: геометрия графиков и сборка блоков из отчётов сервисов."""
import unittest

from fastapi.testclient import TestClient

from gateway_service import infographic as ig
from sportsman_service import main as sportsman


class ScaleTests(unittest.TestCase):
    def test_scale_has_at_most_four_steps_and_covers_the_maximum(self):
        for v in (0, 1, 3, 4, 7, 23, 99, 101, 480, 1234):
            top, step = ig._scale(v)
            self.assertGreaterEqual(top, v)
            self.assertLessEqual(top // step, 4)
            self.assertEqual(top % step, 0)

    def test_empty_chart_still_has_a_scale(self):
        self.assertEqual(ig._scale(0), (4, 1))


class ComboChartTests(unittest.TestCase):
    def test_bars_stay_inside_the_plot_and_are_proportional(self):
        c = ig.combo_chart([2023, 2024, 2025], [10, 20, 0], [5, 8, 0])
        base = c["base"]
        self.assertEqual([b["value"] for b in c["bars"]], [10, 20, 0])
        for b in c["bars"]:
            self.assertAlmostEqual(b["y"] + b["h"], base, places=1)
            self.assertGreaterEqual(b["y"], ig.PAD_T - 0.1)
        self.assertAlmostEqual(c["bars"][1]["h"], 2 * c["bars"][0]["h"], places=1)
        self.assertEqual(c["bars"][2]["h"], 0)
        self.assertEqual(len(c["dots"]), 3)
        self.assertEqual(c["points"].count(","), 3)
        self.assertIn("2024: 20", c["aria"])

    def test_without_line_there_are_no_dots(self):
        c = ig.combo_chart([2024, 2025], [3, 4])
        self.assertEqual((c["dots"], c["points"]), ([], ""))

    def test_all_zero_values_do_not_divide_by_zero(self):
        c = ig.combo_chart([2024, 2025], [0, 0], [0, 0])
        self.assertTrue(all(b["h"] == 0 for b in c["bars"]))


class DonutAndBarsTests(unittest.TestCase):
    def test_donut_arcs_fill_the_circle_and_skip_zero_rows(self):
        d = ig.donut([("A", 3), ("B", 1), ("C", 0)])
        self.assertEqual([s["label"] for s in d["segments"]], ["A", "B"])
        self.assertAlmostEqual(sum(s["dash"] for s in d["segments"]), d["c"], places=1)
        self.assertEqual([s["pct"] for s in d["segments"]], [75, 25])
        self.assertEqual(d["segments"][0]["offset"], 0)
        self.assertLess(d["segments"][1]["offset"], 0)

    def test_donut_without_data_is_none(self):
        self.assertIsNone(ig.donut([("A", 0)]))
        self.assertIsNone(ig.donut([]))

    def test_hbars_width_is_relative_to_the_maximum_and_limit_folds_the_rest(self):
        h = ig.hbars([("a", 8), ("b", 4), ("c", 2), ("d", 0)], limit=2)
        self.assertEqual([(r["label"], r["width"], r["share"]) for r in h["rows"]], [("a", 100, 57), ("b", 50, 29)])
        self.assertEqual((h["total"], h["rest"]), (14, 2))
        self.assertIsNone(ig.hbars([("a", 0)]))

    def test_growth_is_relative_to_the_previous_year(self):
        g = ig.growth([2023, 2024, 2025], [10, 15, 15])
        self.assertEqual([(x["delta"], x["pct"]) for x in g], [(None, None), (5, 50.0), (0, 0.0)])
        self.assertEqual(ig.growth([1, 2], [0, 5])[1]["pct"], None)


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sportsman.store.__init__()
        with TestClient(sportsman.app) as api:  # демо-реестр засевается при старте приложения
            cls.report = api.get("/api/v1/reports/headcount", params={"years": 6}).json()
        cls.stats = {"athletes": 40, "with_rank": 10}

    def test_build_from_the_real_headcount_report(self):
        out = ig.build(self.report, self.stats, [])
        total = self.report["total"]
        self.assertEqual([b["value"] for b in out["athletes"]["bars"]], total["counts"])
        self.assertEqual(len(out["growth"]), 6)
        self.assertEqual(out["period"]["delta"], total["delta"])
        self.assertEqual(out["period"]["joined"], sum(total["joined"]))
        self.assertTrue(out["disciplines"] and out["municipalities"] and out["age"] and out["gender"])
        self.assertEqual(out["ranked"]["pct"], 25)
        self.assertNotIn("participants", out)

    def test_competition_blocks_go_by_year_when_two_years_have_a_calendar(self):
        years = [{"year": 2025, "items": [], "kpi": {"participants": 12, "held": 3, "total": 4}},
                 {"year": 2026, "items": [], "kpi": {"participants": 20, "held": 5, "total": 6}}]
        out = ig.build(None, None, years)
        self.assertEqual(out["comp_period"]["mode"], "years")
        self.assertEqual([b["value"] for b in out["participants"]["bars"]], [12, 20])
        self.assertEqual([d["value"] for d in out["competitions"]["dots"]], [4, 6])
        self.assertNotIn("athletes", out)

    def test_competition_blocks_go_by_month_when_only_one_year_has_a_calendar(self):
        items = [{"start_date": "2026-03-10", "status": "FINISHED", "participants": 7},
                 {"start_date": "2026-03-20", "status": "FINISHED", "participants": 5},
                 {"start_date": "2026-11-05", "status": "REGISTRATION", "participants": 0}]
        years = [{"year": 2025, "items": [], "kpi": {"participants": 0, "held": 0, "total": 0}},
                 {"year": 2026, "items": items, "kpi": {"participants": 12, "held": 2, "total": 3}}]
        out = ig.build(None, None, years)
        self.assertEqual(out["comp_period"], {"mode": "months", "year": 2026})
        self.assertEqual(len(out["participants"]["bars"]), 12)
        self.assertEqual(out["participants"]["bars"][2]["value"], 12)
        self.assertEqual([b["value"] for b in out["competitions"]["bars"]][2], 2)
        self.assertEqual([d["value"] for d in out["competitions"]["dots"]][10], 1)

    def test_calendar_without_competitions_gives_no_competition_blocks(self):
        out = ig.build(None, None, [{"year": 2026, "items": [], "kpi": {"participants": 0, "held": 0, "total": 0}}])
        self.assertEqual(out, {})

    def test_unavailable_sources_give_an_empty_result(self):
        self.assertEqual(ig.build(None, None, []), {})
        self.assertNotIn("ranked", ig.build(None, {"athletes": 0, "with_rank": 0}, []))


if __name__ == "__main__":
    unittest.main()

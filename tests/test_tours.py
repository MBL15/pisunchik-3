"""Интерактивные туры: структура шагов и привязка к страницам."""
import json
import unittest

from gateway_service import tours

TEMPLATES = ["landing.html", "calendar.html", "rating.html", "register.html", "cabinet_athlete.html", "cabinet_trainer.html",
             "cabinet_judge.html", "cabinet_admin.html", "teams.html", "team.html", "report_headcount.html", "protocol.html",
             "registry.html", "applications.html", "audit.html"]


def user(role="admin", roles=("admin", "judge")):
    return {"role": role, "person": {"roles": list(roles), "fsp_id": "FSP-56-25-00001-6"}}


class ToursTest(unittest.TestCase):
    def test_every_page_has_a_well_formed_tour(self):
        ids = []
        for t in TEMPLATES:
            tour = tours.for_page(t, user(), can_pick=False)
            self.assertIsNotNone(tour, t)
            ids.append(tour["id"])
            self.assertGreaterEqual(len(tour["steps"]), 3, t)
            for s in tour["steps"]:
                self.assertTrue(s["sel"] and s["title"] and s["text"], (t, s))
            self.assertEqual(tour["steps"][-1]["sel"], "[data-tour-start]")  # последним всегда напоминание про кнопку «?»
        self.assertEqual(len(ids), len(set(ids)))

    def test_pages_without_a_tour_and_json_roundtrip(self):
        self.assertIsNone(tours.for_page("error.html", None))
        tour = tours.for_page("cabinet_admin.html", user())
        self.assertEqual(json.loads(json.dumps(tour, ensure_ascii=False)), tour)
        self.assertEqual(tour["next"]["href"], "/rating")

    def test_header_steps_follow_the_visitor(self):
        guest = [s["sel"] for s in tours.for_page("landing.html", None)["steps"]]
        self.assertIn('header a[href="/register"]', guest)
        self.assertNotIn('header a[href="/cabinet"]', guest)
        landing = [s["sel"] for s in tours.for_page("landing.html", user("athlete", ("athlete",)))["steps"]]
        self.assertIn('header a[href="/cabinet"]', landing)
        single = [s["sel"] for s in tours.for_page("cabinet_athlete.html", user("athlete", ("athlete",)))["steps"]]
        self.assertNotIn('header a[href="/cabinet"]', single)  # в самом кабинете ссылка на него не нужна
        self.assertFalse(any("Переключение роли" in s for s in single))
        multi = [s["sel"] for s in tours.for_page("cabinet_athlete.html", user("athlete", ("athlete", "trainer")))["steps"]]
        self.assertTrue(any("Переключение роли" in s for s in multi))

    def test_rating_tour_mentions_team_building_only_for_the_federation(self):
        plain = tours.for_page("rating.html", None, can_pick=False)
        admin = tours.for_page("rating.html", user(), can_pick=True)
        self.assertEqual((plain["id"], admin["id"]), ("rating", "rating-admin"))
        self.assertNotIn("#team-form", [s["sel"] for s in plain["steps"]])
        self.assertIn("#team-form", [s["sel"] for s in admin["steps"]])

    def test_protocol_highlight_step_is_admin_only(self):
        self.assertNotIn("th.no-print", [s["sel"] for s in tours.for_page("protocol.html", None)["steps"]])
        self.assertIn("th.no-print", [s["sel"] for s in tours.for_page("protocol.html", user())["steps"]])


if __name__ == "__main__":
    unittest.main()

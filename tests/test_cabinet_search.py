"""Поиск функции в кабинете: каталог каждой роли ведёт только на существующие разделы и страницы."""
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from gateway_service import cabinet_search
from gateway_service.main import ADMIN_SECTIONS, app
from shared.domain import Role

TEMPLATES = Path(__file__).resolve().parent.parent / "gateway_service" / "templates"
ROLE_TEMPLATE = {Role.ATHLETE: "cabinet_athlete.html", Role.TRAINER: "cabinet_trainer.html", Role.JUDGE: "cabinet_judge.html",
                 Role.ADMIN: "cabinet_admin.html", Role.MINISTRY: "cabinet_ministry.html"}


def source(name: str) -> str:
    return (TEMPLATES / name).read_text(encoding="utf-8")


class CabinetSearchTest(unittest.TestCase):
    def test_every_role_has_a_complete_catalog(self):
        for role in ROLE_TEMPLATE:
            rows = cabinet_search.entries(role, telegram=True)
            self.assertGreaterEqual(len(rows), 10, role)
            self.assertTrue(any(r["top"] for r in rows), f"{role}: нет подсказок до набора текста")
            self.assertEqual(len({(r["title"], r["href"]) for r in rows}), len(rows), f"{role}: повторы")
            for r in rows:
                self.assertTrue(all(r[k] for k in ("title", "hint", "href", "kw", "where")), r)

    def test_cabinet_anchors_exist_in_the_role_template(self):
        for role, name in ROLE_TEMPLATE.items():
            html = source(name) + source("_cabinet_head.html")
            for r in cabinet_search.entries(role, telegram=True):
                url = urlsplit(r["href"])
                if url.path != "/cabinet" or not url.fragment:
                    continue
                self.assertIn(f'id="{url.fragment}"', html, f"{role}: нет якоря #{url.fragment} для «{r['title']}»")

    def test_admin_links_point_to_the_tab_that_holds_the_section(self):
        for r in cabinet_search.entries(Role.ADMIN):
            url = urlsplit(r["href"])
            if url.path != "/cabinet" or not url.fragment:
                continue
            tab = parse_qs(url.query).get("tab", [""])[0]
            self.assertEqual(tab, ADMIN_SECTIONS.get(url.fragment, "overview"), f"«{r['title']}»: раздел #{url.fragment} лежит на другой вкладке")

    def test_other_pages_exist(self):
        paths = [route.path_regex for route in app.routes if hasattr(route, "path_regex")]
        for role in ROLE_TEMPLATE:
            for r in cabinet_search.entries(role):
                path = urlsplit(r["href"]).path
                self.assertTrue(any(rx.match(path) for rx in paths), f"{role}: нет страницы {path} для «{r['title']}»")

    def test_telegram_entry_only_when_the_bot_is_configured(self):
        self.assertFalse(any("Telegram" in r["title"] for r in cabinet_search.entries(Role.ATHLETE)))
        self.assertTrue(any("Telegram" in r["title"] for r in cabinet_search.entries(Role.ATHLETE, telegram=True)))

    def test_role_specific_entries_come_first(self):
        rows = cabinet_search.entries(Role.TRAINER)
        self.assertEqual(rows[0]["href"], "/cabinet#todo")
        self.assertEqual(rows[-1]["title"], "Моя анкета на бирже")

    def test_page_renders_the_catalog_and_the_script_ships_the_finder(self):
        finder = source("_cabinet_finder.html")
        for head in ("_cabinet_head.html", "_cabinet_head_compact.html"):
            self.assertIn("_cabinet_finder.html", source(head))  # поиск есть в обеих шапках кабинета
        self.assertIn("data-finder", finder)
        self.assertIn("finder_entries(user.role", finder)
        js = (TEMPLATES.parent / "static" / "ux.js").read_text(encoding="utf-8")
        self.assertTrue(re.search(r"querySelectorAll\('\[data-finder\]'\)", js))


if __name__ == "__main__":
    unittest.main()

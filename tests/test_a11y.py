"""Специальные возможности: палитра, режимы и панель настроек согласованы между шаблоном, стилями и скриптом."""
import re
import unittest
from pathlib import Path

GATEWAY = Path(__file__).resolve().parent.parent / "gateway_service"


def read(*parts: str) -> str:
    return (GATEWAY.joinpath(*parts)).read_text(encoding="utf-8")


BASE, PANEL = read("templates", "base.html"), read("templates", "_a11y_panel.html")
CSS, JS, TOUR = read("static", "a11y.css"), read("static", "a11y.js"), read("static", "tour.js")
DEFAULTS = dict(re.findall(r"(\w+): '([^']*)'", re.search(r"const DEFAULTS = \{(.*?)\};", JS, re.S).group(1)))


class A11yTest(unittest.TestCase):
    def test_palette_comes_from_variables_with_the_same_defaults(self):
        fallbacks = dict(re.findall(r"(\w+): '(\d+ \d+ \d+)'", re.search(r"fsp: Object\.fromEntries\((.*?)\)\.map", BASE, re.S).group(1)))
        root = dict(re.findall(r"--c-(\w+): (\d+ \d+ \d+);", CSS.split("/* ---------- Размер текста")[0]))
        self.assertEqual(len(fallbacks), 13)
        self.assertEqual(fallbacks, root, "запасные значения в Tailwind-конфиге и :root в a11y.css разошлись")
        self.assertIn("rgb(var(--c-${k}, ${v}) / <alpha-value>)", BASE)

    def test_settings_apply_before_first_paint(self):
        boot, sheet = BASE.index("fsp.a11y"), BASE.index("/static/a11y.css")
        self.assertLess(boot, sheet, "ранний скрипт должен идти раньше стилей, иначе схема мигает")
        for needle in ("/static/a11y.js", 'include "_a11y_panel.html"', "data-a11y-open", 'accesskey="0"'):
            self.assertIn(needle, BASE)

    def test_every_setting_is_used_somewhere(self):
        self.assertGreaterEqual(len(DEFAULTS), 10)
        for key in DEFAULTS:
            if key == "speak":  # только состояние скрипта, на вид страницы не влияет
                continue
            attr = f"data-a11y-{key}"
            self.assertTrue(any(attr in text for text in (CSS, BASE, TOUR)), f"{attr}: ни стили, ни страница его не читают")

    def test_panel_options_match_styles(self):
        choices = re.findall(r'choice\("(\w+)", "([^"]+)"', PANEL)
        self.assertGreaterEqual(len(choices), 10)
        for group, value in choices:
            self.assertIn(group, DEFAULTS, group)
            if value != DEFAULTS[group]:
                self.assertIn(f'data-a11y-{group}="{value}"', CSS, f"{group}={value}: нет правил в a11y.css")
        for group in {g for g, _ in choices}:
            self.assertIn(DEFAULTS[group], {v for g, v in choices if g == group}, f"{group}: нет варианта по умолчанию")
        for key in re.findall(r'toggle\("(\w+)"', PANEL):
            self.assertIn(key, DEFAULTS, key)

    def test_presets_exist_in_the_script(self):
        for name in re.findall(r'\("([a-z-]+)", "[^"]+", "[^"]+"\),?\s*\n?', PANEL.split("Быстрые профили")[1].split("</section>")[0]):
            self.assertIn(name, JS, name)
        presets = re.search(r"const PRESETS = \{(.*?)\n  \};", JS, re.S).group(1)
        for key, value in re.findall(r"(\w+): '([^']*)'", presets):
            self.assertIn(key, DEFAULTS, key)
            self.assertNotEqual(value, "", key)

    def test_no_pseudo_elements_inside_is_lists(self):
        """Псевдоэлемент внутри :is() делает весь селектор недействительным — правило молча не работает."""
        self.assertIsNone(re.search(r":is\([^)]*::", CSS))

    def test_calm_mode_reaches_effects_script_and_live_tables(self):
        self.assertIn("data-a11y-calm", BASE)
        self.assertIn("data-a11y-sr", BASE)
        self.assertIn("Обновить таблицу", BASE)
        self.assertIn("data-a11y-sr", TOUR)


if __name__ == "__main__":
    unittest.main()

# AGENTS.md

- Python interpreter: use `py` (Windows launcher, Python 3.14). Plain `python` resolves to the Microsoft Store stub.
- Run the stack: `py run_all.py` (ports 8000/8001/8002, live-reload; state is in-memory and reseeds on reload).
- Unit tests: `py -m unittest -v`. E2E smoke (stack must be running): `py -X utf8 tests/e2e_smoke.py`.
- Starlette 1.0: use `templates.TemplateResponse(request, name, context)`.
- Jinja macros used inside a child template must be defined inside a `{% block %}`.
- Design tokens: Tailwind `fsp-*` colors in `gateway_service/templates/base.html`, effects in `static/app.css`, shared class tokens/icons in `templates/macros.html`.
- Visual checks: headless Chrome `"/c/Program Files/Google/Chrome/Application/chrome.exe" --headless=new --screenshot=... URL`. In Git Bash set `MSYS_NO_PATHCONV=1` when passing `/paths` as args. Chrome enforces a ~500px minimum window width — test mobile via a 375px `<iframe>` page.
- Demo data lives in `shared/demo_roster.py` (people) and `competition_service/seed.py` (calendar, synthetic logs).
- Project knowledge base (RU): `docs/knowledge-base/` — start at its `README.md`. Keep it in sync when changing domain rules, API, demo data or UI effects.
- Tours: steps live in `gateway_service/tours.py`, engine in `static/tour.js`; state is per-user `localStorage` (`fsp.tour:<id>`) — clear it to see the first-visit offer again. Mobile checks: Chrome's emulation does not change `innerWidth`; use a 375px iframe.
- On Windows the gateway's live-reload can hang after `.py`/`.html` edits; do a full stack restart (see `docs/knowledge-base/08-dev-runbook.md`). E2E mutates in-memory state — restart before demos.

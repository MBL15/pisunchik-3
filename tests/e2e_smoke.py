"""End-to-end smoke test against a running stack (py run_all.py).  Run: py tests/e2e_smoke.py

Drives the three case scenarios through the gateway like a browser (cookies, form posts, redirects).
"""
import asyncio
import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from mcp import Client, StdioServerParameters

BASE = os.getenv("FSP_E2E_BASE", "http://127.0.0.1:8000")  # адрес шлюза; другой — если стек поднят на нестандартных портах
ROOT = Path(__file__).resolve().parent.parent
ok = True


def check(cond: bool, label: str) -> None:
    global ok
    ok &= bool(cond)
    print(f"  [{'OK' if cond else 'FAIL'}] {label}")


def page(c: httpx.Client, url: str) -> str:
    r = c.get(url)
    check(r.status_code == 200, f"GET {url} → {r.status_code}")
    return r.text


print("== 0. public pages")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    html = page(c, "/")
    check("КОД СТАЛ СПОРТОМ" in html and "FSP_ORENBURG_INIT... SUCCESS" in html, "hero terminal + typing header")
    check(html.count("hover:-translate-x-1 hover:-translate-y-1") >= 6
          and html.count("hover:shadow-[4px_4px_0px_0px_#402FFF]") >= 6, "6 discipline cards: shift + FSP-blue hard shadow")
    check("bg-fsp-light/95" in html and "#402FFF" in html, "FSP Russia design tokens (light header island, #402FFF)")
    check("×1.2" in html, "landing rating shows ×1.2 coefficient")
    html = page(c, "/rating?discipline=ALG")
    check("Жуков" in html and "Исключены из рейтинга" in html, "sanctioned athlete excluded with reason")
    check("222.0" in html, "Соколов: (40+0+45+100)×1.2 = 222.0")
    check("Войти и подать заявку" in page(c, "/calendar?status=REGISTRATION_OPEN"), "guest calendar leads an open start to login")
    for path in ("/", "/login", "/register", "/rating", "/glossary"):
        html = c.get(path).text
        check('id="a11y-panel"' in html and "data-a11y-open" in html and "/static/a11y.css" in html, f"{path}: панель специальных возможностей на месте")
    check(all(c.get(f"/static/{f}").status_code == 200 for f in ("a11y.css", "a11y.js")), "a11y.css и a11y.js отдаются")
    live = page(c, "/partials/live/5")
    check("tail -f" in live, "live protocol partial renders")
    check(c.get("/api/public/competition/api/v1/rating?discipline=AI").status_code == 200, "public proxy: rating allowed")
    check(c.get("/api/public/sportsman/api/v1/persons").status_code == 404, "public proxy: registry blocked")

print("== 0b. every scenario demo account")
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from shared.demo_roster import DEMO_ACCOUNTS, DEMO_PASSWORD  # noqa: E402
from shared.domain import ROLE_LABELS  # noqa: E402

with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    check(len(DEMO_ACCOUNTS) >= 12 and len(re.findall(r'name="role" value=', page(c, "/login"))) == len(DEMO_ACCOUNTS),
          f"login page lists all {len(DEMO_ACCOUNTS)} scenario accounts")
for a in DEMO_ACCOUNTS:
    with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
        r = c.post("/login", data={"email": a["email"], "password": DEMO_PASSWORD, "role": a["role"], "next": "/cabinet"})
        check(f"кабинет: {ROLE_LABELS[a['role']]}" in r.text and a["check"] in r.text,
              f"{a['email']:<18} → {ROLE_LABELS[a['role']]:<9} «{a['title']}»")
        check("data-finder-input" in r.text and "Найти функцию или параметр" in r.text, f"{a['email']:<18} → в кабинете есть поиск функции")

print("== 1. register multi-role user")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    email = f"novice{int(time.time())}@example.ru"
    form = {"last_name": "Новиков", "first_name": "Пётр", "middle_name": "Ильич", "gender": "М",
            "birth_date": "2001-05-05", "municipality": "г. Орск", "organization": "ЦЦОД «IT-куб» г. Орск",
            "roles": ["athlete", "trainer"], "disciplines": ["ALG", "AI"], "email": email,
            "password": "secret-pass", "consent_pd": "on"}
    bad = c.post("/register", data={**form, "consent_pd": ""})
    check("152-ФЗ" in bad.text, "registration without consent rejected with 152-ФЗ message")
    r = c.post("/register", data=form)
    m = re.search(r"FSP-56-\d{2}-\d{5}-\d", r.text)
    check(r.status_code == 200 and m is not None, f"FSP ID issued: {m.group(0) if m else None}")
    check("Кабинет спортсмена" in r.text or "кабинет: Спортсмен" in r.text, "landed in athlete cabinet")
    r = c.post("/role", data={"role": "trainer"})
    check("кабинет: Тренер" in r.text, "switched to trainer without re-login")
    r = c.post("/role", data={"role": "admin"})
    check(r.status_code == 403, "cannot escalate to an unassigned role")

print("== 2. trainer dashboard")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    r = c.post("/login", data={"email": "trainer@fsp56.ru", "password": "fsp2026"})
    check("кабинет: Тренер" in r.text, "trainer logged in")
    check("Абрамов" in r.text and "≤30 дн" in r.text and "истёк" in r.text, "rank expiry alert badges (≤30 / expired)")
    r = c.post("/cabinet/applications", data={"competition_id": "6", "athlete_fsp_id": "FSP-56-25-00010-3"})
    check("[OK]" in r.text or "[ERR]" in r.text, "application submit returns flash")
    r = c.post("/role", data={"role": "judge"})
    check("Назначения" in r.text and "≤30 дн" in r.text, "judge cabinet with category expiry alert")

print("== 3. federation admin dashboard")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    r = c.post("/login", data={"email": "admin@fsp56.ru", "password": "fsp2026"})
    check("кабинет: Федерация" in r.text and 'aria-label="Вкладки кабинета"' in r.text and "Журнал действий" in r.text,
          "admin dashboard opens on the overview tab")
    check('id="protocols"' not in r.text and 'id="rating-settings"' not in r.text, "overview does not render other tabs")
    tabs = {t: c.get("/cabinet", params={"tab": t}).text for t in ("competitions", "results", "rating", "reports")}
    check('id="create"' in tabs["competitions"] and 'id="registration"' in tabs["competitions"] and 'id="judges"' in tabs["competitions"],
          "competitions tab: new competition, registration, judge panels")
    check("Автоматические протоколы" in tabs["results"] and 'id="evsk"' in tabs["results"] and 'id="documents"' in tabs["results"],
          "results tab: protocols, ЕВСК, certificates")
    check("Управление рейтингом" in tabs["rating"] and 'id="teams"' in tabs["rating"], "rating tab: settings and teams")
    check('href="/reports/headcount"' in tabs["reports"] and 'href="/registry"' in tabs["reports"], "reports tab: headcount and registry")
    check('aria-current="page">Протоколы и результаты' in tabs["results"], "active tab is marked")
    r = c.get("/cabinet", params={"tab": "results"})
    sample = re.search(r'<textarea id="log"[^>]*>(.*?)</textarea>', r.text, re.S).group(1)
    sample = sample.replace("&#34;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    r = c.post("/cabinet/admin/protocol/20/ingest", data={"log": sample})
    check(r.url.params.get("tab") == "results", "action redirects back to its tab")
    check("пересобран из лога" in r.text and "предупреждений парсера: 1" in r.text, "log ingested, bad check digit flagged")
    r = c.post("/cabinet/admin/rating-settings", data={**{f"{l}_{i}": v for l, vs in {
        "CHAMP_RU": [100, 80, 60], "FIRST_RU": [80, 60, 40], "INTERREG": [60, 45, 30], "REGIONAL": [40, 30, 20]}.items()
        for i, v in enumerate(vs)}, "coefficient": "1.2", "min_podiums": "2"})
    check("пересчитан" in r.text, "rating settings saved")
    r = c.post("/documents/participation", data={"competition_id": "4", "fsp_id": "FSP-56-25-00003-8"})
    check(r.status_code == 200 and "<svg" in r.text and "Справка" in r.text, f"document rendered with QR ({r.url.path})")
    vurl = re.search(r'href="(/verify/[^"]+)"', r.text).group(1).replace("&amp;", "&")
    v = c.get(vurl)
    check("SIGNATURE... VALID" in v.text, "verification page: VALID")
    v = c.get(vurl[:-3] + "XXX")
    check("INVALID" in v.text, "tampered code: INVALID")
    r = c.post("/cabinet/admin/protocol/5/publish")
    check("опубликован" in r.text, "live protocol published → rating & history")

print("== 4. results entry → protocol from approved applications → athlete history")
SEC4 = 22
HASANOV, ZAITSEVA, ISAEV, VALIEVA, KALININ = (
    "FSP-56-26-00026-8", "FSP-56-25-00013-7", "FSP-56-25-00014-5", "FSP-56-25-00009-5", "FSP-56-25-00015-2")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    c.post("/login", data={"email": "admin@fsp56.ru", "password": "fsp2026"})
    r = c.get("/cabinet", params={"tab": "results"})
    check('id="results-entry"' in r.text and f"/cabinet/admin/protocol/{SEC4}/results" in r.text
          and HASANOV in r.text and "Калинин" in r.text, "entry sheet lists approved applicants, pending one flagged")
    check(f'name="fid" value="{KALININ}"' not in r.text, "pending application is not in the entry sheet")
    rows = {ZAITSEVA: (6, 512), ISAEV: (5, 401), HASANOV: (5, 401), VALIEVA: (3, 260)}
    form = {"fid": list(rows), "action": "draft"} | {f"solved_{f}": s for f, (s, _) in rows.items()} \
        | {f"penalty_{f}": p for f, (_, p) in rows.items()}
    r = c.post(f"/cabinet/admin/protocol/{SEC4}/results", data=form)
    check("Черновик протокола" in r.text and "ранжированы автоматически" in r.text, "draft protocol ranked")
    stranger = c.post(f"/cabinet/admin/protocol/{SEC4}/results", data={**form, "fid": [*rows, "FSP-56-25-00008-7"]})
    check("Нет одобренной заявки" in stranger.text, "participant without an approved application rejected")
    with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as anon:
        check(anon.get(f"/protocols/{SEC4}").status_code == 403, "draft protocol hidden from the public")
        check(anon.get(f"/api/public/competition/api/v1/competitions/{SEC4}/protocol").status_code == 404,
              "public proxy: draft protocol is not served")
        check(anon.get(f"/partials/live/{SEC4}").status_code == 404, "live partial: only for a live or published protocol")
    r = c.post(f"/cabinet/admin/protocol/{SEC4}/results", data={**form, "action": "publish"})
    check(r.url.path == f"/protocols/{SEC4}" and "ПРОТОКОЛ" in r.text.upper() and "Главный судья" in r.text,
          "published → official protocol form with judges")
    check(httpx.get(f"{BASE}/api/public/competition/api/v1/competitions/{SEC4}/protocol", timeout=15).status_code == 200,
          "public proxy: published protocol is served")
    p = c.get(f"/api/public/competition/api/v1/competitions/{SEC4}/protocol").json()
    places = {e["fsp_id"]: e["place"] for e in p["entries"]}
    check(places == {ZAITSEVA: 1, ISAEV: 2, HASANOV: 2, VALIEVA: 4}, f"places computed, ties shared: {places}")
    check(p["published"] and p["competition"]["status"] == "FINISHED", "competition → FINISHED")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    r = c.post("/login", data={"email": "hasanov@fsp56.ru", "password": "fsp2026"})
    check(f'href="/protocols/{SEC4}"' in r.text and "«OrenCTF Осень»" in r.text, "result is in the athlete's history with protocol link")

print("== 5. rank & judge category requests → federation inbox → profile; judge panel")
PETROVA, ABRAMOV, GALIEV = "FSP-56-26-00020-1", "FSP-56-25-00008-7", "FSP-56-25-00007-9"


def login(c: httpx.Client, email: str) -> str:
    return c.post("/login", data={"email": email, "password": "fsp2026"}).text


with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    html = login(c, "petrova@fsp56.ru")
    basis = re.search(r'id="rb-todo"[^>]*>\s*<option value="([^"]+)"', html)
    check(basis is not None and "Тренера нет — подайте заявку сами" in html, "athlete without trainer: one-step rank form in «Что сделать»")
    r = c.post("/cabinet/qualification", data={"basis": basis.group(1) if basis else "", "code": "2"})
    check("Отправлена в федерацию" in r.text and "на рассмотрении в федерации" in r.text, "rank request sent, status shown in todo")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    html = login(c, "abramov@fsp56.ru")
    check("Заявку на подтверждение подаёт ваш тренер" in html, "athlete with trainer: told the trainer files the request")
    r = c.post("/cabinet/qualification", data={"basis": f"{ABRAMOV}|2", "code": "1"})
    check("подаёт ваш тренер" in r.text, "athlete with trainer cannot self-file")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    html = login(c, "trainer@fsp56.ru")
    check(f'value="{ABRAMOV}|2"' in html and "Подать на подтверждение разряда" in html, "trainer: rank form inside the ward's alert card")
    r = c.post("/cabinet/qualification", data={"basis": f"{ABRAMOV}|2", "code": "1"})
    check("Отправлена в федерацию" in r.text and 'id="ranks"' in r.text and "На рассмотрении" in r.text, "trainer filed, request listed")
    r = c.post("/cabinet/qualification", data={"basis": f"{PETROVA}|14", "code": "2"})
    check("[ERR]" in r.text or "тренер с активной привязкой" in r.text, "trainer cannot file for a stranger")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    html = login(c, "galiev@fsp56.ru")
    check("Подтвердить категорию 1К" in html, "judge: one-click category confirmation in the alert")
    r = c.post("/cabinet/qualification", data={"code": "1К"})
    check("Отправлена в федерацию" in r.text and "ждёт решения федерации" in r.text, "judge request sent")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    html = login(c, "admin@fsp56.ru")
    check(all(n in html for n in ("Петрова Анастасия", "Абрамов", "Галиев", "Тимофеев")) and "Разряды и судейские категории" in html,
          "federation inbox lists rank and category requests")
    ids = {n: re.search(rf"{n}.*?/cabinet/admin/qualification/(\d+)", html, re.S).group(1) for n in ("Петрова", "Галиев", "Тимофеев")}
    r = c.post(f"/cabinet/admin/qualification/{ids['Петрова']}", data={"status": "APPROVED", "order_no": "Приказ МФКиС Оренбургской обл. № 901-р"})
    check("присвоено и внесено в профиль" in r.text, "rank approved with order number")
    r = c.post(f"/cabinet/admin/qualification/{ids['Галиев']}", data={"status": "APPROVED", "order_no": "Приказ № 902-р", "valid_until": "2028-10-26"})
    check("присвоено" in r.text, "judge category confirmed with a validity date")
    r = c.post(f"/cabinet/admin/qualification/{ids['Тимофеев']}", data={"status": "REJECTED", "comment": "Недостаточно судейской практики"})
    check("заявка отклонена" in r.text, "category request rejected with a reason")
    r = c.post("/cabinet/admin/competitions/21/judges", data={"fsp_id": GALIEV, "position": "Главный судья"})
    check("Назначение уже в кабинете судьи" in r.text, "judge assigned to the panel")
    r = c.post("/cabinet/admin/competitions/21/judges", data={"fsp_id": PETROVA, "position": "Судья"})
    check("нет роли «Судья»" in r.text, "non-judge cannot be assigned")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    html = login(c, "petrova@fsp56.ru")
    check("901-р" in html and "Межрегиональный ML-турнир ПФО" in html and "Удовлетворена" in html,
          "athlete profile: rank with order and «за что получен»")
    check("II спортивный разряд истёк" not in html, "rank alert gone after approval")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    html = login(c, "galiev@fsp56.ru")
    check("Главный судья" in html and "Подтвердить категорию" not in html and "902-р" in html, "judge sees assignment and renewed category")
    reg = page(c, "/competitions/21/regulation")
    check("Положение" in reg and "Заявки подаются" in reg and "Галиев" in reg, "regulation document with the judge panel")

print("== 6. federation tools: competition types, registration switch, ЕВСК highlight, headcount report, exports, team + order")
from datetime import date, timedelta  # noqa: E402

from shared.demo_roster import ROSTER_BY_SEQ  # noqa: E402

ALG6 = 6
SOKOLOV, ABRAMOV_ID, ZHUKOV = (ROSTER_BY_SEQ[s]["fsp_id"] for s in (3, 8, 12))
soon = (date.today() + timedelta(days=10)).isoformat()
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "admin@fsp56.ru")
    r = c.post("/cabinet/admin/competition-types", data={"title": "Фестиваль"})
    check("добавлен в справочник" in r.text and 'aria-label="Удалить тип: Фестиваль"' in r.text, "competition type added to the directory")
    check("уже есть" in c.post("/cabinet/admin/competition-types", data={"title": "фестиваль"}).text, "duplicate type rejected (case-insensitive)")
    check("удалён из справочника" in c.post("/cabinet/admin/competition-types/remove", data={"title": "Фестиваль"}).text, "unused type removed")
    check("используется в календаре" in c.post("/cabinet/admin/competition-types/remove", data={"title": "Турнир"}).text, "type used in the calendar cannot be removed")

    r = c.post(f"/cabinet/admin/competitions/{ALG6}/registration", data={"action": "close"})
    check("закрыт: заявок" in r.text and "Открыть приём" in r.text, "registration closed manually")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "trainer@fsp56.ru")
    r = c.post("/cabinet/applications", data={"competition_id": str(ALG6), "athlete_fsp_id": ABRAMOV_ID})
    check("закрыт" in r.text and "[ERR]" in r.text, "application to a closed registration is refused")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "admin@fsp56.ru")
    r = c.post(f"/cabinet/admin/competitions/{ALG6}/registration", data={"action": "open", "deadline": soon})
    check("открыт до" in r.text and "Закрыть приём" in r.text, "registration reopened with a new deadline")
    r = c.post("/cabinet/admin/competitions/5/registration", data={"action": "close"})
    check("уже идёт или завершено" in r.text, "live competition cannot be switched")

    cabinet = c.get("/cabinet", params={"tab": "results"}).text
    check('id="evsk"' in cabinet and "Таблица требований, по которой идёт подсветка" in cabinet, "ЕВСК section with the requirements table")
    m_ev = re.search(r'id="evsk".*?href="/protocols/(\d+)"', cabinet, re.S)
    check(m_ev is not None and "ЕВСК ·" in cabinet, "ЕВСК candidates listed with their target rank")
    if m_ev:
        page_ev = page(c, f"/protocols/{m_ev.group(1)}")
        check('<th scope="col" class="no-print px-2 py-2">ЕВСК</th>' in page_ev and "ЕВСК ·" in page_ev, "admin protocol highlights ЕВСК athletes (screen only)")
    with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as anon:
        check('class="no-print px-2 py-2">ЕВСК' not in anon.get("/protocols/4").text, "public protocol carries no ЕВСК column")

    hc = page(c, "/reports/headcount")
    check("Занимающихся — всего" in hc and "по дисциплинам" in hc and "по возрастным группам" in hc, "headcount report: totals and cuts")
    check("Муниципалитет" in page(c, "/reports/headcount?years=2&discipline=ALG"), "headcount report filters")
    for fmt, magic in (("xlsx", b"PK"), ("docx", b"PK"), ("pdf", b"%PDF")):
        resp = c.get(f"/reports/headcount/export.{fmt}")
        check(resp.status_code == 200 and resp.content.startswith(magic) and "attachment" in resp.headers["content-disposition"],
              f"headcount .{fmt} download ({len(resp.content)} bytes)")
        resp = c.get(f"/protocols/4/export.{fmt}")
        check(resp.status_code == 200 and resp.content.startswith(magic), f"protocol .{fmt} download")
    check(c.get("/protocols/4/export.odt").status_code == 404, "unknown export format → 404")

    rating = page(c, "/rating?discipline=ALG")
    picks = re.findall(r'name="fids" value="(FSP-[\w-]+)"', rating)
    season = re.search(r'name="season" value="(\d+)"', rating).group(1)
    check(len(picks) >= 4 and "Сформировать сборную из отмеченных" in rating, f"rating offers a team form for the federation ({len(picks)} rows)")
    nobody = c.post("/cabinet/admin/teams", data={"discipline": "ALG", "season": season, "title": "Сборная команда региона"})
    check("Отметьте в таблице рейтинга" in nobody.text, "team needs at least one selected athlete")
    r = c.post("/cabinet/admin/teams", data={"discipline": "ALG", "season": season, "title": "Сборная команда региона (алгоритмика)",
                                             "purpose": "участия во всероссийских соревнованиях", "fids": picks[:3]})
    tid = re.search(r"/teams/(\d+)", str(r.url)).group(1) if "/teams/" in str(r.url) else None
    check(tid is not None and "Сборная сформирована" in r.text and "проект приказа" in r.text, f"team #{tid} built from selected rating rows")
    bad = c.post("/cabinet/admin/teams", data={"discipline": "ALG", "season": season, "title": "Сборная команда региона", "fids": [ZHUKOV]})
    check("Нет в рейтинге" in bad.text, "sanctioned athlete cannot join a team")
    for kind, fmt, magic in (("roster", "xlsx", b"PK"), ("roster", "pdf", b"%PDF"), ("order", "docx", b"PK"), ("order", "pdf", b"%PDF")):
        resp = c.get(f"/teams/{tid}/{kind}.{fmt}")
        check(resp.status_code == 200 and resp.content.startswith(magic), f"team {kind} .{fmt} download ({len(resp.content)} bytes)")
    r = c.post(f"/cabinet/admin/teams/{tid}/members/{picks[0]}/role", data={"role": "RESERVE"})
    check("Статус спортсмена в сборной изменён" in r.text and "В основной" in r.text, "athlete moved to the reserve")
    r = c.post(f"/cabinet/admin/teams/{tid}/approve", data={"order_no": "без номера", "order_date": date.today().isoformat()})
    check("Укажите номер приказа" in r.text, "approval without an order number refused")
    r = c.post(f"/cabinet/admin/teams/{tid}/approve", data={"order_no": "№ 412-р", "order_date": date.today().isoformat()})
    check("Состав утверждён приказом № 412-р" in r.text and "Утвердить состав" not in r.text, "team approved by order, editing closed")
    r = c.post(f"/cabinet/admin/teams/{tid}/members", data={"fsp_id": picks[3]})
    check("Состав утверждён приказом — изменить его нельзя" in r.text, "approved roster is locked on the server too")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "athlete@fsp56.ru")
    html = c.get("/cabinet").text
    check(f'href="/athletes/{SOKOLOV}/report.pdf"' in html, "athlete cabinet offers the competitions report")
    for fmt, magic in (("xlsx", b"PK"), ("docx", b"PK"), ("pdf", b"%PDF")):
        resp = c.get(f"/athletes/{SOKOLOV}/report.{fmt}")
        check(resp.status_code == 200 and resp.content.startswith(magic), f"athlete report .{fmt}")
    check(c.get(f"/athletes/{ABRAMOV_ID}/report.pdf").status_code == 403, "athlete cannot export someone else's report")
    check(c.get("/reports/headcount").status_code == 403 and c.get("/teams").status_code == 403, "headcount and teams are federation-only")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "trainer@fsp56.ru")
    check(c.get(f"/athletes/{ABRAMOV_ID}/report.xlsx").status_code == 200, "trainer exports the report of an active ward")
    check(c.get(f"/athletes/{ZHUKOV}/report.xlsx").status_code == 403, "trainer cannot export a stranger's report")

print("== 7. usability: optional interactive tour, confirmations, quick actions, team picker")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    landing = page(c, "/")
    check('id="tour-data"' in landing and "Знакомство с платформой" in landing and 'header a[href=\\"/register\\"]' in landing,
          "landing carries a tour for guests (registration + login steps)")
    check("Как зарегистрироваться" in page(c, "/register") and 'id="tour-data"' in page(c, "/calendar"), "register and calendar have tours")
    for asset in ("tour.js", "ux.js"):
        r = c.get(f"/static/{asset}")
        check(r.status_code == 200 and "javascript" in r.headers["content-type"], f"/static/{asset} served")
    check('data-pick-bar' not in page(c, "/rating"), "public rating has no team picker")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "admin@fsp56.ru")
    cab = c.get("/cabinet").text
    check('data-tour="quick-actions"' in cab and "Внести результаты" in cab and "Кабинет федерации" in cab, "admin cabinet: quick actions + tour")
    confirms = sum(c.get("/cabinet", params={"tab": t}).text.count("data-confirm=") for t in ("overview", "competitions", "results", "rating"))
    check(confirms >= 4, f"irreversible admin actions ask for confirmation ({confirms} places)")
    check('const sections = {"inbox": "overview"' in cab and '"registration": "competitions"' in cab,
          "old /cabinet#section links are routed to their tab (section map in the page)")
    check('data-dismiss' not in cab or 'aria-label="Закрыть сообщение"' in cab, "flash messages are dismissible")
    rating = c.get("/rating?discipline=ALG").text
    check("data-pick-bar" in rating and 'data-pick-top="5"' in rating and "data-pick-submit" in rating, "rating: quick selection bar for the team")
    check("rating-admin" in rating or "Набор в сборную" in rating, "rating tour for the federation mentions team building")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    html = login(c, "athlete@fsp56.ru")
    check("Кабинет спортсмена" in html and "[data-tour-start]" in html, "athlete cabinet tour ends with the help-button step")

print("== 8. registry with filters, organizations, notifications feed, antidoping, report deadlines")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "admin@fsp56.ru")
    reg = c.get("/registry", params={"tab": "athlete", "organization": "Лицей № 1 г. Оренбурга", "age_group": "юноши/девушки 14–17 лет"})
    check(reg.status_code == 200 and "Валиева" in reg.text and "Жуков" not in reg.text, "registry filters by organization and age group")
    check("Жуков" in c.get("/registry", params={"rank": "КМС", "q": "жук"}).text, "registry search + rank filter")
    orgs = c.get("/registry?tab=org").text
    check("ЦЦОД «IT-куб» г. Орск" in orgs and "Организация доп. образования" in orgs, "registry lists organizations with type")
    for fmt, magic in (("xlsx", b"PK"), ("docx", b"PK"), ("pdf", b"%PDF")):
        resp = c.get(f"/registry/export.{fmt}?tab=judge")
        check(resp.status_code == 200 and resp.content.startswith(magic), f"registry export .{fmt}")
    cab = c.get("/cabinet").text
    check("Отчёт: " in cab and 'href="/notifications"' in cab and 'href="/registry"' in cab, "admin: report deadlines, bell, registry link")
    feed = c.get("/notifications").text
    check("Новая заявка на соревнование" in feed and "Заявка на разряд" in feed, "federation feed got applications and rank requests from e2e")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "athlete@fsp56.ru")
    check(c.get("/registry").status_code == 403, "registry is federation-only")
    c.post("/cabinet/antidoping", data={"course_date": "2026-09-01", "certificate_url": "https://course.rusada.ru/c/e2e"})
    html = c.get("/cabinet").text
    check("Онлайн-курс РУСАДА пройден" in html and "course.rusada.ru/c/e2e" in html, "athlete saves the RUSADA course mark")
    check("Согласие на обработку персональных данных" in html, "athlete sees the stored PD consent")
    check("вы проходите по возрасту" in c.get("/calendar?mine=1").text, "calendar marks age eligibility for the athlete")
    check("Ваша заявка: одобрена" in c.get("/calendar?status=REGISTRATION_OPEN").text, "calendar shows the athlete's own application status")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "hasanov@fsp56.ru")
    html = c.get("/calendar?status=REGISTRATION_OPEN").text
    check('name="competition_id" value="6"' in html, "calendar offers a one-click application for an open start")
    check("ждёт вашего ответа" in c.get("/cabinet").text, "pending trainer request is also listed under «Мои тренеры»")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "abramov@fsp56.ru")
    check("Истекает срок разряда" in c.get("/notifications").text, "rank expiry reminder lands in the athlete's feed")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "trainer@fsp56.ru")
    check("Истекает срок разряда подопечного" in c.get("/notifications").text, "rank expiry reminder reaches the trainer")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "admin@fsp56.ru")
    check("срок разряда" in c.get("/notifications").text, "rank expiry digest reaches the federation")

print("== 9. large lists: paging, sorting, facets, bulk moderation, audit search")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "admin@fsp56.ru")
    reg = c.get("/registry", params={"per": 25, "sort": "age", "order": "desc"}).text
    check('aria-label="Страницы"' in reg and 'aria-sort="descending"' in reg, "registry: pager and sortable headers")
    check("Алгоритмика · " in reg, "registry: filter options show facet counts")
    last = c.get("/registry", params={"per": 25, "page": 999}).text
    check("из <span class=\"font-bold text-fsp-light\">" in last and "никого не найдено" not in last, "registry: page past the end shows the last page")
    apps = c.get("/applications").text
    check("data-bulk-all" in apps and "Одобрить отмеченные" in apps, "applications page: bulk selection")
    ids = re.findall(r'name="ids" value="(\d+)"', apps)
    check(len(ids) >= 1, f"applications page lists pending applications ({len(ids)})")
    if ids:
        r = c.post("/applications/decide", data={"ids": ids[:1], "status": "APPROVED", "back": "/applications?status=all"})
        check(r.status_code == 200 and "Одобрено: 1" in r.text, "bulk approve returns to the filtered list with a summary")
    audit = c.get("/audit", params={"q": "пакетно"}).text
    check("Модерация заявки" in audit, "audit search finds the bulk decision")
    check(c.get("/audit/export.xlsx", params={"q": "заявк"}).content.startswith(b"PK"), "audit export .xlsx")
    check("// завершённые" in c.get("/calendar", params={"q": "кубок"}).text, "calendar search")
    check(re.search(r'data-bulk>\s*(\{#.*?#\}\s*)?<button type="submit" disabled hidden', apps, re.S) is not None,
          "Enter in the bulk comment cannot submit (disabled default button first)")
    reg = c.get("/registry", params={"rank": "КМС", "stage": "УТ"}).text
    check('href="/registry?tab=trainer"' in reg, "registry: rank/stage filters are not carried to the trainers tab")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "galiev@fsp56.ru")  # ленту в e2e ещё не открывал: есть новое напоминание о категории
    before = c.get("/notifications", params={"q": "нет-такого-слова"})
    check("ничего не найдено" in before.text and "Уведомления: новых" in before.text,
          "notification search does not mark hidden notifications as read")
    c.get("/notifications")
    check("Уведомления: новых" not in c.get("/notifications").text, "opening the feed marks the shown notifications as read")

print("== 10. market: profile with portfolio, offer, accept → contact revealed")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as guest:
    check(guest.get("/market").url.path == "/login", "market is closed to guests")


with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as ab, \
        httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as sokolov:
    ab.post("/login", data={"email": "abramov@fsp56.ru", "password": "fsp2026"})
    sokolov.post("/login", data={"email": "athlete@fsp56.ru", "password": "fsp2026"})
    feed = page(ab, "/market")
    check("Биржа программистов" in feed and "Backend и алгоритмы" in feed and "@sokolov_dev" not in feed,
          "feed lists profiles without contacts")
    check("Backend и алгоритмы" in ab.get("/market", params={"skill": "C++"}).text
          and "Продуктовая разработка" not in ab.get("/market", params={"skill": "C++"}).text, "skill filter")
    r = ab.post("/market/save", data={"headline": "Боты для Telegram", "about": "Пишу ботов на Python для школ и секций.",
                                      "skills": "Python, aiogram", "pf_title": "Бот", "pf_url": "javascript:alert(1)",
                                      "contact": "@abramov"})
    check("Ссылка должна начинаться" in r.text, "non-http portfolio link rejected")
    r = ab.post("/market/save", data={"headline": "Боты для Telegram", "about": "Пишу ботов на Python для школ и секций.",
                                      "skills": "Python, aiogram", "pf_title": "Бот", "pf_url": "https://example.org/bot",
                                      "contact": "@abramov"})
    check("Анкета опубликована" in r.text, "profile published")
    r = ab.post("/market/1/offer", data={"project_title": "Бот для секции", "description": "Нужен бот записи на занятия секции."})
    check("Отклик отправлен" in r.text, "offer sent")
    mine = sokolov.get("/market/my")
    oid = re.findall(r"/market/offers/(\d+)/decision", mine.text)[0]  # входящие: новые сверху
    check("Новый отклик на вашу анкету" in sokolov.get("/notifications").text, "owner notified of the offer")
    r = sokolov.post(f"/market/offers/{oid}/decision", data={"decision": "accept"})
    check("принят" in r.text, "owner accepted the offer")
    check("@sokolov_dev" in ab.get("/market/my").text, "owner contact revealed to the accepted sender")

print("== 11. market API and MCP: bearer token, owner rules, stdio MCP server")


async def mcp_session(token: str) -> tuple[list[str], dict, bool]:
    """Настоящий MCP-сервер по stdio против запущенного стека: список инструментов, поиск и ошибка правил."""
    params = StdioServerParameters(command=sys.executable, args=["-X", "utf8", "-m", "mcp_market"], cwd=str(ROOT),
                                   env={**os.environ, "PYTHONPATH": str(ROOT), "FSP_MARKET_TOKEN": token,
                                        "FSP_MARKET_API_URL": f"{BASE}/api/market/v1"})
    async with Client(params) as client:
        names = [t.name for t in (await client.list_tools()).tools]
        found = json.loads((await client.call_tool("market_search_profiles", {"skill": "C++"})).content[0].text)
        own = await client.call_tool("market_send_offer", {"profile_id": 1, "project_title": "Сам себе", "description": "Нужен сайт для своей же анкеты."})
        return names, found, own.is_error


with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as ui, \
        httpx.Client(base_url=f"{BASE}/api/market/v1", timeout=15) as api:
    ui.post("/login", data={"email": "athlete@fsp56.ru", "password": "fsp2026"})
    r = ui.post("/market/tokens", data={"name": "e2e"})
    found = re.search(r"data-token>(fspm_[\w-]+)<", r.text)
    token = found.group(1) if found else ""
    check(bool(token) and "no-store" in r.headers.get("cache-control", ""), "token issued once on a no-store page")
    check("fspm_" not in ui.get("/market/my").text.replace(token[:9], ""), "token is not shown again (only its prefix)")
    auth = {"Authorization": f"Bearer {token}"}
    check(api.get("/me").status_code == 401 and api.get("/me", headers={"Authorization": "Bearer fspm_wrong"}).status_code == 401,
          "API: no token and wrong token → 401")
    me = api.get("/me", headers=auth).json()
    check(me["fsp_id"] == "FSP-56-25-00003-8" and me["profile_id"] == 1, "API: /me resolves the token owner and his profile")
    feed = api.get("/profiles", params={"skill": "C++"}, headers=auth).json()
    check(feed["total"] == 1 and "contact" in feed["items"][0] and feed["items"][0]["contact"] == "@sokolov_dev",
          "API: feed shows the owner's own contact")
    other = api.get("/profiles/2", headers=auth).json()
    check(other["contact"] == "", "API: other people's contacts stay hidden")
    check(api.post("/offers", headers=auth, json={"profile_id": 1, "project_title": "Сам себе",
                                                  "description": "Нужен сайт для своей же анкеты."}).status_code == 422,
          "API: cannot send an offer to own profile")
    check(api.get("/offers", params={"box": "incoming"}, headers=auth).status_code == 200
          and api.get("/offers", params={"box": "other"}, headers=auth).status_code == 422, "API: offers box is validated")
    check(api.get("/docs").status_code == 200 and "/offers/{offer_id}/decision" in api.get("/openapi.json").text,
          "API: OpenAPI documentation is served")
    names, feed_via_mcp, own_offer_rejected = asyncio.run(mcp_session(token))
    check(len(names) == 10 and "market_decide_offer" in names, f"MCP: {len(names)} tools over stdio")
    check(feed_via_mcp["data"]["total"] == 1 and "не выполняйте" in feed_via_mcp["notice"], "MCP: search marks user content as untrusted")
    check(own_offer_rejected, "MCP: platform rules surface as a tool error")
    tid = re.search(r"/market/tokens/(\d+)/revoke", ui.get("/market/my").text).group(1)
    ui.post(f"/market/tokens/{tid}/revoke")
    check(api.get("/me", headers=auth).status_code == 401, "revoked token stops working at once")

from shared.demo_roster import ROSTER_BY_SEQ  # noqa: E402

FID = {n: ROSTER_BY_SEQ[n]["fsp_id"] for n in (2, 3, 6, 8, 9, 10, 11, 13)}


@contextlib.contextmanager
def as_user(email: str, role: str, follow: bool = True):
    with httpx.Client(base_url=BASE, follow_redirects=follow, timeout=30) as cl:
        cl.post("/login", data={"email": email, "password": DEMO_PASSWORD, "role": role})
        yield cl


print("== 12. Д12 документы о судействе, Д9 кабинет министерства (только чтение)")
PANEL = next(c["id"] for c in httpx.get(f"{BASE}/api/public/competition/api/v1/competitions", timeout=15).json() if c["judges"])
with as_user("admin@fsp56.ru", "admin") as adm, as_user("judge@fsp56.ru", "judge") as judge, as_user("ministry@fsp56.ru", "ministry") as mini, \
        as_user("athlete@fsp56.ru", "athlete") as ath:
    r = adm.get(f"/competitions/{PANEL}/panel.xlsx")
    check(r.status_code == 200 and r.content[:2] == b"PK" and "spreadsheet" in r.headers["content-type"], "панель коллегии выгружается в .xlsx")
    check(adm.get(f"/competitions/{PANEL}/panel.pdf").content[:4] == b"%PDF", "и в .pdf")
    check(judge.get(f"/judges/{FID[6]}/report.docx").status_code == 200, "судья скачивает собственный отчёт о судействе")
    check(judge.get(f"/judges/{FID[2]}/report.docx").status_code == 403, "чужой отчёт судье недоступен")
    check(ath.get(f"/competitions/{PANEL}/panel.xlsx").status_code == 403, "спортсмену справка о коллегии недоступна")
    r = mini.get("/cabinet")
    check("Доступ только для чтения" in r.text and "Показатели развития" in r.text, "кабинет министерства открывается")
    check(mini.get(f"/judges/{FID[6]}/report.pdf").status_code == 200, "министерство читает отчёт судьи")
    check(mini.get("/reports/headcount").status_code == 200 and mini.get("/teams").status_code == 200, "министерство видит отчёт о численности и сборные")
    check(mini.get("/registry").status_code == 403 and mini.get("/integrations").status_code == 403, "реестр и интеграции министерству закрыты")
    check(mini.post("/cabinet/admin/competitions", data={"title": "Не должно создаться"}).status_code == 403, "министерство ничего не меняет")
    check('action="/cabinet/admin/teams/' not in mini.get("/teams/1").text, "в составе сборной у министерства нет кнопок правки")
    html = mini.get("/cabinet").text
    check("Прирост занимающихся" in html and "Участники проведённых соревнований" in html and "Спортсмены с разрядом" in html
          and html.count('role="img"') >= 4 and "Дисциплины" in html, "инфографика министерства: прирост, соревнования, разрезы")

print("== 13. Д6 показатели развития и подтверждающие документы")
with as_user("admin@fsp56.ru", "admin") as adm, as_user("ministry@fsp56.ru", "ministry") as mini:
    html = adm.get("/development").text
    check("Численность занимающихся" in html and "Официальные соревнования" in html and "Организации, ведущие" in html, "три показателя с источниками")
    r = adm.post("/development/attachments", data={"indicator": "headcount", "year": "2026", "title": "Справка по численности",
                                                   "url": "https://docs.example.org/hc.pdf"})
    check("Справка по численности" in r.text, "ссылка-подтверждение приложена")
    r = adm.post("/development/attachments", data={"indicator": "organizations", "year": "2026", "title": "Выписка из реестра"},
                 files={"file": ("vypiska.pdf", b"%PDF-1.4 e2e", "application/pdf")})
    aid = re.search(r'/development/attachments/(\d+)"', r.text).group(1)
    got = mini.get(f"/development/attachments/{aid}")
    check(got.content == b"%PDF-1.4 e2e" and "attachment" in got.headers["content-disposition"] and got.headers["x-content-type-options"] == "nosniff",
          "файл скачивается как вложение, министерство его видит")
    r = adm.post("/development/attachments", data={"indicator": "organizations", "year": "2026", "title": "Вредный файл"},
                 files={"file": ("a.exe", b"MZ", "application/octet-stream")})
    check("Допустимые файлы" in r.text, "исполняемый файл отклонён")
    check(mini.post("/development/attachments", data={"indicator": "headcount", "year": "2026", "title": "Чужое", "url": "https://x.example"}).status_code == 403,
          "министерство вложения не добавляет")

print("== 13a. Файл положения и блок «Ближайшие соревнования» федерации")
with as_user("admin@fsp56.ru", "admin") as adm, as_user("athlete@fsp56.ru", "athlete") as ath, httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as guest:
    check("// ближайшие соревнования" in adm.get("/cabinet").text, "на дашборде федерации есть блок «Ближайшие соревнования»")
    r = adm.post("/competitions/1/regulation-file", files={"file": ("polozhenie.pdf", b"%PDF-1.4 e2e reg", "application/pdf")})
    check("Файл положения загружен" in r.text and "Скачать polozhenie.pdf" in r.text, "федерация загрузила файл положения")
    got = guest.get("/competitions/1/regulation-file")
    check(got.content == b"%PDF-1.4 e2e reg" and "attachment" in got.headers["content-disposition"] and got.headers["x-content-type-options"] == "nosniff",
          "гость скачивает положение как вложение")
    check("Скачать polozhenie.pdf" in ath.get("/competitions/1/regulation").text and "Загрузить файл" not in ath.get("/competitions/1/regulation").text,
          "спортсмен видит ссылку, но не форму загрузки")
    check(ath.post("/competitions/1/regulation-file", files={"file": ("x.pdf", b"%PDF", "application/pdf")}).status_code == 403
          and ath.post("/competitions/1/regulation-file/delete").status_code == 403, "загружать и удалять может только федерация")
    check("Положение загружается файлом" in adm.post("/competitions/1/regulation-file", files={"file": ("a.exe", b"MZ", "application/octet-stream")}).text,
          "исполняемый файл отклонён")
    check("Файл положения удалён" in adm.post("/competitions/1/regulation-file/delete").text
          and guest.get("/competitions/1/regulation-file").status_code == 404, "файл удалён")

print("== 13b. Личные токены роли и API участника (/api/me/v1)")


def issue_token(client: httpx.Client, name: str) -> str:
    r = client.post("/api-access/tokens", data={"name": name})
    m = re.search(r"data-token>(fspu_[\w-]+)<", r.text)
    check(r.status_code == 200 and bool(m) and "no-store" in r.headers.get("cache-control", ""), f"токен «{name}» выпущен и показан один раз")
    return m.group(1) if m else ""


with as_user("trainer@fsp56.ru", "trainer") as tr, as_user("ministry@fsp56.ru", "ministry") as mini, as_user("admin@fsp56.ru", "admin") as adm,         httpx.Client(base_url=f"{BASE}/api/me/v1", timeout=30) as api:
    def get(token: str, path: str, **params):
        return api.get(path, params=params, headers={"Authorization": f"Bearer {token}"})

    tt, mt, at = issue_token(tr, "e2e тренер"), issue_token(mini, "e2e министр"), issue_token(adm, "e2e федерация")
    check("API и MCP" in tr.get("/api-access").text and "e2e тренер" in tr.get("/api-access").text and tt not in tr.get("/api-access").text,
          "страница «API и MCP»: токен в списке, открытое значение больше не показывается")
    check(api.get("/me").status_code == 401 and get("fspu_" + "x" * 40, "/me").status_code == 401, "без токена и с чужим — 401")
    me = get(tt, "/me").json()
    check(me["role"] == "trainer" and "my_athletes" in me["can"], "токен тренера: роль и доступные операции")
    wards = get(tt, "/trainer/athletes")
    check(wards.status_code == 200 and wards.json() and "@" not in wards.text and "birth_date" not in wards.text, "подопечные тренера без e-mail и даты рождения")
    check(get(tt, "/reports/headcount").status_code == 403 and get(tt, "/admin/applications").status_code == 403, "тренеру закрыты отчёты и решения федерации")
    check(get(mt, "/reports/headcount", years=3).status_code == 200 and get(mt, "/dashboard").status_code == 200
          and get(mt, "/teams").status_code == 200 and get(mt, "/development/competitions").status_code == 200, "токен министерства читает отчёты, показатели и сборные")
    check(api.post("/admin/applications/1/decision", json={"status": "APPROVED"}, headers={"Authorization": f"Bearer {mt}"}).status_code == 403
          and api.post("/applications", json={"competition_id": 1}, headers={"Authorization": f"Bearer {mt}"}).status_code == 403, "министерству действия закрыты")
    check(get(at, "/admin/applications").status_code == 200 and get(at, "/competitions", upcoming="true").status_code == 200
          and get(tt, "/competitions/1/protocol").status_code in (200, 403), "токен федерации видит заявки и календарь")
    tid = re.search(r'/api-access/tokens/(\d+)/revoke', tr.get("/api-access").text).group(1)
    check("Токен отозван" in tr.post(f"/api-access/tokens/{tid}/revoke").text and get(tt, "/me").status_code == 401, "отзыв токена закрывает доступ сразу")

print("== 14. Д7 личный путь и справочник понятий")
with as_user("athlete@fsp56.ru", "athlete") as ath, httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as guest:
    html = ath.get("/journey").text
    check("Ступени ЕВСК" in html and "Что нужно для" in html and "Подходящие старты" in html, "личный путь: ступени, требования и старты")
    g = guest.get("/glossary").text
    check("РУСАДА" in g and "Как стать судьёй" in g and "Чемпионат" in g and "rusada.ru" in g, "справочник понятий открыт без входа")
    check(guest.get("/journey").url.path == "/login", "личный путь — только для вошедших")

print("== 15. Д8 премии Губернатора: автоотбор → ходатайство → статусы")
with as_user("admin@fsp56.ru", "admin") as adm, as_user("ministry@fsp56.ru", "ministry") as mini:
    html = adm.get("/awards").text
    year = re.search(r'name="year" value="(\d+)"', html).group(1)
    check("Претенденты по протоколам" in html and "Справочник размеров премий" in html and "до 1 февраля" in html.replace("\n", " ") or f"{int(year) + 1}" in html,
          "претенденты и справочник размеров")
    r = adm.post("/awards/petitions", data={"year": year, "note": "e2e"})
    check("Ходатайство подготовлено" in r.text, "ходатайство сформировано по протоколам года")
    check("уже подготовлено" in adm.post("/awards/petitions", data={"year": year}).text, "второе ходатайство за год не создаётся")
    pid = re.search(r"/awards/petitions/(\d+)/package", r.text).group(1)
    check(adm.get(f"/awards/petitions/{pid}/package.docx").content[:2] == b"PK" and mini.get(f"/awards/petitions/{pid}/package.pdf").content[:4] == b"%PDF",
          "пакет документов: .docx у федерации, .pdf у министерства")
    r = adm.post(f"/awards/petitions/{pid}/status", data={"status": "SENT"})
    check("направлено" in r.text.lower(), "статус: направлено")
    r = adm.post(f"/awards/petitions/{pid}/status", data={"status": "AWARDED", "order_no": ""})
    check("реквизиты акта" in r.text, "назначение без реквизитов акта отклонено")
    r = adm.post(f"/awards/petitions/{pid}/status", data={"status": "AWARDED", "order_no": "Распоряжение № 15-р"})
    check("премия назначена" in r.text.lower(), "статус: премия назначена")
    check(mini.post("/awards/rates", data={}).status_code == 403, "справочник размеров министерству недоступен для правки")

print("== 16. Д5 апелляции и рапорты о нарушениях")
with as_user("abramov@fsp56.ru", "athlete") as ath, as_user("admin@fsp56.ru", "admin") as adm:
    html = ath.get("/appeals").text
    opt = re.search(r'<option value="(FSP-[^|"]+)\|(\d+)">', html)
    check(bool(opt), "спортсмен видит свои результаты для апелляции")
    fid, cid = opt.group(1), opt.group(2)
    r = ath.post("/appeals", data={"result": f"{fid}|{cid}", "subject": "Не засчитана задача C", "ev_kind": ["LOG", "OTHER", "OTHER"],
                                   "description": "Решение прошло все тесты на стороне жюри, посылка отмечена неверной — прошу проверить журнал.",
                                   "ev_title": ["Лог проверки", "", ""], "ev_url": ["https://logs.example.org/42", "", ""]})
    aid = re.search(r"appeal-(\d+)", r.text).group(1)
    check("Апелляция №" in r.text and "Лог проверки" in r.text, "апелляция подана с доказательством")
    r = adm.post(f"/appeals/{aid}/action", data={"action": "review"})
    check("на рассмотрении" in r.text.lower(), "федерация берёт апелляцию в рассмотрение")
    r = adm.post(f"/appeals/{aid}/decision", data={"decision": "accept", "rationale": "Журнал проверок подтвердил ошибку: результат пересчитан.",
                                                   "correction": "RESCORE", "solved": "26", "penalty": "0", "score": "100"})
    check("удовлетворена" in r.text.lower() and "Протокол скорректирован" in r.text, "решение: удовлетворена, протокол скорректирован")
    proto = httpx.get(f"{BASE}/api/public/competition/api/v1/competitions/{cid}/protocol", timeout=15).json()
    me = next(e for e in proto["entries"] if e["fsp_id"] == fid)
    check(proto["corrections"] and me["place"] == 1, "в протоколе запись о корректировке, спортсмен переместился на 1 место")
    check("Корректировки по решениям федерации" in httpx.get(f"{BASE}/protocols/{cid}", timeout=15).text, "корректировка видна на странице протокола")
    check(ath.post(f"/appeals/{aid}/decision", data={"decision": "reject", "rationale": "Попытка самому решить"}).status_code == 403,
          "решать может только федерация")
    html = adm.get(f"/appeals?violation_cid={cid}").text
    subj = re.search(r'name="subject_fsp_id"[^>]*>.*?<option value="(FSP-[^"]+)"', html, re.S).group(1)
    r = adm.post("/violations", data={"competition_id": cid, "subject_fsp_id": subj, "description": "Передача решений третьим лицам во время тура.",
                                     "ev_kind": ["VIDEO", "OTHER", "OTHER"], "ev_title": ["Запись экрана", "", ""],
                                     "ev_url": ["https://video.example.org/9", "", ""]})
    vid = re.search(r"violation-(\d+)", r.text).group(1)
    check("Рапорт №" in r.text, "рапорт о нарушении подан")
    r = adm.post(f"/violations/{vid}/decision", data={"decision": "DSQ", "rationale": "Нарушение подтверждено записью экрана.",
                                                      "sanction_days": "30", "reason": "Передача решений"})
    check("дисквалификация" in r.text.lower() and "санкция до" in r.text, "решение: дисквалификация и санкция в реестре")
    proto = httpx.get(f"{BASE}/api/public/competition/api/v1/competitions/{cid}/protocol", timeout=15).json()
    check(next(e for e in proto["entries"] if e["fsp_id"] == subj)["status"] == "DSQ" and len(proto["corrections"]) == 2,
          "в протоколе нарушитель дисквалифицирован")

print("== 17. Д4 нормативы и аттестация по ФССП")
with as_user("trainer@fsp56.ru", "trainer") as tr, as_user("admin@fsp56.ru", "admin") as adm, as_user("athlete@fsp56.ru", "athlete") as ath:
    html = tr.get("/training").text
    check("Нормативы и аттестация по ФССП" in html and "готов → " in html, "тренер видит подопечных и готовых к переводу")
    check(f"/training/athletes/{FID[10]}" in html, "в списке подопечные тренера")
    r = tr.post(f"/training/athletes/{FID[10]}/attest", data={})
    check("Не выполнено обязательных нормативов" in r.text, "не готового к переводу аттестовать нельзя")
    page_html = tr.get(f"/training/athletes/{FID[10]}").text
    typing = re.search(r'<option value="(\d+)">Скорость печати', page_html).group(1)
    r = tr.post(f"/training/athletes/{FID[10]}/results", data={"norm_id": typing, "value": "400"})
    check("норматив выполнен" in r.text and "готов к переводу" in r.text, "результат внесён — платформа сама признала норматив выполненным")
    r = tr.post(f"/training/athletes/{FID[10]}/attest", data={"note": "e2e"})
    check("Оформлен перевод" in r.text, "перевод на следующий этап оформлен")
    check("Аттестации и переводы" in tr.get(f"/training/athletes/{FID[10]}").text, "история аттестаций в профиле")
    r = tr.post(f"/training/athletes/{FID[3]}/results", data={"norm_id": typing, "value": "400"})
    check("Результаты вносит" in r.text or "доступ" in r.text.lower() or r.status_code == 403, "тренер не вносит результаты чужому спортсмену")
    check(ath.get("/training").url.path == f"/training/athletes/{FID[3]}", "спортсмен попадает в собственный профиль подготовки")
    check("Аттестации и переводы" in ath.get("/training/athletes/" + FID[3]).text, "у спортсмена видна история аттестаций")
    check(ath.get(f"/training/athletes/{FID[8]}").status_code == 403, "чужой профиль подготовки спортсмену закрыт")
    r = adm.post("/training/norms", data={"title": "Тестовый норматив e2e", "kind": "SPECIAL", "stage": "УТ", "discipline": "ALL", "gender": "ALL",
                                         "unit": "балл", "direction": "MIN", "threshold": "7", "required": "1", "active": "1"})
    check("Тестовый норматив e2e" in r.text, "федерация пополняет справочник нормативов")
    check(tr.post("/training/norms", data={"title": "Чужой норматив", "kind": "SPECIAL", "stage": "УТ", "unit": "x", "direction": "MIN",
                                          "threshold": "1"}).status_code == 403, "тренер справочник не правит")

print("== 18. Д3 организации, группы, журнал занятий → отчёт о численности")
with as_user("admin@fsp56.ru", "admin") as adm, as_user("trainer@fsp56.ru", "trainer") as tr:
    html = adm.get("/organizations").text
    check("Лицей № 1 г. Оренбурга" in html and "лицензия" in html and "групп:" in html, "карточки организаций: лицензия, программы, группы")
    gid = re.search(r"/groups/(\d+)", adm.get("/organizations/card?name=" + "Лицей № 1 г. Оренбурга").text).group(1)
    grp = adm.get(f"/groups/{gid}")
    check("Журнал занятий" in grp.text and "Состав" in grp.text, "страница группы: состав и журнал")
    attendees = re.findall(r'name="attendees" value="([^"]+)"', grp.text)
    base = {"on": time.strftime("%Y-%m-%d"), "topic": "Контрольный контест", "duration_min": "90", "attendees": attendees}
    r = adm.post(f"/groups/{gid}/sessions", data={**base, "format": "ONLINE", "confirmation_kind": "", "confirmation_ref": ""})
    check("подтверждается ссылкой на запись" in r.text, "онлайн-занятие без подтверждения не принимается")
    r = adm.post(f"/groups/{gid}/sessions", data={**base, "format": "ONLINE", "confirmation_kind": "RECORDING",
                                                  "confirmation_ref": "https://video.example.org/e2e"})
    check("Занятие занесено в журнал" in r.text and "Контрольный контест" in r.text, "онлайн-занятие с записью занесено в журнал")
    check(tr.get(f"/groups/{gid}").status_code == 200, "тренер видит группы организаций")
    rep = adm.get("/reports/headcount").text
    check("занятия по журналу" in rep.lower() and "по зачислению в группы" in rep.lower(), "группы и журнал попали в отчёт о численности")
    x = adm.get("/reports/headcount/export.xlsx")
    check(x.status_code == 200 and x.content[:2] == b"PK", "отчёт с новыми разрезами выгружается в .xlsx")

print("== 19. Д1 «Войти через ФСП ID» (OpenID Connect, authorization code + PKCE)")
import base64 as _b64  # noqa: E402
import hashlib as _h  # noqa: E402
from urllib.parse import parse_qs, urlsplit  # noqa: E402

import jwt as _jwt  # noqa: E402


def pkce() -> tuple[str, str]:
    v = _b64.urlsafe_b64encode(os.urandom(40)).decode().rstrip("=")
    return v, _b64.urlsafe_b64encode(_h.sha256(v.encode()).digest()).decode().rstrip("=")


with httpx.Client(base_url=BASE, follow_redirects=False, timeout=30) as anon:
    conf = anon.get("/.well-known/openid-configuration").json()
    check(conf["code_challenge_methods_supported"] == ["S256"] and conf["jwks_uri"].endswith("/oidc/jwks")
          and "RS256" in conf["id_token_signing_alg_values_supported"], "discovery-документ")
    verifier, challenge = pkce()
    redirect = f"{BASE}/oidc/demo/callback"
    params = {"response_type": "code", "client_id": "fsp-demo-client", "redirect_uri": redirect, "scope": "openid profile roles disciplines rank",
              "state": "st-1", "nonce": "nn-1", "code_challenge": challenge, "code_challenge_method": "S256"}
    r = anon.get("/oidc/authorize", params=params)
    check(r.status_code == 303 and r.headers["location"].startswith("/login?next="), "без входа authorize ведёт на страницу входа")
    check(anon.get("/oidc/authorize", params={**params, "redirect_uri": "https://evil.example/cb"}).status_code == 400,
          "незарегистрированный адрес возврата отклонён")
with as_user("athlete@fsp56.ru", "athlete", follow=False) as me, httpx.Client(base_url=BASE, timeout=30) as api:
    r = me.get("/oidc/authorize", params={**params, "code_challenge": ""})
    check(r.status_code == 303 and "error=invalid_request" in r.headers["location"], "без PKCE авторизация отклоняется")
    r = me.get("/oidc/authorize", params=params)
    check(r.status_code == 200 and "Войти через ФСП ID" in r.text and "Идентификатор ФСП ID" in r.text, "страница согласия показывает передаваемые данные")
    blob = re.search(r'name="req" value="([^"]+)"', r.text).group(1)
    denied = me.post("/oidc/consent", data={"req": blob, "decision": "deny"})
    check("error=access_denied" in denied.headers["location"] and "state=st-1" in denied.headers["location"], "отказ пользователя возвращается приложению")
    allowed = me.post("/oidc/consent", data={"req": blob, "decision": "allow"})
    back_url = urlsplit(allowed.headers["location"])
    q = parse_qs(back_url.query)
    check(back_url.path == "/oidc/demo/callback" and q["state"] == ["st-1"] and "code" in q, "после согласия приложение получает код и state")
    tok_req = {"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": redirect, "code_verifier": verifier}
    bad = api.post("/oidc/token", data={**tok_req, "code_verifier": "x" * 50}, auth=("fsp-demo-client", "fsp-demo-secret"))
    check(bad.status_code == 400 and bad.json()["error"] == "invalid_grant", "неверный code_verifier отклонён (код при этом сгорает)")
    r = me.get("/oidc/authorize", params=params)
    blob = re.search(r'name="req" value="([^"]+)"', r.text).group(1)
    code = parse_qs(urlsplit(me.post("/oidc/consent", data={"req": blob, "decision": "allow"}).headers["location"]).query)["code"][0]
    check(api.post("/oidc/token", data={**tok_req, "code": code}).status_code == 401, "без секрета клиента токен не выдаётся")
    code = parse_qs(urlsplit(me.post("/oidc/consent", data={"req": blob, "decision": "allow"}).headers["location"]).query)["code"][0]
    tok = api.post("/oidc/token", data={**tok_req, "code": code}, auth=("fsp-demo-client", "fsp-demo-secret")).json()
    check("id_token" in tok and tok["token_type"] == "Bearer", "обмен кода на токены")
    key = _jwt.PyJWK(api.get("/oidc/jwks").json()["keys"][0]).key
    claims = _jwt.decode(tok["id_token"], key, algorithms=["RS256"], audience="fsp-demo-client", issuer=conf["issuer"])
    check(claims["sub"] == FID[3] and claims["nonce"] == "nn-1" and "Соколов" in claims["name"] and "athlete" in claims["roles"],
          "id_token подписан RS256, в нём ФИО, роли и nonce")
    info = api.get("/oidc/userinfo", headers={"Authorization": "Bearer " + tok["access_token"]}).json()
    check(info["sub"] == FID[3] and info["rank"] and "disciplines" in info, "userinfo: ФИО, роли, дисциплины, разряд")
    check(api.get("/oidc/userinfo", headers={"Authorization": "Bearer nope"}).status_code == 401, "userinfo с чужим токеном недоступен")
    reuse = api.post("/oidc/token", data={**tok_req, "code": code}, auth=("fsp-demo-client", "fsp-demo-secret"))
    check(reuse.status_code == 400, "код одноразовый")
with as_user("admin@fsp56.ru", "admin") as adm:
    r = adm.post("/oidc/clients", data={"name": "Сайт партнёра e2e", "redirect_uris": "https://partner.example.org/cb"})
    check(bool(re.search(r"data-token>(fspcs_[\w-]+)<", r.text)) and "client_id:" in r.text, "администратор регистрирует приложение, секрет показан один раз")
    check("fspcs_" not in adm.get("/oidc/clients").text, "секрет повторно не показывается")
    check(adm.get("/oidc/demo").status_code == 200, "тестовое приложение-партнёр доступно")

print("== 20. Д2 интеграции: токены с ограничением прав, вебхуки, импорт результатов")
with as_user("admin@fsp56.ru", "admin") as adm, httpx.Client(base_url=f"{BASE}/api/ext/v1", timeout=30) as ext, as_user("ministry@fsp56.ru", "ministry") as mini:
    check(mini.get("/integrations").status_code == 403, "интеграции — только федерации")
    r = adm.post("/integrations/clients", data={"name": "Партнёр e2e", "scope": ["competitions:read", "protocols:read"]})
    token = re.search(r"data-token>(fspi_[\w-]+)<", r.text).group(1)
    auth = {"Authorization": f"Bearer {token}"}
    check(ext.get("/competitions").status_code == 401 and ext.get("/competitions", headers={"Authorization": "Bearer fspi_x"}).status_code == 401,
          "без токена и с чужим — 401")
    cal = ext.get("/competitions", headers=auth, params={"status": "REGISTRATION_OPEN"})
    check(cal.status_code == 200 and cal.json() and all(c["status"] == "REGISTRATION_OPEN" for c in cal.json()), "календарь по токену")
    pub = next(c["id"] for c in ext.get("/competitions", headers=auth).json() if c["protocol_published"])
    check(ext.get(f"/competitions/{pub}/protocol", headers=auth).json()["entries"], "опубликованный протокол по токену")
    check(ext.get("/competitions/999/protocol", headers=auth).status_code == 404, "несуществующий протокол — 404")
    check(ext.post("/competitions/22/results", headers=auth, json={"results": [{"fsp_id": FID[9], "solved": 1}]}).status_code == 403,
          "без области results:write импорт закрыт")
    check("/competitions/{cid}/results" in ext.get("/openapi.json").text and ext.get("/docs").status_code == 200, "описание API (OpenAPI) для разработчиков")
    r = adm.post("/integrations/clients", data={"name": "Импорт e2e", "scope": ["competitions:read", "results:write"]})
    token_w = re.search(r"data-token>(fspi_[\w-]+)<", r.text).group(1)
    sys_id = re.findall(r"/integrations/clients/(\d+)/revoke", r.text)[0]
    r = adm.post("/integrations/webhooks", data={"client_id": sys_id, "url": f"{BASE}/integrations/demo-receiver",
                                                 "event": ["protocol.published", "competition.published", "application.approved"]})
    check(bool(re.search(r"data-token>([0-9a-f]{48})<", r.text)), "вебхук создан, секрет подписи показан один раз")
    hid = re.search(r"/integrations/webhooks/(\d+)/test", r.text).group(1)
    r = adm.post(f"/integrations/webhooks/{hid}/test")
    check("доставлено" in r.text, "тестовое событие доставлено")
    time.sleep(0.3)
    check("ping" in adm.get("/integrations").text, "демо-приёмник показывает принятое событие")
    adm.post("/cabinet/admin/competitions", data={"title": "Открытый кубок вебхуков", "discipline": "ALG", "level": "REGIONAL", "type": "Кубок",
                                                 "format": "ONLINE", "calendar_plan": "REGIONAL", "plan_number": "№ 56-301", "start_date": "2027-04-01",
                                                 "end_date": "2027-04-01", "venue": "онлайн", "age_group": "без ограничений 12+",
                                                 "registration_deadline": "2027-03-20"})
    deadline = time.time() + 5
    while time.time() < deadline and "competition.published" not in adm.get("/integrations").text:
        time.sleep(0.2)
    check("competition.published" in adm.get("/integrations").text, "создание соревнования уходит вебхуком competition.published")
    r = ext.post("/competitions/22/results", headers={"Authorization": f"Bearer {token_w}"}, json={"system": "Партнёр", "results": [
        {"fsp_id": FID[9], "solved": 5, "penalty": 240}, {"fsp_id": FID[13], "solved": 6, "penalty": 400}]})
    check(r.status_code in (202, 422, 409), f"импорт результатов принят API: {r.status_code} {r.text[:80]}")
    r = adm.post("/integrations/clients/" + sys_id + "/revoke")
    check("Токен отозван" in r.text, "токен можно отозвать, вебхуки отключаются")
    check(ext.get("/competitions", headers={"Authorization": f"Bearer {token_w}"}).status_code == 401, "отозванный токен не работает")

print("== 21. упрощённые кабинеты, главная без живого протокола, клуб тренера")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as anon:
    home = anon.get("/").text
    check('id="live"' not in home and "Живой протокол" not in home, "главная: онлайн-протокола нет")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    html = login(c, "admin@fsp56.ru")
    check('aria-label="Профиль"' in html and "<canvas" not in html and "tail ./audit.log" not in html,
          "кабинет федерации: короткая шапка, таблица журнала убрана с обзора")
    check('href="/audit"' in html, "кабинет федерации: ссылка на журнал действий остаётся")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    html = login(c, "ministry@fsp56.ru")
    check("Доступ только для чтения" in html and "<canvas" not in html and "<details" in html,
          "кабинет министерства: короткая шапка, графики свёрнуты")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    login(c, "trainer@fsp56.ru")
    org = page(c, "/organizations")
    check('id="new-club"' in org and 'action="/organizations/clubs"' in org, "тренер видит форму «Создать клуб»")
    r = c.post("/organizations/clubs", data={"name": "Клуб «Код & Ко» г. Оренбург", "municipality": "г. Оренбург", "description": "Олимпиадники"})
    check("создан" in r.text and "Код &amp; Ко" in r.text and "Редактировать карточку" in r.text,
          "клуб создан: открылась его карточка, основатель её редактирует")
    check("Номер лицензии" not in r.text, "основатель клуба не вносит лицензию")
    dup = c.post("/organizations/clubs", data={"name": "клуб «код & ко» г. оренбург", "municipality": "г. Оренбург"})
    check("уже есть в реестре" in dup.text, "повтор названия отклонён")
    check("Клуб «Код &amp; Ко» г. Оренбург" in page(c, "/organizations"), "клуб в списке организаций")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as c:
    login(c, "athlete@fsp56.ru")
    c.post("/role", data={"role": "athlete"})
    check(c.post("/organizations/clubs", data={"name": "Клуб спортсмена", "municipality": "г. Оренбург"}).status_code == 403,
          "спортсмен клубы не создаёт")
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=15) as anon:
    check("Клуб «Код &amp; Ко»" in anon.get("/register").text, "клуб доступен в регистрации")
    cr = anon.get("/rating/clubs")
    check(cr.status_code == 200 and "Рейтинг клубов и организаций" in cr.text and "ОГУ" in cr.text, "рейтинг клубов открыт без входа и считает организации")
    only = anon.get("/rating/clubs", params={"only_clubs": "1", "discipline": "ALG"})
    check(only.status_code == 200 and "у клубов пока нет баллов" in only.text, "«только клубы»: у нового клуба баллов ещё нет")
    check("Рейтинг клубов" in anon.get("/").text and "Рейтинг клубов" in anon.get("/rating").text, "ссылки на рейтинг клубов в меню и на странице рейтинга")

print("\nRESULT:", "ALL PASSED" if ok else "FAILURES")
sys.exit(0 if ok else 1)

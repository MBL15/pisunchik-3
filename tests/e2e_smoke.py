"""End-to-end smoke test against a running stack (py run_all.py).  Run: py tests/e2e_smoke.py

Drives the three case scenarios through the gateway like a browser (cookies, form posts, redirects).
"""
import re
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
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
    page(c, "/calendar")
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
    check("кабинет: Федерация" in r.text and "Автоматические протоколы" in r.text, "admin dashboard")
    check("Управление рейтингом" in r.text and "Журнал действий" in r.text, "rating management + audit log")
    sample = re.search(r'<textarea id="log"[^>]*>(.*?)</textarea>', r.text, re.S).group(1)
    sample = sample.replace("&#34;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    r = c.post("/cabinet/admin/protocol/20/ingest", data={"log": sample})
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
    r = c.post("/login", data={"email": "admin@fsp56.ru", "password": "fsp2026"})
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
    r = c.post(f"/cabinet/admin/protocol/{SEC4}/results", data={**form, "action": "publish"})
    check(r.url.path == f"/protocols/{SEC4}" and "ПРОТОКОЛ" in r.text.upper() and "Главный судья" in r.text,
          "published → official protocol form with judges")
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

    cabinet = c.get("/cabinet").text
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
    check(cab.count("data-confirm=") >= 4, f"irreversible admin actions ask for confirmation ({cab.count('data-confirm=')} places)")
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
with httpx.Client(base_url=BASE, follow_redirects=True, timeout=30) as c:
    login(c, "abramov@fsp56.ru")
    check("Истекает срок разряда" in c.get("/notifications").text, "rank expiry reminder lands in the athlete's feed")

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

print("\nRESULT:", "ALL PASSED" if ok else "FAILURES")
sys.exit(0 if ok else 1)

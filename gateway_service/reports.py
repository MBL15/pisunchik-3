"""Построители выгружаемых документов: данные сервисов → `exports.Doc` (рендеринг в xlsx/docx/pdf — в `exports.py`)."""
from datetime import date

from gateway_service.exports import Doc, Heading, Para, Signatures, Table
from shared.domain import FEDERATION_NAME, ORDER_ISSUER, TEAM_ROLE_LABELS


def _d(value) -> str:
    return date.fromisoformat(str(value)[:10]).strftime("%d.%m.%Y") if value else "—"


def _period(c: dict) -> str:
    return _d(c["start_date"]) + (f" — {_d(c['end_date'])}" if c["end_date"] != c["start_date"] else "")


# ---------------------------------------------------------------- протокол соревнования
def protocol_doc(p: dict, judges: dict[str, str]) -> Doc:
    c, icpc = p["competition"], p["mode"] == "icpc"
    dsq = sum(e["status"] == "DSQ" for e in p["entries"])
    columns = ["Место", "Фамилия, имя, отчество", "ФСП ID", "Территория"] + (["Решено", "Штраф, мин"] if icpc else ["Баллы"]) + ["Примечание"]
    align = ["center", "left", "left", "left"] + (["center", "right"] if icpc else ["right"]) + ["left"]
    widths = [0.7, 3.2, 2.1, 1.8] + ([0.9, 1.1] if icpc else [1]) + [2]
    rows = [[("DSQ" if e["status"] == "DSQ" else e["place"]), e["full_name"], e["fsp_id"], e["region"]]
            + ([e["solved"], e["penalty"]] if icpc else [float(e["score"])]) + [e["dsq_reason"] or ""] for e in p["entries"]]
    blocks: list = []
    if not p["published"]:
        blocks.append(Para("ЧЕРНОВИК — протокол не опубликован", bold=True, align="center"))
    blocks += [Table(columns, rows, widths=widths, align=align),
               Signatures([(pos, judges.get(pos, "")) for pos in ("Главный судья", "Главный секретарь")])]
    source = ("по результатам, внесённым федерацией для участников с одобренными заявками" if p["source"] == "manual"
              else "из лога соревнования (формат FSP-LOG/1)")
    return Doc(
        title="ПРОТОКОЛ", subtitle=f"результатов официального спортивного соревнования\n{c['title']}",
        filename=f"Протокол {c['code']}", header_lines=["Вид спорта «спортивное программирование»", FEDERATION_NAME],
        meta=[("Код / план", f"{c['code']} · {c['plan_label']}, {c['plan_number']}"), ("Дисциплина", c["discipline_title"]),
              ("Статус / тип", f"{c['level_status']} · {c['type']}"), ("Сроки проведения", _period(c)),
              ("Место проведения", c["venue"]),
              ("Формат", c["format_label"] + (f" · {c['format_note']}" if c["format_note"] else "")),
              ("Возрастная группа", c["age_group"]),
              ("Ранжирование", "по решённым задачам и штрафному времени (ICPC)" if icpc else "по сумме баллов жюри"),
              ("Участников", f"{len(p['entries'])}" + (f", дисквалифицировано {dsq}" if dsq else ""))],
        blocks=blocks, landscape=True,
        footer=f"Протокол сформирован автоматически в Единой цифровой платформе ФСП Оренбургской области {source}. "
               f"Равные показатели делят место; дисквалифицированные участники указаны без места. Обновлён {p['updated_at'].replace('T', ' ')}.")


# ---------------------------------------------------------------- отчёт о численности занимающихся
def _pct(v) -> str:
    return "—" if v is None else f"{v:+.1f} %".replace(".", ",")


def headcount_doc(r: dict, filter_text: str) -> Doc:
    years = r["years"]
    columns = ["Показатель"] + [f"на {_d(d)}" for d in r["as_of"]] + [f"Δ {years[0]}→{years[-1]}", "Δ, %"]
    align = ["left"] + ["right"] * (len(years) + 2)
    widths = [3.4] + [1] * len(years) + [1.1, 1]
    total = r["total"]
    blocks: list = [Table(columns, [
        ["Занимающихся — всего", *total["counts"], f"{total['delta']:+d}", _pct(total["delta_pct"])],
        ["в т.ч. начали заниматься в течение года", *total["joined"], "", ""],
    ], title="Сводка", widths=widths, align=align)]
    for cut in r["cuts"]:
        if not cut["rows"]:
            continue
        blocks.append(Table(columns, [[row["label"], *row["counts"], f"{row['delta']:+d}", _pct(row["delta_pct"])] for row in cut["rows"]],
                            title=cut["title"], widths=widths, align=align))
        if cut["note"]:
            blocks.append(Para(cut["note"], small=True))
    blocks.append(Signatures([("Руководитель регионального отделения", "")]))
    return Doc(
        title="ОТЧЁТ О ЧИСЛЕННОСТИ ЗАНИМАЮЩИХСЯ", subtitle=f"динамика за {len(years) - 1} {'год' if len(years) == 2 else 'года'}: {years[0]}–{years[-1]}",
        filename=f"Численность занимающихся {years[0]}-{years[-1]}", header_lines=[FEDERATION_NAME],
        meta=[("Вид спорта", "спортивное программирование"), ("Сформирован", _d(r["generated_on"])), ("Фильтры", filter_text)],
        blocks=blocks,
        footer="Занимающимся на отчётную дату считается спортсмен реестра, начавший занятия не позже этого года (данных о выбытии нет). "
               "Возраст — на отчётную дату; разряд — по текущей записи профиля. Отчётная дата текущего года — день формирования.")


# ---------------------------------------------------------------- сборная: список и проект приказа
def _order_number(t: dict) -> str:
    no = t["order_no"].strip()
    return no if no.startswith("№") else f"№ {no}"


def _rating_basis(t: dict) -> str:
    parts = [f"рейтинг сезона {t['season']} · {t['discipline_title']}"]
    if t["age_group"]:
        parts.append(f"возрастная группа: {t['age_group']}")
    if t["min_rank"]:
        parts.append(f"разряд не ниже {t['min_rank']}")
    return "; ".join(parts)


def _member_row(i: int, m: dict) -> list:
    return [i, m["full_name"], _d(m["birth_date"]), m["fsp_id"], m["rank_short"], m["municipality"],
            ", ".join(m["coaches"]) or "—", m["position"], float(m["points"])]


_ROSTER_COLUMNS = ["№", "Фамилия, имя, отчество", "Дата рождения", "ФСП ID", "Разряд", "Муниципальное образование", "Тренер",
                   "Место в рейтинге", "Баллы"]
_ROSTER_ALIGN = ["center", "left", "center", "left", "center", "left", "left", "center", "right"]
_ROSTER_WIDTHS = [0.5, 2.6, 1.2, 2, 0.9, 1.8, 2.3, 0.9, 0.8]


def team_roster_doc(t: dict) -> Doc:
    main = [m for m in t["members"] if m["role"] == "MAIN"]
    reserve = [m for m in t["members"] if m["role"] == "RESERVE"]
    blocks: list = [Table(_ROSTER_COLUMNS, [_member_row(i, m) for i, m in enumerate(main, 1)], title=TEAM_ROLE_LABELS["MAIN"],
                          widths=_ROSTER_WIDTHS, align=_ROSTER_ALIGN)]
    if reserve:
        blocks.append(Table(_ROSTER_COLUMNS, [_member_row(i, m) for i, m in enumerate(reserve, 1)], title=TEAM_ROLE_LABELS["RESERVE"],
                            widths=_ROSTER_WIDTHS, align=_ROSTER_ALIGN))
    blocks.append(Signatures([("Руководитель регионального отделения", "")]))
    meta = [("Сборная", t["title"]), ("Дисциплина", t["discipline_title"]), ("Основание состава", _rating_basis(t)),
            ("Цель формирования", t["purpose"] or "—"), ("Состав", f"основной — {t['main_count']}, резерв — {t['reserve_count']}"),
            ("Статус", t["status_label"] + (f": приказ {_order_number(t)} от {_d(t['order_date'])}" if t["locked"] else "")),
            ("Тренеры", ", ".join(t["coaches"]) or "—")]
    return Doc(title="СПИСОК СБОРНОЙ КОМАНДЫ", subtitle=t["title"], filename=f"Список сборной {t['id']} {t['discipline']} {t['season']}",
               header_lines=[FEDERATION_NAME], meta=meta, blocks=blocks, landscape=True,
               footer=f"Состав набран вручную из рейтинга спортсменов региона; место и баллы — на момент включения в состав. "
                      f"Сформирован {_d(t['updated_at'])}.")


def team_order_doc(t: dict) -> Doc:
    """Проект приказа об утверждении состава сборной: готовит федерация, издаёт профильное министерство."""
    approved = t["locked"]
    date_line = f"от {_d(t['order_date'])} {_order_number(t)}" if approved else "от «___» ____________ 20___ г. № ______"
    goal = t["purpose"] or "подготовки и участия спортсменов в официальных всероссийских и межрегиональных спортивных соревнованиях"
    main = [m for m in t["members"] if m["role"] == "MAIN"]
    reserve = [m for m in t["members"] if m["role"] == "RESERVE"]
    discipline = t["discipline_title"].lower()
    items = [
        f"1. Утвердить состав сборной команды Оренбургской области по виду спорта «спортивное программирование» "
        f"(дисциплина «{discipline}») в количестве {len(main)} спортсменов основного состава"
        + (f" и {len(reserve)} спортсменов резерва" if reserve else "") + " согласно приложению к настоящему приказу.",
    ]
    if t["coaches"]:
        items.append(f"2. Закрепить за спортсменами сборной команды тренеров: {', '.join(t['coaches'])}.")
    items += [
        f"{len(items) + 1}. Региональному отделению Федерации спортивного программирования Оренбургской области обеспечить "
        f"участие спортсменов сборной команды в спортивных соревнованиях в соответствии с календарными планами.",
        f"{len(items) + 2}. Контроль за исполнением настоящего приказа оставляю за собой.",
    ]
    rows = [[i, m["full_name"], _d(m["birth_date"]), m["rank_short"], m["municipality"], ", ".join(m["coaches"]) or "—",
             TEAM_ROLE_LABELS[m["role"]]] for i, m in enumerate(main + reserve, 1)]
    blocks: list = [
        Para("ПРОЕКТ" if not approved else "Проект утверждён", bold=True, align="right"),
        Para(f"О формировании сборной команды Оренбургской области по спортивному программированию ({discipline})", bold=True),
        Para(f"В целях {goal}, на основании рейтинга спортсменов Оренбургской области за {t['season']} год, представленного "
             f"региональным отделением Федерации спортивного программирования Оренбургской области,"),
        Para("ПРИКАЗЫВАЮ:", bold=True),
        *[Para(i) for i in items],
        Signatures([("Министр", "")]),
        Heading("Приложение к приказу " + date_line),
        Table(["№", "Фамилия, имя, отчество", "Дата рождения", "Разряд", "Муниципальное образование", "Тренер", "Состав"], rows,
              title=f"Состав сборной команды Оренбургской области — {t['discipline_title']}",
              widths=[0.5, 2.6, 1.2, 0.9, 1.9, 2.4, 1.3], align=["center", "left", "center", "center", "left", "left", "left"]),
    ]
    return Doc(title="ПРИКАЗ", subtitle=date_line, filename=f"Проект приказа о сборной {t['id']} {t['discipline']} {t['season']}",
               header_lines=[ORDER_ISSUER], blocks=blocks,
               footer="Проект подготовлен автоматически в Единой цифровой платформе ФСП Оренбургской области на основании рейтинга "
                      "и подлежит согласованию; реквизиты приказа заполняются при утверждении.")


# ---------------------------------------------------------------- отчёт о соревнованиях спортсмена
def athlete_report_doc(person: dict, perf: dict, today: date) -> Doc:
    results = perf.get("results", [])
    rank = person["rank"]
    rows = []
    for r in results:
        if r["status"] == "DSQ":
            outcome = "дисквалификация"
        elif r["mode"] == "icpc":
            outcome = f"решено {r['solved']}, штраф {r['penalty']} мин"
        else:
            outcome = f"{r['score']:.2f} балла".replace(".", ",")
        rows.append([_d(r["date"]), r["title"], r["competition_code"], r["level_label"], "DSQ" if r["status"] == "DSQ" else r["place"],
                     f"из {r['protocol_size']}", outcome, r["base_points"]])
    return Doc(
        title="ОТЧЁТ О СОРЕВНОВАНИЯХ СПОРТСМЕНА", subtitle=person["full_name"], filename=f"Отчёт о соревнованиях {person['short_name']}",
        header_lines=[FEDERATION_NAME],
        meta=[("ФСП ID", person["fsp_id"]), ("Дата рождения", _d(person["birth_date"])),
              ("Муниципальное образование", person["municipality"]), ("Организация", person["organization"]),
              ("Дисциплины", ", ".join(person["discipline_labels"]) or "—"),
              ("Спортивный разряд", f"{rank['title']} (до {_d(rank['expires_at'])})" if rank and rank["expires_at"] else
               (rank["title"] if rank else "нет"))],
        blocks=[Table(["Дата", "Соревнование", "Код", "Статус", "Место", "Участников", "Результат", "Баллы"], rows,
                      title=f"Выступления: стартов — {perf.get('starts', 0)}, призовых мест — {perf.get('podiums', 0)}, "
                            f"побед — {perf.get('golds', 0)}, лучшее место — {perf.get('best_place') or '—'}",
                      widths=[1, 3.4, 1.8, 1.8, 0.8, 1, 2.2, 0.8], align=["center", "left", "left", "left", "center", "center", "left", "right"]),
                Signatures([("Руководитель регионального отделения", "")])],
        landscape=True, footer=f"Сформирован {_d(today)} по опубликованным протоколам соревнований. Баллы — по таблице рейтинга без коэффициента.")


# ---------------------------------------------------------------- реестр
def _person_rank(p: dict) -> str:
    r = p["rank"]
    if not r:
        return "—"
    return r["short"] + (f" до {_d(r['expires_at'])}" if r["expires_at"] else "")


def registry_doc(tab: str, tab_title: str, rows: list[dict], filter_text: str, today: date) -> Doc:
    if tab == "org":
        columns = ["№", "Организация", "Тип", "Муниципалитет", "Спортсменов", "Тренеров", "Судей", "Дисциплины"]
        data = [[i, o["name"], o["type"], o["municipality"], o["athletes"], o["trainers"], o["judges"],
                 ", ".join(o["discipline_labels"]) or "—"] for i, o in enumerate(rows, 1)]
        widths, align = [0.5, 3.6, 1.8, 1.5, 1, 0.9, 0.8, 2.6], ["center", "left", "left", "left", "right", "right", "right", "left"]
    else:
        columns = ["№", "ФИО", "ФСП ID", "Дата рождения", "Муниципалитет", "Организация", "Дисциплины"]
        if tab == "athlete":
            columns += ["Этап", "Разряд"]
        elif tab == "judge":
            columns += ["Категория"]
        data = []
        for i, p in enumerate(rows, 1):
            row = [i, p["full_name"], p["fsp_id"], _d(p["birth_date"]), p["municipality"], p["organization"],
                   ", ".join(p["discipline_labels"]) or "—"]
            if tab == "athlete":
                row += [p["training_stage"] or "—", _person_rank(p)]
            elif tab == "judge":
                jc = p["judge_category"]
                row += [f"{jc['code']} до {_d(jc['expires_at'])}" if jc else "—"]
            data.append(row)
        widths = [0.5, 2.8, 2, 1.1, 1.4, 2.8, 1.8] + ([0.7, 1.4] if tab == "athlete" else [1.4] if tab == "judge" else [])
        align = ["center", "left", "left", "center", "left", "left", "left"] + ["left"] * (len(columns) - 7)
    return Doc(
        title="РЕЕСТР", subtitle=tab_title.lower(), filename=f"Реестр — {tab_title.lower()} {today:%d.%m.%Y}",
        header_lines=[FEDERATION_NAME], meta=[("Сформирован", _d(today)), ("Фильтры", filter_text), ("Записей", str(len(rows)))],
        blocks=[Table(columns, data, widths=widths, align=align)], landscape=True,
        footer="Выгрузка из реестра Единой цифровой платформы ФСП Оренбургской области. Персональные данные в демо — фиктивные.")


# ---------------------------------------------------------------- журнал действий
def audit_doc(rows: list[dict], filter_text: str, today: date) -> Doc:
    data = [[a["at"].replace("T", " "), a["service"], a["actor"], a["action"], a["object"]] for a in rows]
    return Doc(
        title="ЖУРНАЛ ДЕЙСТВИЙ", subtitle="кто, когда и что сделал в платформе", filename=f"Журнал действий {today:%d.%m.%Y}",
        header_lines=[FEDERATION_NAME], meta=[("Сформирован", _d(today)), ("Фильтры", filter_text), ("Записей", str(len(rows)))],
        blocks=[Table(["Время", "Сервис", "Кто", "Действие", "Объект"], data, widths=[1.6, 1.1, 1.8, 2, 4.5],
                      align=["left", "left", "left", "left", "left"])], landscape=True,
        footer="Выгрузка журнала Единой цифровой платформы ФСП Оренбургской области.")

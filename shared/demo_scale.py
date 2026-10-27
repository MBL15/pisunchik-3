"""Синтетический объём для проверки работы с большими данными: `FSP_DEMO_SCALE=N` добавляет N вымышленных людей.

Генерация детерминирована (одинакова в обоих сервисах), поэтому competition_service может засеять заявки тех же людей.
Люди без email — войти под ними нельзя; в протоколы и рейтинг они не попадают. По умолчанию (0) ничего не добавляется.
"""
import random
from datetime import date

from shared import config
from shared.domain import DISCIPLINES, JUDGE_CATEGORIES, MUNICIPALITIES, ORGANIZATIONS, SPORT_RANKS
from shared.fsp_id import make_fsp_id

FIRST_SEQ = 1000  # с этого номера, чтобы не пересекаться с основным ростером и новыми регистрациями демо

_M_LAST = ["Иванов", "Смирнов", "Кузьмин", "Попов", "Васильев", "Петров", "Соловьёв", "Новиков", "Фёдоров", "Морозов",
           "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров", "Павлов", "Козлов", "Степанов", "Николаев", "Орлов",
           "Андреев", "Макаров", "Никитин", "Захаров", "Зайцев", "Соколов", "Борисов", "Яковлев", "Григорьев", "Романов",
           "Воробьёв", "Сергеев", "Кузнецов", "Фролов", "Александров", "Дмитриев", "Королёв", "Гусев", "Киселёв", "Ильин",
           "Максимов", "Поляков", "Сорокин", "Виноградов", "Ковалёв", "Белов", "Медведев", "Антонов", "Тарасов", "Жуков",
           "Баранов", "Филиппов", "Комаров", "Давыдов", "Беляев", "Герасимов", "Богданов", "Осипов", "Сидоров", "Матвеев",
           "Ахметов", "Галиев", "Сафин", "Исмаилов", "Каримов", "Юсупов", "Шарипов", "Нуриев", "Валиев", "Хабибуллин"]
_M_FIRST = ["Александр", "Дмитрий", "Максим", "Сергей", "Андрей", "Алексей", "Артём", "Илья", "Кирилл", "Михаил",
            "Никита", "Матвей", "Роман", "Егор", "Арсений", "Иван", "Денис", "Евгений", "Тимофей", "Владислав",
            "Игорь", "Павел", "Руслан", "Марк", "Тимур", "Ринат", "Арслан", "Данияр", "Лев", "Глеб"]
_F_FIRST = ["Анастасия", "Мария", "Дарья", "Анна", "Елизавета", "Полина", "Виктория", "Екатерина", "Софья", "Александра",
            "Варвара", "Ксения", "Алина", "Вероника", "Арина", "Ульяна", "Ева", "Кира", "Алёна", "Диана",
            "Милана", "Камила", "Амина", "Светлана", "Ольга", "Наталья", "Юлия", "Татьяна", "Ирина", "Елена"]
_PATRONYMIC = ["Александров", "Дмитриев", "Сергеев", "Андреев", "Алексеев", "Игорев", "Владимиров", "Николаев", "Олегов",
               "Викторов", "Евгеньев", "Павлов", "Михайлов", "Юрьев", "Романов", "Ильдаров", "Рустамов", "Маратов"]


def _female(last: str) -> str:
    if last.endswith(("ов", "ев", "ёв", "ин")):
        return last + "а"
    return last


def generate(n: int | None = None) -> list[dict]:
    """N синтетических людей в формате ростера (без `strength`)."""
    n = config.DEMO_SCALE if n is None else n
    if n <= 0:
        return []
    rnd = random.Random(56)
    today = config.today()
    disciplines = [d["code"] for d in DISCIPLINES]
    rank_codes = [r["code"] for r in SPORT_RANKS if r["code"] != "МС"]
    out = []
    for i in range(n):
        seq = FIRST_SEQ + i
        gender = "Ж" if rnd.random() < 0.38 else "М"
        last = rnd.choice(_M_LAST)
        roll = rnd.random()
        roles = (["athlete"] if roll < 0.85 else ["trainer"] if roll < 0.93 else ["judge"] if roll < 0.97
                 else ["trainer", "judge"])
        athlete = "athlete" in roles
        birth = (date(rnd.randint(2006, 2016), rnd.randint(1, 12), rnd.randint(1, 28)) if athlete
                 else date(rnd.randint(1968, 1999), rnd.randint(1, 12), rnd.randint(1, 28)))
        reg_year = rnd.choice([today.year - 1, today.year])
        start_year = max(birth.year + 9, min(reg_year, today.year - int(rnd.random() ** 2 * 6)))
        rank = rnd.choice(rank_codes[:6]) if athlete and rnd.random() < 0.45 else None
        judge = rnd.choice([c["code"] for c in JUDGE_CATEGORIES]) if "judge" in roles else None
        out.append({
            "seq": seq, "fsp_id": make_fsp_id(reg_year, seq),
            "last_name": _female(last) if gender == "Ж" else last,
            "first_name": rnd.choice(_F_FIRST if gender == "Ж" else _M_FIRST),
            "middle_name": rnd.choice(_PATRONYMIC) + ("на" if gender == "Ж" else "ич"),
            "gender": gender, "birth_date": birth.isoformat(),
            "municipality": rnd.choices(MUNICIPALITIES, weights=[40, 14, 8, 8, 6, 5, 4, 5, 5, 5])[0],
            "organization": rnd.choice(ORGANIZATIONS),
            "roles": roles, "disciplines": rnd.sample(disciplines, rnd.choice([1, 1, 1, 2])) if roles != ["judge"] else [],
            "rank_code": rank, "rank_expiry_offset": rnd.randint(-60, 700) if rank else None,
            "judge_category": judge, "judge_expiry_offset": rnd.randint(-30, 900) if judge else None,
            "start_year": start_year, "created_offset": -rnd.randint(0, 400),
        })
    return out


def age_on(birth: str, on: date) -> int:
    b = date.fromisoformat(birth)
    return on.year - b.year - ((on.month, on.day) < (b.month, b.day))


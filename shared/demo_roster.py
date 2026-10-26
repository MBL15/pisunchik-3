"""Deterministic demo roster (fictional persons only) used to seed both services' mock databases.

`strength` (0..1) per discipline drives the synthetic competition-log generator in
competition_service; it is never exposed through any API.
Date offsets are relative to config.today() so expiry alerts are always meaningful in a live demo.
"""
from shared.domain import ROLE_LABELS
from shared.fsp_id import make_fsp_id

DEMO_PASSWORD = "fsp2026"

# (seq, reg_year, last, first, middle, gender, birth, municipality, organization, roles, disciplines{code: strength},
#  rank_code, rank_expiry_offset_days, judge_category, judge_expiry_offset_days, email, sanction)
_ROWS = [
    (1, 2025, "Малахова", "Ольга", "Сергеевна", "Ж", "1986-04-12", "г. Оренбург", "Региональное отделение ФСП", ["admin", "judge"], {}, None, None, "1К", 210, "admin@fsp56.ru", None),
    (2, 2025, "Кузнецов", "Андрей", "Викторович", "М", "1984-09-03", "г. Оренбург", "ОГУ — Оренбургский государственный университет", ["trainer", "judge"], {"ALG": 0, "SEC": 0}, None, None, "2К", 18, "trainer@fsp56.ru", None),
    (3, 2025, "Соколов", "Дмитрий", "Алексеевич", "М", "2004-02-17", "г. Оренбург", "ОГУ — Оренбургский государственный университет", ["athlete", "trainer"], {"ALG": 0.93, "AI": 0.70}, "КМС", 240, None, None, "athlete@fsp56.ru", None),
    (4, 2025, "Ермакова", "Наталья", "Петровна", "Ж", "1979-06-21", "г. Орск", "ЦЦОД «IT-куб» г. Орск", ["trainer"], {"ROB": 0, "UAV": 0}, None, None, None, None, None, None),
    (5, 2025, "Тимофеев", "Игорь", "Владимирович", "М", "1990-01-30", "г. Оренбург", "Детский технопарк «Кванториум» г. Оренбург", ["trainer", "judge"], {"PRD": 0, "AI": 0}, None, None, "3К", 400, None, None),
    (6, 2025, "Белова", "Светлана", "Анатольевна", "Ж", "1982-03-08", "г. Бузулук", "Без организации", ["judge"], {}, None, None, "ВК", 520, None, None),
    (7, 2025, "Галиев", "Ринат", "Маратович", "М", "1988-12-14", "г. Оренбург", "Оренбургский колледж экономики и информатики", ["judge"], {}, None, None, "1К", -5, None, None),
    (8, 2025, "Абрамов", "Кирилл", "Сергеевич", "М", "2008-05-21", "г. Оренбург", "Лицей № 1 г. Оренбурга", ["athlete"], {"ALG": 0.88}, "1", 12, None, None, None, None),
    (9, 2025, "Валиева", "Алина", "Ренатовна", "Ж", "2009-11-02", "г. Оренбург", "Лицей № 1 г. Оренбурга", ["athlete"], {"ALG": 0.80, "SEC": 0.60}, "2", 55, None, None, None, None),
    (10, 2025, "Григорьев", "Максим", "Олегович", "М", "2007-01-14", "г. Орск", "ЦЦОД «IT-куб» г. Орск", ["athlete"], {"ALG": 0.85}, "1", -8, None, None, None, None),
    (11, 2025, "Дьяченко", "Полина", "Игоревна", "Ж", "2010-03-30", "г. Оренбург", "Детский технопарк «Кванториум» г. Оренбург", ["athlete"], {"ALG": 0.66}, "3Ю", 300, None, None, None, None),
    (12, 2025, "Жуков", "Артём", "Денисович", "М", "2006-07-08", "г. Новотроицк", "Без организации", ["athlete"], {"ALG": 0.91}, "КМС", 400, None, None, None,
     {"type": "DISQUALIFICATION", "from_offset": -30, "to_offset": 120, "reason": "Решение дисциплинарной комиссии: передача решений третьим лицам"}),
    (13, 2025, "Зайцева", "Виктория", "Андреевна", "Ж", "2005-12-19", "г. Оренбург", "ОГУ — Оренбургский государственный университет", ["athlete"], {"SEC": 0.90, "ALG": 0.72}, "КМС", 80, None, None, None, None),
    (14, 2025, "Исаев", "Тимур", "Рустамович", "М", "2006-04-04", "г. Оренбург", "Оренбургский колледж экономики и информатики", ["athlete"], {"SEC": 0.82}, "1", 500, None, None, None, None),
    (15, 2025, "Калинин", "Егор", "Павлович", "М", "2007-09-27", "г. Бузулук", "Без организации", ["athlete"], {"SEC": 0.74}, "2", 25, None, None, None, None),
    (16, 2025, "Лебедева", "Дарья", "Максимовна", "Ж", "2004-06-11", "г. Оренбург", "ОГУ — Оренбургский государственный университет", ["athlete"], {"PRD": 0.88, "AI": 0.80}, "1", 365, None, None, None, None),
    (17, 2025, "Морозов", "Никита", "Евгеньевич", "М", "2005-08-23", "г. Орск", "Орский гуманитарно-технологический институт (филиал ОГУ)", ["athlete"], {"PRD": 0.80}, "2", 140, None, None, None, None),
    (18, 2026, "Назарова", "Ксения", "Владимировна", "Ж", "2008-02-09", "г. Оренбург", "Детский технопарк «Кванториум» г. Оренбург", ["athlete"], {"PRD": 0.70, "AI": 0.72}, "3", 44, None, None, None, None),
    (19, 2026, "Орлов", "Илья", "Константинович", "М", "2003-10-05", "г. Оренбург", "ОГУ — Оренбургский государственный университет", ["athlete"], {"AI": 0.93}, "КМС", 600, None, None, None, None),
    (20, 2026, "Петрова", "Анастасия", "Романовна", "Ж", "2006-05-15", "г. Гай", "Без организации", ["athlete"], {"AI": 0.78}, "2", -30, None, None, None, None),
    (21, 2026, "Романов", "Глеб", "Артурович", "М", "2009-01-28", "г. Орск", "ЦЦОД «IT-куб» г. Орск", ["athlete"], {"ROB": 0.86, "UAV": 0.70}, "2Ю", 70, None, None, None, None),
    (22, 2026, "Степанова", "Ева", "Николаевна", "Ж", "2010-07-12", "г. Орск", "ЦЦОД «IT-куб» г. Орск", ["athlete"], {"ROB": 0.75}, "3Ю", 5, None, None, None, None),
    (23, 2026, "Тарасов", "Матвей", "Ильич", "М", "2008-12-03", "г. Медногорск", "Без организации", ["athlete"], {"ROB": 0.70, "UAV": 0.85}, "1Ю", 220, None, None, None, None),
    (24, 2026, "Ушаков", "Роман", "Дмитриевич", "М", "2007-03-17", "г. Оренбург", "Оренбургский колледж экономики и информатики", ["athlete"], {"UAV": 0.80}, "3", 180, None, None, None, None),
    (25, 2026, "Фёдорова", "Мария", "Алексеевна", "Ж", "2007-10-29", "г. Сорочинск", "Без организации", ["athlete"], {"UAV": 0.72, "ALG": 0.60}, None, None, None, None, None, None),
    (26, 2026, "Хасанов", "Арслан", "Ильдарович", "М", "2006-11-11", "г. Оренбург", "ОГУ — Оренбургский государственный университет", ["athlete"], {"ALG": 0.78, "SEC": 0.70}, "2", 330, None, None, None,
     {"type": "SUSPENSION", "from_offset": -150, "to_offset": -60, "reason": "Временное отстранение на период рассмотрения апелляции (снято)"}),
    (27, 2026, "Чернова", "Ульяна", "Сергеевна", "Ж", "2009-08-08", "г. Бугуруслан", "Без организации", ["athlete"], {"PRD": 0.66, "ALG": 0.55}, "3Ю", 400, None, None, None, None),
    (28, 2026, "Шевченко", "Лев", "Андреевич", "М", "2005-04-01", "г. Оренбург", "ОГУ — Оренбургский государственный университет", ["athlete"], {"AI": 0.84, "PRD": 0.75}, "1", 88, None, None, None, None),
]

ROSTER: list[dict] = [
    {
        "seq": r[0], "fsp_id": make_fsp_id(r[1], r[0]), "last_name": r[2], "first_name": r[3], "middle_name": r[4],
        "gender": r[5], "birth_date": r[6], "municipality": r[7], "organization": r[8], "roles": r[9],
        "disciplines": list(r[10].keys()), "strength": r[10], "rank_code": r[11], "rank_expiry_offset": r[12],
        "judge_category": r[13], "judge_expiry_offset": r[14], "email": r[15], "sanction": r[16],
    }
    for r in _ROWS
]
ROSTER_BY_SEQ: dict[int, dict] = {p["seq"]: p for p in ROSTER}

# One demo login per scenario (all share DEMO_PASSWORD). `role` = active role after quick login, `check` = UI marker
# the e2e smoke test expects to see in that cabinet.
DEMO_ACCOUNTS: list[dict] = [
    {"group": "Сквозные сценарии кейса", "seq": 1, "email": "admin@fsp56.ru", "role": "admin",
     "title": "Федерация", "hint": "Единые входящие: заявки на старты, разряды и судейские категории; судейские коллегии; протоколы; рейтинг; справка с QR",
     "check": "входящие · требуют решения"},
    {"group": "Сквозные сценарии кейса", "seq": 2, "email": "trainer@fsp56.ru", "role": "trainer",
     "title": "Тренер", "hint": "Бейджи истечения разрядов (истёк / ≤30 / ≤90 дн.) и заявка на подтверждение разряда прямо из карточки подопечного",
     "check": "Сроки действия разрядов подопечных"},
    {"group": "Сквозные сценарии кейса", "seq": 3, "email": "athlete@fsp56.ru", "role": "athlete",
     "title": "Спортсмен + Тренер", "hint": "Мультироль: лидер рейтинга с ×1.2 (222.0), история выступлений, справка PDF; переключитесь на «Тренер»",
     "check": "История выступлений"},
    {"group": "Спортсмен", "seq": 8, "email": "abramov@fsp56.ru", "role": "athlete",
     "title": "Разряд истекает, DSQ в протоколе", "hint": "I разряд истекает через 12 дн.; дисквалификация на «Волга-Код» лишила его ×1.2; заявка на модерации",
     "check": "истекает"},
    {"group": "Спортсмен", "seq": 9, "email": "valieva@fsp56.ru", "role": "athlete",
     "title": "Несовершеннолетняя спортсменка", "hint": "16 лет: согласие законного представителя на обработку ПДн, ×1.2 в рейтинге сезона",
     "check": "законный представитель"},
    {"group": "Спортсмен", "seq": 26, "email": "hasanov@fsp56.ru", "role": "athlete",
     "title": "Запрос тренера на привязку", "hint": "Входящий запрос от тренера Кузнецова — подтвердите или отклоните связь в кабинете",
     "check": "запрос на привязку"},
    {"group": "Спортсмен", "seq": 20, "email": "petrova@fsp56.ru", "role": "athlete",
     "title": "Без тренера, разряд истёк", "hint": "Самостоятельный путь: II разряд истёк 30 дн. назад — заявка на подтверждение по результату сезона в один шаг",
     "check": "Подать на подтверждение"},
    {"group": "Спортсмен", "seq": 12, "email": "zhukov@fsp56.ru", "role": "athlete",
     "title": "Действующая дисквалификация", "hint": "Исключён из рейтинга несмотря на 3 призовых места; подача заявок заблокирована",
     "check": "Действующая дисквалификация"},
    {"group": "Тренер", "seq": 4, "email": "ermakova@fsp56.ru", "role": "trainer",
     "title": "Тренер робототехники и БАС", "hint": "г. Орск: разряд подопечной истекает через 5 дн., заявка на Кубок по робототехнике",
     "check": "Мои спортсмены"},
    {"group": "Судья", "seq": 6, "email": "judge@fsp56.ru", "role": "judge",
     "title": "Судья всероссийской категории", "hint": "Назначения главным судьёй и главным секретарём, история судейства",
     "check": "Назначения"},
    {"group": "Судья", "seq": 7, "email": "galiev@fsp56.ru", "role": "judge",
     "title": "Судейская категория истекла", "hint": "Категория 1К истекла — подтвердите её в один клик, иначе федерация не назначит в коллегию",
     "check": "Подтвердить категорию"},
    {"group": "Судья", "seq": 2, "email": "trainer@fsp56.ru", "role": "judge",
     "title": "Тренер в роли судьи", "hint": "Тот же аккаунт Кузнецова: роль «Судья» без повторного входа, категория истекает через 18 дн.",
     "check": "истекает"},
]
for _acc in DEMO_ACCOUNTS:
    _p = ROSTER_BY_SEQ[_acc["seq"]]
    _p["email"] = _acc["email"]
    _acc["name"] = f"{_p['last_name']} {_p['first_name'][0]}. {_p['middle_name'][0]}."
    _acc["roles"] = _p["roles"]
    _acc["roles_label"] = " + ".join(ROLE_LABELS[r] for r in _p["roles"])

# (trainer_seq, athlete_seq, discipline, status)
TRAINER_LINKS = [
    (2, 8, "ALG", "ACTIVE"), (2, 9, "ALG", "ACTIVE"), (2, 10, "ALG", "ACTIVE"), (2, 11, "ALG", "ACTIVE"),
    (2, 13, "SEC", "ACTIVE"), (2, 14, "SEC", "ACTIVE"), (2, 15, "SEC", "SUSPENDED"), (2, 26, "ALG", "PENDING"),
    (3, 27, "ALG", "ACTIVE"), (3, 25, "ALG", "ACTIVE"),
    (4, 21, "ROB", "ACTIVE"), (4, 22, "ROB", "ACTIVE"), (4, 23, "UAV", "ACTIVE"), (4, 24, "UAV", "ACTIVE"),
    (5, 16, "PRD", "ACTIVE"), (5, 18, "PRD", "ACTIVE"), (5, 18, "AI", "ACTIVE"), (5, 19, "AI", "ACTIVE"), (5, 28, "AI", "ACTIVE"),
]


def full_name(p: dict) -> str:
    return f"{p['last_name']} {p['first_name']} {p['middle_name']}".strip()


def athletes_in(discipline: str) -> list[dict]:
    return [p for p in ROSTER if "athlete" in p["roles"] and discipline in p["strength"]]

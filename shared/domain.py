"""Domain dictionaries (справочники) shared by all services. Russian localisation lives here."""
from datetime import date
from enum import StrEnum

REGION_CODE = "56"
REGION_NAME = "Оренбургская область"
FEDERATION_NAME = "Региональное отделение Федерации спортивного программирования Оренбургской области"
FEDERATION_SHORT = "ФСП · Оренбургская область"


class Role(StrEnum):
    ADMIN = "admin"
    ATHLETE = "athlete"
    TRAINER = "trainer"
    JUDGE = "judge"
    MINISTRY = "ministry"  # министерство спорта: только чтение (отчёты, календарь, сборная); выдаётся федерацией


ROLE_LABELS: dict[str, str] = {
    Role.ADMIN: "Федерация",
    Role.ATHLETE: "Спортсмен",
    Role.TRAINER: "Тренер",
    Role.JUDGE: "Судья",
    Role.MINISTRY: "Министерство спорта",
}

# Roles a person may self-assign at public registration (admin is granted by the federation only).
SELF_SERVICE_ROLES = (Role.ATHLETE, Role.TRAINER, Role.JUDGE)

DISCIPLINES: list[dict] = [
    {
        "code": "ALG",
        "title": "Программирование алгоритмическое",
        "short": "Алгоритмика",
        "icon": "code",
        "cmd": "solve --tasks=A..H --time=5h",
        "description": "Олимпиадные задачи на время: структуры данных, графы, динамика. Решение проверяет автоматическая система.",
        "skills": ["C++", "Python", "Графы", "ДП"],
    },
    {
        "code": "PRD",
        "title": "Программирование продуктовое",
        "short": "Продукт",
        "icon": "package",
        "cmd": "build --mvp --pitch",
        "description": "Командная разработка цифрового продукта под кейс заказчика: от идеи и прототипа до защиты перед жюри.",
        "skills": ["Web", "Mobile", "UX", "Pitch"],
    },
    {
        "code": "SEC",
        "title": "Программирование систем информационной безопасности",
        "short": "Кибербезопасность",
        "icon": "shield",
        "cmd": "ctf --mode=attack-defense",
        "description": "CTF-соревнования: поиск уязвимостей, реверс, криптография, защита инфраструктуры в формате Attack-Defense.",
        "skills": ["Pwn", "Web", "Crypto", "Forensics"],
    },
    {
        "code": "AI",
        "title": "Программирование систем искусственного интеллекта",
        "short": "Искусственный интеллект",
        "icon": "cpu",
        "cmd": "train --metric=f1 --leaderboard",
        "description": "Соревнования по машинному обучению: подготовка данных, обучение моделей, борьба за метрику на лидерборде.",
        "skills": ["ML", "CV", "NLP", "Data"],
    },
    {
        "code": "ROB",
        "title": "Программирование робототехники",
        "short": "Робототехника",
        "icon": "bot",
        "cmd": "deploy --robot --track=final",
        "description": "Программирование автономных роботов: навигация, датчики, прохождение трасс и выполнение миссий на полигоне.",
        "skills": ["ROS", "C", "Сенсоры", "SLAM"],
    },
    {
        "code": "UAV",
        "title": "Программирование беспилотных авиационных систем",
        "short": "Беспилотники",
        "icon": "drone",
        "cmd": "fly --autonomous --mission=7",
        "description": "Автономные полёты БАС: компьютерное зрение, планирование маршрута, выполнение миссий без участия пилота.",
        "skills": ["PX4", "OpenCV", "Python", "Навигация"],
    },
]
DISCIPLINE_BY_CODE: dict[str, dict] = {d["code"]: d for d in DISCIPLINES}

# Режим протокола: ICPC (решённые задачи + штраф) или сумма баллов жюри.
ICPC_DISCIPLINES = {"ALG", "SEC"}


def protocol_mode(discipline: str) -> str:
    return "icpc" if discipline in ICPC_DISCIPLINES else "score"

# Дисциплины, где можно собрать команду (алгоритмика, продуктовая, CTF, ИИ): спортсмен сам собирает команду. Размер — рабочее значение, сверить с положением.
TEAM_DISCIPLINES = ("ALG", "PRD", "SEC", "AI")
TEAM_MAX_SIZE = 5

# ЕВСК: спортивные разряды и звания (по возрастанию). validity_years=None — бессрочно (звание).
SPORT_RANKS: list[dict] = [
    {"code": "3Ю", "title": "III юношеский спортивный разряд", "short": "III юн.", "validity_years": 2},
    {"code": "2Ю", "title": "II юношеский спортивный разряд", "short": "II юн.", "validity_years": 2},
    {"code": "1Ю", "title": "I юношеский спортивный разряд", "short": "I юн.", "validity_years": 2},
    {"code": "3", "title": "III спортивный разряд", "short": "III разряд", "validity_years": 2},
    {"code": "2", "title": "II спортивный разряд", "short": "II разряд", "validity_years": 2},
    {"code": "1", "title": "I спортивный разряд", "short": "I разряд", "validity_years": 2},
    {"code": "КМС", "title": "Кандидат в мастера спорта", "short": "КМС", "validity_years": 3},
    {"code": "МС", "title": "Мастер спорта России", "short": "МС", "validity_years": None},
]
RANK_BY_CODE: dict[str, dict] = {r["code"]: r for r in SPORT_RANKS}
RANK_ORDER: dict[str, int] = {r["code"]: i for i, r in enumerate(SPORT_RANKS)}

JUDGE_CATEGORIES: list[dict] = [
    {"code": "ЮС", "title": "Юный спортивный судья"},
    {"code": "3К", "title": "Спортивный судья третьей категории"},
    {"code": "2К", "title": "Спортивный судья второй категории"},
    {"code": "1К", "title": "Спортивный судья первой категории"},
    {"code": "ВК", "title": "Спортивный судья всероссийской категории"},
]
JUDGE_CATEGORY_BY_CODE: dict[str, dict] = {c["code"]: c for c in JUDGE_CATEGORIES}

JUDGE_POSITIONS = ["Главный судья", "Главный секретарь", "Заместитель главного судьи", "Судья", "Технический судья"]


class CompetitionLevel(StrEnum):
    CHAMP_RU = "CHAMP_RU"
    FIRST_RU = "FIRST_RU"
    INTERREG = "INTERREG"
    REGIONAL = "REGIONAL"


LEVEL_LABELS: dict[str, str] = {
    CompetitionLevel.CHAMP_RU: "Чемпионат России",
    CompetitionLevel.FIRST_RU: "Первенство России",
    CompetitionLevel.INTERREG: "Межрегиональные (в т.ч. окружные)",
    CompetitionLevel.REGIONAL: "Региональные",
}
LEVEL_STATUS_LABELS: dict[str, str] = {
    CompetitionLevel.CHAMP_RU: "всероссийское",
    CompetitionLevel.FIRST_RU: "всероссийское",
    CompetitionLevel.INTERREG: "межрегиональное",
    CompetitionLevel.REGIONAL: "региональное",
}

# Требования ЕВСК к результату: разряд -> [(уровень соревнования, место не ниже, участников не менее)].
# ВНИМАНИЕ: пороги рабочие, масштабированы под размеры протоколов демо-данных. Перед боевым использованием их нужно
# сверить с действующей таблицей ЕВСК для вида спорта (приказ Минспорта России) — править только эту таблицу.
# Звание МС присваивается по отдельной процедуре и автоматически не подсвечивается.
EVSK_REQUIREMENTS: dict[str, list[tuple[str, int, int]]] = {
    "3Ю": [(CompetitionLevel.REGIONAL, 8, 6), (CompetitionLevel.INTERREG, 12, 8)],
    "2Ю": [(CompetitionLevel.REGIONAL, 6, 6), (CompetitionLevel.INTERREG, 9, 8), (CompetitionLevel.FIRST_RU, 14, 10)],
    "1Ю": [(CompetitionLevel.REGIONAL, 4, 6), (CompetitionLevel.INTERREG, 6, 8), (CompetitionLevel.FIRST_RU, 10, 10)],
    "3": [(CompetitionLevel.REGIONAL, 3, 6), (CompetitionLevel.INTERREG, 5, 8), (CompetitionLevel.FIRST_RU, 8, 10),
          (CompetitionLevel.CHAMP_RU, 12, 12)],
    "2": [(CompetitionLevel.REGIONAL, 2, 8), (CompetitionLevel.INTERREG, 4, 10), (CompetitionLevel.FIRST_RU, 6, 12),
          (CompetitionLevel.CHAMP_RU, 10, 12)],
    "1": [(CompetitionLevel.REGIONAL, 1, 8), (CompetitionLevel.INTERREG, 3, 10), (CompetitionLevel.FIRST_RU, 4, 12),
          (CompetitionLevel.CHAMP_RU, 8, 14)],
    "КМС": [(CompetitionLevel.INTERREG, 1, 12), (CompetitionLevel.FIRST_RU, 3, 14), (CompetitionLevel.CHAMP_RU, 6, 16)],
}

# Рекомендуемые базовые значения кейса (федерация может изменить в настройках): баллы за 1/2/3 место.
DEFAULT_POINTS_TABLE: dict[str, list[int]] = {
    CompetitionLevel.CHAMP_RU: [100, 80, 60],
    CompetitionLevel.FIRST_RU: [80, 60, 40],
    CompetitionLevel.INTERREG: [60, 45, 30],
    CompetitionLevel.REGIONAL: [40, 30, 20],
}
DEFAULT_STABILITY_COEFFICIENT = 1.2
DEFAULT_STABILITY_MIN_PODIUMS = 2


class CalendarPlan(StrEnum):
    EKP = "EKP"
    REGIONAL = "REGIONAL"


CALENDAR_PLAN_LABELS: dict[str, str] = {
    CalendarPlan.EKP: "ЕКП Минспорта России",
    CalendarPlan.REGIONAL: "Календарный план Оренбургской области",
}


class CompetitionFormat(StrEnum):
    OFFLINE = "OFFLINE"
    ONLINE = "ONLINE"
    HYBRID = "HYBRID"


FORMAT_LABELS: dict[str, str] = {
    CompetitionFormat.OFFLINE: "Очно",
    CompetitionFormat.ONLINE: "Онлайн",
    CompetitionFormat.HYBRID: "Смешанный",
}

# Стартовое наполнение справочника типов; дальше его пополняет федерация (competition_service, /api/v1/competition-types).
DEFAULT_COMPETITION_TYPES = ["Чемпионат", "Первенство", "Кубок", "Турнир"]


class CompetitionStatus(StrEnum):
    PLANNED = "PLANNED"
    REGISTRATION_OPEN = "REGISTRATION_OPEN"
    REGISTRATION_CLOSED = "REGISTRATION_CLOSED"
    LIVE = "LIVE"
    FINISHED = "FINISHED"


COMPETITION_STATUS_LABELS: dict[str, str] = {
    CompetitionStatus.PLANNED: "Запланировано",
    CompetitionStatus.REGISTRATION_OPEN: "Приём заявок",
    CompetitionStatus.REGISTRATION_CLOSED: "Приём закрыт",
    CompetitionStatus.LIVE: "Идёт сейчас",
    CompetitionStatus.FINISHED: "Завершено",
}

AGE_GROUPS: dict[str, tuple[int, int]] = {
    "мальчики/девочки 10–13 лет": (10, 13),
    "юноши/девушки 14–17 лет": (14, 17),
    "юниоры 16–21 год": (16, 21),
    "мужчины/женщины 18+": (18, 99),
    "без ограничений 12+": (12, 99),
}


def age_on(birth: "date | str", on: "date | str") -> int:
    """Полных лет на дату `on`; даты — `date` или ISO-строка."""
    birth, on = (date.fromisoformat(v) if isinstance(v, str) else v for v in (birth, on))
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


def age_group_fit(birth: "date | str", group: str, start: "date | str") -> tuple[int, bool]:
    """(возраст, подходит ли группе) на дату начала соревнования, а не на сегодня: допуск определяется возрастом к старту."""
    age = age_on(birth, start)
    lo, hi = AGE_GROUPS[group]
    return age, lo <= age <= hi


class LinkStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    ENDED = "ENDED"


LINK_STATUS_LABELS: dict[str, str] = {
    LinkStatus.PENDING: "Ожидает подтверждения",
    LinkStatus.ACTIVE: "Активна",
    LinkStatus.SUSPENDED: "Приостановлена",
    LinkStatus.ENDED: "Завершена",
}


class ApplicationStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


APPLICATION_STATUS_LABELS: dict[str, str] = {
    ApplicationStatus.PENDING: "На модерации",
    ApplicationStatus.APPROVED: "Одобрена",
    ApplicationStatus.REJECTED: "Отклонена",
}

TEAM_STATUS_LABELS = {"DRAFT": "Состав формируется", "APPROVED": "Утверждена приказом"}
TEAM_ROLE_LABELS = {"MAIN": "Основной состав", "RESERVE": "Резерв"}
# Проект приказа о сборной готовит федерация; издаёт его профильное министерство региона.
ORDER_ISSUER = "Министерство физической культуры и спорта Оренбургской области"

SANCTION_LABELS = {"DISQUALIFICATION": "Дисквалификация", "SUSPENSION": "Отстранение"}

# Заявки на присвоение/подтверждение спортивного разряда и судейской категории (статусы — ApplicationStatus).
QUALIFICATION_KIND_LABELS = {"RANK": "Спортивный разряд", "JUDGE_CATEGORY": "Судейская категория"}
QUALIFICATION_PURPOSE_LABELS = {"ASSIGN": "присвоение", "CONFIRM": "подтверждение"}
QUALIFICATION_STATUS_LABELS = {
    ApplicationStatus.PENDING: "На рассмотрении",
    ApplicationStatus.APPROVED: "Удовлетворена",
    ApplicationStatus.REJECTED: "Отклонена",
}

# Возрастные группы отчёта о численности (не пересекаются; возраст — на отчётную дату).
HEADCOUNT_AGE_BANDS: list[tuple[str, int, int]] = [
    ("до 10 лет", 0, 9), ("10–13 лет", 10, 13), ("14–17 лет", 14, 17), ("18–21 год", 18, 21), ("22 года и старше", 22, 200),
]
GENDER_LABELS = {"М": "Мужской", "Ж": "Женский"}

MUNICIPALITIES = [
    "г. Оренбург", "г. Орск", "г. Новотроицк", "г. Бузулук", "г. Бугуруслан",
    "г. Гай", "г. Медногорск", "г. Сорочинск", "Кувандыкский г.о.", "Ясненский г.о.",
]

ORGANIZATIONS = [
    "ОГУ — Оренбургский государственный университет",
    "Детский технопарк «Кванториум» г. Оренбург",
    "ЦЦОД «IT-куб» г. Орск",
    "Лицей № 1 г. Оренбурга",
    "Орский гуманитарно-технологический институт (филиал ОГУ)",
    "Оренбургский колледж экономики и информатики",
    "Без организации",
]
NO_ORGANIZATION = "Без организации"
CLUB_TYPE = "Клуб"
_club_names: set[str] = set()


def sync_clubs(clubs: list[dict]) -> None:
    """Клубы, созданные тренерами, живут в данных сервиса, а не в константах: добавляет их в общие списки организаций
    (и убирает исчезнувшие — важно для тестов и перезапуска). Вызывается сервисом реестра при запуске и после создания клуба."""
    wanted = {c["name"]: c for c in clubs}
    for name in _club_names - set(wanted):
        if name in ORGANIZATIONS:
            ORGANIZATIONS.remove(name)
        ORGANIZATION_INFO.pop(name, None)
    _club_names.clear()
    for name, c in wanted.items():
        if name not in ORGANIZATIONS:
            ORGANIZATIONS.insert(max(len(ORGANIZATIONS) - 1, 0), name)  # «Без организации» остаётся последней
        ORGANIZATION_INFO[name] = {"type": CLUB_TYPE, "municipality": c["municipality"]}
        _club_names.add(name)

# Карточки организаций реестра: тип (школа, вуз, спортивная школа, клуб и т.п.) и муниципалитет.
ORGANIZATION_INFO: dict[str, dict] = {
    "ОГУ — Оренбургский государственный университет": {"type": "Вуз", "municipality": "г. Оренбург"},
    "Детский технопарк «Кванториум» г. Оренбург": {"type": "Организация доп. образования", "municipality": "г. Оренбург"},
    "ЦЦОД «IT-куб» г. Орск": {"type": "Организация доп. образования", "municipality": "г. Орск"},
    "Лицей № 1 г. Оренбурга": {"type": "Школа", "municipality": "г. Оренбург"},
    "Орский гуманитарно-технологический институт (филиал ОГУ)": {"type": "Вуз", "municipality": "г. Орск"},
    "Оренбургский колледж экономики и информатики": {"type": "Колледж", "municipality": "г. Оренбург"},
}

# Этапы спортивной подготовки (ФССП по виду спорта); у спортсмена этап указывается при наличии.
TRAINING_STAGES: dict[str, str] = {
    "НП": "Этап начальной подготовки",
    "УТ": "Учебно-тренировочный этап",
    "ССМ": "Этап совершенствования спортивного мастерства",
    "ВСМ": "Этап высшего спортивного мастерства",
}

# Ежегодные сроки отчётности федерации: (месяц, день, что сдать, кому). Видны федерации в «Контроле сроков».
REPORT_DEADLINES: list[tuple[int, int, str, str]] = [
    (1, 20, "Отчёт о численности занимающихся за год", "в министерство спорта области"),
    (2, 1, "Ходатайства на премии Губернатора", "по Указу № 93-ук"),
    (6, 30, "Отчёт о развитии вида спорта за полугодие", "в министерство спорта области"),
    (11, 1, "Предложения в календарный план на следующий год", "ЕКП и план области"),
]


def report_deadlines(today) -> list[dict]:
    """Ближайшая дата каждого отчётного срока (сегодняшний ещё не прошёл), по возрастанию."""
    out = []
    for month, day, title, note in REPORT_DEADLINES:
        due = today.replace(month=month, day=day)
        if due < today:
            due = due.replace(year=today.year + 1)
        out.append({"title": title, "note": note, "date": due.isoformat(), "days_left": (due - today).days})
    return sorted(out, key=lambda d: d["days_left"])

# Пороги предупреждений об истечении разряда/категории (дни).
EXPIRY_CRITICAL_DAYS = 30
EXPIRY_WARNING_DAYS = 90


def expiry_state(days_left: int | None) -> str:
    """Classify a validity window: none | expired | critical | warning | ok."""
    if days_left is None:
        return "none"
    if days_left < 0:
        return "expired"
    if days_left <= EXPIRY_CRITICAL_DAYS:
        return "critical"
    if days_left <= EXPIRY_WARNING_DAYS:
        return "warning"
    return "ok"

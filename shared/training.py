"""Д4. Нормативы и аттестация по ФССП (приказ Минспорта России от 24.06.2025 № 445): чистая логика и стартовый справочник.

Норматив: этап подготовки × дисциплина (или «общий») × пол → порог и направление сравнения. Платформа сама решает, выполнен ли норматив,
и готов ли спортсмен к переводу: на его этапе у каждого обязательного применимого норматива последняя попытка — зачётная.

ВНИМАНИЕ: пороги рабочие, подобраны под демонстрацию. Перед боевым использованием их нужно сверить с приказом № 445 —
федерация правит справочник в интерфейсе (`/training/norms`), код менять не нужно.
"""
from shared.domain import TRAINING_STAGES

STAGE_ORDER = list(TRAINING_STAGES)  # НП → УТ → ССМ → ВСМ
GENERAL, SPECIAL = "GENERAL", "SPECIAL"
KIND_LABELS = {GENERAL: "ОФП (общая физическая подготовка)", SPECIAL: "СФП (специальная подготовка)"}
DIRECTION_LABELS = {"MIN": "не меньше", "MAX": "не больше"}


def next_stage(stage: str | None) -> str | None:
    if stage not in STAGE_ORDER:
        return None
    i = STAGE_ORDER.index(stage) + 1
    return STAGE_ORDER[i] if i < len(STAGE_ORDER) else None


def evaluate(direction: str, threshold: float, value: float) -> bool:
    """Норматив выполнен: результат не хуже порога (для времени и ошибок — не больше, для скорости и баллов — не меньше)."""
    return value >= threshold if direction == "MIN" else value <= threshold


def applies(norm: dict, stage: str | None, disciplines: list[str], gender: str) -> bool:
    return bool(norm.get("active", True) and stage and norm["stage"] == stage
                and norm["discipline"] in ("ALL", *disciplines) and norm["gender"] in ("ALL", gender))


def latest_by_norm(results: list[dict]) -> dict[int, dict]:
    """Последняя попытка по каждому нормативу (по дате, затем по номеру записи)."""
    out: dict[int, dict] = {}
    for r in sorted(results, key=lambda r: (r["date"], r["id"])):
        out[r["norm_id"]] = r
    return out


# (название, вид, дисциплина, пол, единица, направление, пороги по этапам НП / УТ / ССМ / ВСМ)
_STAGES = tuple(STAGE_ORDER)
DEFAULT_NORMS: list[tuple[str, str, str, str, str, str, tuple[float, float, float, float]]] = [
    ("Скорость печати на клавиатуре", SPECIAL, "ALL", "ALL", "зн/мин", "MIN", (120, 180, 240, 300)),
    ("Бег 30 м", GENERAL, "ALL", "М", "с", "MAX", (6.5, 6.0, 5.6, 5.3)),
    ("Бег 30 м", GENERAL, "ALL", "Ж", "с", "MAX", (7.0, 6.5, 6.1, 5.8)),
    ("Прыжок в длину с места", GENERAL, "ALL", "М", "см", "MIN", (150, 170, 190, 205)),
    ("Прыжок в длину с места", GENERAL, "ALL", "Ж", "см", "MIN", (130, 150, 165, 180)),
    ("Задачи уровня B за 60 минут", SPECIAL, "ALG", "ALL", "задач", "MIN", (1, 2, 3, 4)),
    ("Задания CTF за 60 минут", SPECIAL, "SEC", "ALL", "заданий", "MIN", (1, 2, 3, 4)),
    ("Метрика F1 на учебном датасете", SPECIAL, "AI", "ALL", "F1", "MIN", (0.60, 0.70, 0.80, 0.88)),
    ("Прототип по кейсу: оценка жюри", SPECIAL, "PRD", "ALL", "баллов", "MIN", (50, 60, 70, 80)),
    ("Прохождение учебной трассы", SPECIAL, "ROB", "ALL", "с", "MAX", (120, 90, 70, 55)),
    ("Полёт по миссии: оценка жюри", SPECIAL, "UAV", "ALL", "баллов", "MIN", (50, 60, 70, 80)),
]


def default_norm_rows() -> list[dict]:
    rows = []
    for title, kind, discipline, gender, unit, direction, thresholds in DEFAULT_NORMS:
        for stage, threshold in zip(_STAGES, thresholds):
            rows.append({"title": title, "kind": kind, "stage": stage, "discipline": discipline, "gender": gender, "unit": unit,
                         "direction": direction, "threshold": float(threshold), "required": True, "active": True, "note": ""})
    return rows

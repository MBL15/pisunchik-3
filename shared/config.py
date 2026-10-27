import os
from datetime import date
from pathlib import Path


def _load_dotenv() -> None:
    """Подхватывает KEY=VALUE из .env в корне проекта; уже заданные переменные окружения не затирает."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

GATEWAY_PORT = int(os.getenv("FSP_GATEWAY_PORT", "8000"))
SPORTSMAN_PORT = int(os.getenv("FSP_SPORTSMAN_PORT", "8001"))
COMPETITION_PORT = int(os.getenv("FSP_COMPETITION_PORT", "8002"))

HOST = os.getenv("FSP_HOST", "127.0.0.1")

SPORTSMAN_URL = os.getenv("FSP_SPORTSMAN_URL", f"http://{HOST}:{SPORTSMAN_PORT}")
COMPETITION_URL = os.getenv("FSP_COMPETITION_URL", f"http://{HOST}:{COMPETITION_PORT}")
PUBLIC_BASE_URL = os.getenv("FSP_PUBLIC_BASE_URL", f"http://localhost:{GATEWAY_PORT}")

# Development-only default. Override FSP_SECRET_KEY in any shared/deployed environment.
SECRET_KEY = os.getenv("FSP_SECRET_KEY", "dev-only-fsp56-change-me")

TELEGRAM_TOKEN = os.getenv("FSP_TELEGRAM_TOKEN", "")
TELEGRAM_BOT = os.getenv("FSP_TELEGRAM_BOT", "")

# Postgres, например postgresql://fsp:fsp@localhost:5432/fsp. Пусто — состояние только в памяти (как раньше).
DATABASE_URL = os.getenv("FSP_DATABASE_URL", "")

# Синтетический объём для проверки больших списков: N вымышленных людей и их заявок (shared/demo_scale.py). 0 — выключено.
DEMO_SCALE = int(os.getenv("FSP_DEMO_SCALE", "0") or 0)

SERVICE_TIMEOUT =float(os.getenv("FSP_SERVICE_TIMEOUT", "5.0"))

# TTF-шрифт с кириллицей для выгрузки в PDF; по умолчанию ищется Arial / DejaVu / Liberation в системных каталогах.
PDF_FONT = os.getenv("FSP_PDF_FONT", "")
PDF_FONT_BOLD = os.getenv("FSP_PDF_FONT_BOLD", "")


def today() -> date:
    """Single source of 'now' so demos can be pinned via FSP_DEMO_TODAY=YYYY-MM-DD."""
    pinned = os.getenv("FSP_DEMO_TODAY")
    return date.fromisoformat(pinned) if pinned else date.today()

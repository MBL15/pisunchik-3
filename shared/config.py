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
MARKET_PORT = int(os.getenv("FSP_MARKET_PORT", "8003"))

HOST = os.getenv("FSP_HOST", "127.0.0.1")

SPORTSMAN_URL = os.getenv("FSP_SPORTSMAN_URL", f"http://{HOST}:{SPORTSMAN_PORT}")
COMPETITION_URL = os.getenv("FSP_COMPETITION_URL", f"http://{HOST}:{COMPETITION_PORT}")
MARKET_URL = os.getenv("FSP_MARKET_URL", f"http://{HOST}:{MARKET_PORT}")
GATEWAY_URL = os.getenv("FSP_GATEWAY_URL", f"http://{HOST}:{GATEWAY_PORT}")  # как шлюз виден сервисам (вебхуки на демо-приёмник)
PUBLIC_BASE_URL = os.getenv("FSP_PUBLIC_BASE_URL", f"http://localhost:{GATEWAY_PORT}")

# Development-only default. Override FSP_SECRET_KEY in any shared/deployed environment.
DEV_SECRET_KEY = "dev-only-fsp56-change-me"
SECRET_KEY = os.getenv("FSP_SECRET_KEY", DEV_SECRET_KEY)
if SECRET_KEY == DEV_SECRET_KEY and not PUBLIC_BASE_URL.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]")):
    # Ключ из репозитория известен всем: с ним можно подделать cookie сессии любого пользователя, включая администратора.
    raise RuntimeError("FSP_SECRET_KEY не задан, а FSP_PUBLIC_BASE_URL указывает не на localhost — задайте собственный секрет")

# Служебный токен шлюза для внутренних сервисов (заголовок X-FSP-Service-Token). Пусто — проверка выключена (локальный запуск).
SERVICE_TOKEN = os.getenv("FSP_SERVICE_TOKEN", "")

# Флаг Secure у cookie сессии: по умолчанию включается, если публичный адрес — https.
_cookie_secure = os.getenv("FSP_COOKIE_SECURE")
COOKIE_SECURE = PUBLIC_BASE_URL.startswith("https://") if _cookie_secure is None else _cookie_secure.lower() in ("1", "true", "yes")

TELEGRAM_TOKEN = os.getenv("FSP_TELEGRAM_TOKEN", "")
TELEGRAM_BOT = os.getenv("FSP_TELEGRAM_BOT", "")
# Прокси для api.telegram.org, если хостинг его блокирует: socks5://host:port или http://host:port (может содержать логин:пароль)
TELEGRAM_PROXY = os.getenv("FSP_TELEGRAM_PROXY", "")

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

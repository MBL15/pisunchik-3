"""Boot the whole FSP Orenburg platform: sportsman (8001) + competition (8002) → gateway (8000).

    py run_all.py              # live-reload for every service (default)
    py run_all.py --no-reload  # plain processes

Each service seeds its own in-memory mock database on startup. Output of all services is multiplexed
into this console with coloured prefixes. Ctrl+C stops the whole process tree.
"""
import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from shared import config  # noqa: E402
from shared.demo_roster import DEMO_ACCOUNTS, DEMO_PASSWORD  # noqa: E402

SERVICES = [
    ("sportsman_service", config.SPORTSMAN_PORT, "\033[92m"),
    ("competition_service", config.COMPETITION_PORT, "\033[96m"),
    ("gateway_service", config.GATEWAY_PORT, "\033[94m"),
]
RESET, BOLD, DIM, RED = "\033[0m", "\033[1m", "\033[2m", "\033[91m"
_print_lock = threading.Lock()


def _port_busy(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((config.HOST, port)) == 0


def _pump(name: str, color: str, proc: subprocess.Popen) -> None:
    tag = f"{color}{name:<20}{RESET}"
    for line in iter(proc.stdout.readline, ""):
        with _print_lock:
            print(f"{tag}│ {line.rstrip()}", flush=True)


def _spawn(name: str, port: int, color: str, reload: bool, env: dict) -> subprocess.Popen:
    cmd = [sys.executable, "-m", "uvicorn", f"{name}.main:app", "--host", config.HOST, "--port", str(port)]
    if reload:
        cmd += ["--reload", "--reload-dir", str(ROOT / name), "--reload-dir", str(ROOT / "shared"),
                "--reload-include", "*.html", "--reload-include", "*.css"]
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1, **kwargs)
    threading.Thread(target=_pump, args=(name, color, proc), daemon=True).start()
    return proc


def _wait_healthy(name: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://{config.HOST}:{port}/health", timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    return False


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":  # uvicorn --reload spawns a worker child; kill the whole tree
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        os.killpg(proc.pid, signal.SIGTERM)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-reload", action="store_true", help="disable uvicorn live-reload")
    args = parser.parse_args()
    if os.name == "nt":
        os.system("")  # enable ANSI colours in classic Windows consoles

    busy = [f"{n}:{p}" for n, p, _ in SERVICES if _port_busy(p)]
    if busy:
        print(f"{RED}Порты заняты: {', '.join(busy)}. Остановите предыдущий запуск или задайте FSP_*_PORT.{RESET}")
        return 1

    # Polling: OS file events are unreliable in synced folders (OneDrive/Dropbox) and some network drives.
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT),
           "WATCHFILES_FORCE_POLLING": os.environ.get("WATCHFILES_FORCE_POLLING", "true")}
    procs: list[subprocess.Popen] = []
    try:
        for name, port, color in SERVICES:
            procs.append(_spawn(name, port, color, not args.no_reload, env))
            if not _wait_healthy(name, port):
                print(f"{RED}{name} не поднялся на :{port} — см. лог выше.{RESET}")
                return 1
        accounts = "\n".join(f"    {a['email']:<20} {a['role']:<8} {a['title']}" for a in DEMO_ACCOUNTS)
        with _print_lock:
            print(f"""
{BOLD}{"═" * 64}{RESET}
{BOLD}  // FSP_ORENBURG_INIT... {"\033[92m"}SUCCESS{RESET}
{BOLD}{"═" * 64}{RESET}
  Платформа:         {BOLD}{config.PUBLIC_BASE_URL}{RESET}
  Sportsman API:     http://{config.HOST}:{config.SPORTSMAN_PORT}/docs
  Competition API:   http://{config.HOST}:{config.COMPETITION_PORT}/docs

  Демо-аккаунты по сценариям (пароль {BOLD}{DEMO_PASSWORD}{RESET}, все — на странице /login):
{accounts}
{DIM}  live-reload: {"выкл" if args.no_reload else "вкл"} · Ctrl+C — остановить всё{RESET}
{BOLD}{"═" * 64}{RESET}
""", flush=True)
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
        print(f"{RED}Один из сервисов завершился — останавливаю остальные.{RESET}")
        return 1
    except KeyboardInterrupt:
        print("\nОстановка...")
        return 0
    finally:
        for p in reversed(procs):
            _kill_tree(p)


if __name__ == "__main__":
    sys.exit(main())

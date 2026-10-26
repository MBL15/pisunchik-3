"""Stateless signed cookies (HMAC-SHA256) for the session and one-shot flash messages.

The session survives gateway live-reloads, and the active role is re-validated against the registry
on every request, so revoking a role takes effect immediately.
"""
import base64
import hashlib
import hmac
import json
import time

from fastapi import Request
from fastapi.responses import Response

from shared.config import SECRET_KEY

SESSION_COOKIE = "fsp_session"
FLASH_COOKIE = "fsp_flash"
SESSION_TTL = 60 * 60 * 12


def sign(payload: dict) -> str:
    raw = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()).decode().rstrip("=")
    sig = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def unsign(token: str | None) -> dict | None:
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    expected = hmac.new(SECRET_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except ValueError:
        return None


def read_session(request: Request) -> dict | None:
    data = unsign(request.cookies.get(SESSION_COOKIE))
    if not data or data.get("exp", 0) < time.time():
        return None
    return data


def write_session(response: Response, fsp_id: str, role: str) -> None:
    token = sign({"fid": fsp_id, "role": role, "exp": int(time.time()) + SESSION_TTL})
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True, samesite="lax")


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


def set_flash(response: Response, kind: str, text: str, extra: dict | None = None) -> None:
    response.set_cookie(FLASH_COOKIE, sign({"kind": kind, "text": text[:400], **(extra or {})}),
                        max_age=60, httponly=True, samesite="lax")


def pop_flash(request: Request) -> dict | None:
    return unsign(request.cookies.get(FLASH_COOKIE))

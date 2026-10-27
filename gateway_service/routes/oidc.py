"""Д1. Региональный ФСП ID — провайдер OpenID Connect: «Войти через ФСП ID» для сайтов и сервисов партнёров.

Поток: authorization code + PKCE (S256, обязателен) → страница согласия на передачу данных → token endpoint (id_token RS256 + access token)
→ userinfo (ФИО, роли, дисциплины, разряд — по запрошенным областям). Discovery — `/.well-known/openid-configuration`, ключи — `/oidc/jwks`.
Клиентов регистрирует администратор (`/oidc/clients`), для демонстрации есть тестовое приложение-партнёр (`/oidc/demo`).
Коды и токены хранятся в памяти шлюза и недолговечны; ключ подписи лежит в `.oidc_key.pem` (создаётся при первом запуске).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from gateway_service.session import sign, unsign
from gateway_service.web import Redirect, back, clients, current_user, humanize, render, require
from shared import config
from shared.domain import ROLE_LABELS, Role
from shared.http import ServiceError

router = APIRouter()
ISSUER = config.PUBLIC_BASE_URL.rstrip("/")
SCOPES = {"openid": "Идентификатор ФСП ID", "profile": "ФИО", "roles": "Роли в платформе", "disciplines": "Дисциплины",
          "rank": "Спортивный разряд и срок его действия"}
CODE_TTL, TOKEN_TTL, ID_TOKEN_TTL, REQUEST_TTL = 120, 3600, 600, 600
DEMO_CLIENT_ID, DEMO_CLIENT_SECRET = "fsp-demo-client", "fsp-demo-secret"
DEMO_COOKIE = "fsp_oidc_demo"
KEY_FILE = Path(os.getenv("FSP_OIDC_KEY_FILE") or Path(__file__).resolve().parents[2] / ".oidc_key.pem")

_codes: dict[str, dict] = {}
_tokens: dict[str, dict] = {}


def _load_key():
    try:
        return serialization.load_pem_private_key(KEY_FILE.read_bytes(), password=None)
    except (OSError, ValueError):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        try:
            KEY_FILE.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        except OSError:  # нет прав на запись: ключ живёт до перезапуска
            pass
        return key


_KEY = _load_key()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _int_b64(n: int) -> str:
    return _b64(n.to_bytes((n.bit_length() + 7) // 8, "big"))


_NUMBERS = _KEY.public_key().public_numbers()
KID = _b64(hashlib.sha256(f"{_NUMBERS.n}:{_NUMBERS.e}".encode()).digest())[:16]
JWK = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": KID, "n": _int_b64(_NUMBERS.n), "e": _int_b64(_NUMBERS.e)}


def _purge() -> None:
    now = time.time()
    for store in (_codes, _tokens):
        for k in [k for k, v in store.items() if v["exp"] < now]:
            del store[k]


def _pkce_ok(verifier: str, challenge: str) -> bool:
    return hmac.compare_digest(_b64(hashlib.sha256(verifier.encode()).digest()), challenge)


def _redirect_with(uri: str, **params) -> str:
    u = urlsplit(uri)
    query = urlencode(parse_qsl(u.query) + [(k, v) for k, v in params.items() if v is not None])
    return urlunsplit((u.scheme, u.netloc, u.path, query, ""))


def _claims(person: dict, scopes: list[str]) -> dict:
    out = {"sub": person["fsp_id"]}
    if "profile" in scopes:
        out |= {"name": person["full_name"], "given_name": person["first_name"], "family_name": person["last_name"]}
    if "roles" in scopes:
        out["roles"] = person["roles"]
    if "disciplines" in scopes:
        out["disciplines"] = person["disciplines"]
    if "rank" in scopes:
        r = person["rank"]
        out["rank"] = {"code": r["code"], "title": r["title"], "expires_at": r["expires_at"]} if r else None
    return out


def _oauth_error(error: str, description: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=status,
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


def _page_error(request: Request, text: str):
    resp = render(request, "error.html", user=None, code=400, service="ФСП ID", message=text)
    resp.status_code = 400
    return resp


# ---------------------------------------------------------------- discovery и ключи
@router.get("/.well-known/openid-configuration")
async def discovery():
    return {
        "issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/oidc/authorize", "token_endpoint": f"{ISSUER}/oidc/token",
        "userinfo_endpoint": f"{ISSUER}/oidc/userinfo", "jwks_uri": f"{ISSUER}/oidc/jwks", "scopes_supported": list(SCOPES),
        "response_types_supported": ["code"], "grant_types_supported": ["authorization_code"], "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"], "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post", "none"],
        "claims_supported": ["sub", "name", "given_name", "family_name", "roles", "disciplines", "rank", "iss", "aud", "exp", "iat",
                             "auth_time", "nonce"],
    }


@router.get("/oidc/jwks")
async def jwks():
    return {"keys": [JWK]}


# ---------------------------------------------------------------- authorize + согласие
async def _client(client_id: str) -> dict | None:
    try:
        return await clients["sportsman"].get(f"/api/v1/oidc/clients/by-id/{quote(client_id, safe='')}")
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return None


def _redirect_allowed(request: Request, client: dict, uri: str) -> bool:
    """Точное совпадение с зарегистрированным адресом; демо-приложению дополнительно — свой же адрес обратного вызова на текущем хосте."""
    if uri in client["redirect_uris"]:
        return True
    return client["client_id"] == DEMO_CLIENT_ID and uri == f"{request.url.scheme}://{request.headers.get('host', '')}/oidc/demo/callback"


@router.get("/oidc/authorize", response_class=HTMLResponse)
async def authorize(request: Request):
    q = request.query_params
    client = await _client(q.get("client_id", ""))
    uri = q.get("redirect_uri", "")
    if not client or not _redirect_allowed(request, client, uri):  # до проверки адреса возврата ошибку на него не отправляем
        return _page_error(request, "Неизвестное приложение или адрес возврата не зарегистрирован в ФСП ID.")
    state = q.get("state")

    def fail(error: str, text: str):
        return RedirectResponse(_redirect_with(uri, error=error, error_description=text, state=state), status_code=303)

    if q.get("response_type") != "code":
        return fail("unsupported_response_type", "Поддерживается только response_type=code")
    if q.get("code_challenge_method") != "S256" or not 43 <= len(q.get("code_challenge", "")) <= 128:
        return fail("invalid_request", "Требуется PKCE: code_challenge (S256)")
    scopes = [s for s in q.get("scope", "").split() if s in client["scopes"]]
    if "openid" not in scopes:
        return fail("invalid_scope", "Нужна область openid")
    user = await current_user(request)
    if not user:
        raise Redirect("/login?next=" + quote(f"{request.url.path}?{request.url.query}", safe=""))
    blob = sign({"c": client["client_id"], "u": uri, "s": scopes, "st": state, "n": q.get("nonce"), "ch": q["code_challenge"],
                 "f": user["person"]["fsp_id"], "exp": int(time.time()) + REQUEST_TTL})
    shared_values = _claims(user["person"], scopes)
    return render(request, "oidc_consent.html", user=user, client=client, scopes=[(s, SCOPES[s]) for s in scopes], blob=blob,
                  preview=shared_values, role_labels=ROLE_LABELS)


@router.post("/oidc/consent")
async def consent(request: Request):
    user = await require(request)
    form = await request.form()
    req = unsign(str(form.get("req", "")))
    if not req or req.get("exp", 0) < time.time() or req.get("f") != user["person"]["fsp_id"]:
        return _page_error(request, "Запрос на вход устарел или относится к другому аккаунту — начните вход заново.")
    if form.get("decision") != "allow":
        return RedirectResponse(_redirect_with(req["u"], error="access_denied", error_description="Пользователь отказал в доступе",
                                               state=req["st"]), status_code=303)
    _purge()
    code = secrets.token_urlsafe(32)
    _codes[code] = {"client_id": req["c"], "redirect_uri": req["u"], "scopes": req["s"], "nonce": req["n"], "challenge": req["ch"],
                    "fid": req["f"], "auth_time": int(time.time()), "exp": time.time() + CODE_TTL}
    return RedirectResponse(_redirect_with(req["u"], code=code, state=req["st"]), status_code=303)


# ---------------------------------------------------------------- token + userinfo
@router.post("/oidc/token")
async def token(request: Request):
    form = await request.form()
    cid, secret = form.get("client_id"), form.get("client_secret")
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            cid, _, secret = base64.b64decode(auth[6:]).decode().partition(":")
        except (ValueError, UnicodeDecodeError):
            return _oauth_error("invalid_client", "Некорректный заголовок Authorization", 401)
    try:
        client = await clients["sportsman"].post("/api/v1/oidc/clients/verify", json={"client_id": str(cid or ""), "secret": secret or None})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return _oauth_error("invalid_client", "Клиент не распознан или секрет неверен", 401)
    if form.get("grant_type") != "authorization_code":
        return _oauth_error("unsupported_grant_type", "Поддерживается authorization_code")
    _purge()
    grant = _codes.pop(str(form.get("code", "")), None)  # код одноразовый: погашается при любой попытке
    if not grant or grant["client_id"] != client["client_id"] or grant["redirect_uri"] != form.get("redirect_uri"):
        return _oauth_error("invalid_grant", "Код недействителен, просрочен или выдан другому приложению")
    if not _pkce_ok(str(form.get("code_verifier", "")), grant["challenge"]):
        return _oauth_error("invalid_grant", "code_verifier не соответствует code_challenge")
    person = await clients["sportsman"].get(f"/api/v1/persons/{grant['fid']}")
    now = int(time.time())
    id_claims = {"iss": ISSUER, "aud": client["client_id"], "iat": now, "exp": now + ID_TOKEN_TTL, "auth_time": grant["auth_time"],
                 **({"nonce": grant["nonce"]} if grant["nonce"] else {}), **_claims(person, grant["scopes"])}
    access = "fspat_" + secrets.token_urlsafe(32)
    _tokens[access] = {"fid": grant["fid"], "scopes": grant["scopes"], "client_id": client["client_id"], "exp": time.time() + TOKEN_TTL}
    return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": TOKEN_TTL, "scope": " ".join(grant["scopes"]),
                         "id_token": jwt.encode(id_claims, _KEY, algorithm="RS256", headers={"kid": KID})},
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.api_route("/oidc/userinfo", methods=["GET", "POST"])
async def userinfo(request: Request):
    _purge()
    auth = request.headers.get("authorization", "")
    grant = _tokens.get(auth[7:]) if auth.lower().startswith("bearer ") else None
    if not grant:
        return JSONResponse({"error": "invalid_token"}, status_code=401, headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
    person = await clients["sportsman"].get(f"/api/v1/persons/{grant['fid']}")
    return JSONResponse(_claims(person, grant["scopes"]), headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- регистрация клиентов (администратор)
@router.get("/oidc/clients", response_class=HTMLResponse)
async def clients_page(request: Request):
    user = await require(request, Role.ADMIN)
    return render(request, "oidc_clients.html", user=user, items=await clients["sportsman"].get("/api/v1/oidc/clients"), scopes=SCOPES,
                  issuer=ISSUER, created=None)


@router.post("/oidc/clients", response_class=HTMLResponse)
async def register_client(request: Request):
    user = await require(request, Role.ADMIN)
    form = await request.form()
    uris = [u.strip() for u in str(form.get("redirect_uris", "")).replace(",", "\n").splitlines() if u.strip()]
    try:
        created = await clients["sportsman"].post("/api/v1/oidc/clients", json={
            "name": str(form.get("name", "")), "redirect_uris": uris, "public": form.get("public") == "1",
            "scopes": form.getlist("scope") or list(SCOPES), "actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/oidc/clients", "error", humanize(exc.detail))
    resp = render(request, "oidc_clients.html", user=user, items=await clients["sportsman"].get("/api/v1/oidc/clients"), scopes=SCOPES,
                  issuer=ISSUER, created=created)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/oidc/clients/{cid}/revoke")
async def revoke_client(request: Request, cid: int):
    user = await require(request, Role.ADMIN)
    try:
        await clients["sportsman"].request("POST", f"/api/v1/oidc/clients/{cid}/revoke", params={"actor": user["person"]["fsp_id"]})
    except ServiceError as exc:
        if exc.unavailable:
            raise
        return back("/oidc/clients", "error", humanize(exc.detail))
    return back("/oidc/clients", "ok", "Приложение отключено: новые входы через него невозможны")


# ---------------------------------------------------------------- тестовое приложение-партнёр
def _demo_state(request: Request) -> dict | None:
    return unsign(request.cookies.get(DEMO_COOKIE))


@router.get("/oidc/demo", response_class=HTMLResponse)
async def demo_page(request: Request):
    user = await current_user(request)
    return render(request, "oidc_demo.html", user=user, result=None, issuer=ISSUER, error=None)


@router.get("/oidc/demo/login")
async def demo_login(request: Request):
    verifier, state, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(16), secrets.token_urlsafe(16)
    redirect = f"{request.url.scheme}://{request.headers.get('host', '')}/oidc/demo/callback"
    url = "/oidc/authorize?" + urlencode({
        "response_type": "code", "client_id": DEMO_CLIENT_ID, "redirect_uri": redirect, "scope": "openid profile roles disciplines rank",
        "state": state, "nonce": nonce, "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()), "code_challenge_method": "S256"})
    resp = RedirectResponse(url, status_code=303)
    resp.set_cookie(DEMO_COOKIE, sign({"v": verifier, "s": state, "n": nonce, "r": redirect, "exp": int(time.time()) + 600}),
                    max_age=600, httponly=True, samesite="lax", secure=config.COOKIE_SECURE)
    return resp


@router.get("/oidc/demo/callback", response_class=HTMLResponse)
async def demo_callback(request: Request):
    """Приложение-партнёр: обменивает код на токены внутри процесса (ASGITransport), проверяет подпись id_token и читает userinfo."""
    saved, q = _demo_state(request), request.query_params
    if q.get("error"):
        return render(request, "oidc_demo.html", user=None, result=None, issuer=ISSUER, error=f"{q['error']}: {q.get('error_description', '')}")
    if not saved or saved.get("exp", 0) < time.time() or not hmac.compare_digest(saved["s"], q.get("state", "")):
        return render(request, "oidc_demo.html", user=None, result=None, issuer=ISSUER, error="Состояние входа не совпало (state) — начните заново.")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=request.app), base_url="http://fsp") as http:
        tok = (await http.post("/oidc/token", data={"grant_type": "authorization_code", "code": q.get("code", ""), "redirect_uri": saved["r"],
                                                    "client_id": DEMO_CLIENT_ID, "client_secret": DEMO_CLIENT_SECRET,
                                                    "code_verifier": saved["v"]})).json()
        if "error" in tok:
            return render(request, "oidc_demo.html", user=None, result=None, issuer=ISSUER, error=f"{tok['error']}: {tok.get('error_description', '')}")
        keys = (await http.get("/oidc/jwks")).json()["keys"]
        info = (await http.get("/oidc/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"})).json()
    key = jwt.PyJWK(next(k for k in keys if k["kid"] == jwt.get_unverified_header(tok["id_token"])["kid"])).key
    try:
        claims = jwt.decode(tok["id_token"], key, algorithms=["RS256"], audience=DEMO_CLIENT_ID, issuer=ISSUER)
    except jwt.PyJWTError as exc:
        return render(request, "oidc_demo.html", user=None, result=None, issuer=ISSUER, error=f"id_token не прошёл проверку: {exc}")
    if claims.get("nonce") != saved["n"]:
        return render(request, "oidc_demo.html", user=None, result=None, issuer=ISSUER, error="nonce не совпал")
    resp = render(request, "oidc_demo.html", user=None, issuer=ISSUER, error=None, result={
        "claims": claims, "userinfo": info, "scope": tok["scope"], "expires_in": tok["expires_in"],
        "id_token_preview": tok["id_token"][:60] + "…", "claims_json": json.dumps(claims, ensure_ascii=False, indent=2),
        "userinfo_json": json.dumps(info, ensure_ascii=False, indent=2)})
    resp.delete_cookie(DEMO_COOKIE)
    return resp

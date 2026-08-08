"""
احراز هویت پنل وب:
- Telegram Login Widget
- ورود با یوزرنیم / پسورد (متغیرهای محیطی PANEL_USER و PANEL_PASSWORD)
"""
import hashlib
import hmac
import os
import secrets
import time

from aiohttp import web

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

PANEL_USER = os.environ.get("PANEL_USER", "").strip()
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "").strip()
_panel_uid_raw = os.environ.get("PANEL_USER_ID", "").strip()
PANEL_USER_ID: int | None = int(_panel_uid_raw) if _panel_uid_raw.isdigit() else None

COOKIE_NAME = "kh_session"
SESSION_TTL = 30 * 24 * 3600
AUTH_MAX_AGE = 86400

BOT_USERNAME: str = ""
_sessions: dict[str, dict] = {}


def password_login_enabled() -> bool:
    return bool(PANEL_USER and PANEL_PASSWORD)


def panel_enabled() -> bool:
    return bool(ADMIN_IDS) or password_login_enabled()


def _password_session_user_id() -> int | None:
    if PANEL_USER_ID is not None:
        return PANEL_USER_ID
    if ADMIN_IDS:
        return sorted(ADMIN_IDS)[0]
    return 1


def verify_telegram_login(data: dict) -> int | None:
    received_hash = data.get("hash")
    if not received_hash:
        return None

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()) if k != "hash")
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    computed_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    try:
        auth_date = int(data.get("auth_date", 0))
    except (TypeError, ValueError):
        return None
    if time.time() - auth_date > AUTH_MAX_AGE:
        return None

    try:
        user_id = int(data.get("id"))
    except (TypeError, ValueError):
        return None

    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        return None

    return user_id


def verify_password_login(username: str, password: str) -> int | None:
    if not password_login_enabled():
        return None
    if not username or not password:
        return None
    user_ok = secrets.compare_digest(username.strip(), PANEL_USER)
    pass_ok = secrets.compare_digest(password, PANEL_PASSWORD)
    if not (user_ok and pass_ok):
        return None
    return _password_session_user_id()


def create_session(user_id: int) -> str:
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {"user_id": user_id, "expires": time.time() + SESSION_TTL}
    return session_id


def get_session_user(session_id: str | None) -> int | None:
    if not session_id:
        return None
    s = _sessions.get(session_id)
    if not s:
        return None
    if s["expires"] < time.time():
        _sessions.pop(session_id, None)
        return None
    return s["user_id"]


def destroy_session(session_id: str | None) -> None:
    if session_id:
        _sessions.pop(session_id, None)


_PUBLIC_PANEL_PATHS = {
    "/panel/login",
    "/panel/auth/callback",
    "/panel/auth/password",
    "/panel/logout",
}


@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path

    if path.startswith("/panel/static/") or path in _PUBLIC_PANEL_PATHS:
        return await handler(request)

    if path == "/panel" or path.startswith("/api/"):
        session_id = request.cookies.get(COOKIE_NAME)
        user_id = get_session_user(session_id)
        if user_id is None:
            if path.startswith("/api/"):
                return web.json_response({"error": "لاگین نکردی یا سشنت منقضی شده."}, status=401)
            raise web.HTTPFound("/panel/login")
        request["user_id"] = user_id

    return await handler(request)

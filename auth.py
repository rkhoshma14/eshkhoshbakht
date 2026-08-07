"""
احراز هویت پنل وب با Telegram Login Widget + مدیریت سشن.

نحوه‌ی کار:
۱. کاربر تو صفحه‌ی لاگین رو دکمه‌ی "ورود با تلگرام" می‌زنه.
۲. تلگرام کاربر رو با id/hash/... به /panel/auth/callback ریدایرکت می‌کنه.
۳. hash رو با HMAC-SHA256 و کلید مشتق‌شده از BOT_TOKEN وریفای می‌کنیم.
۴. اگه id توی ADMIN_IDS بود، یه سشن می‌سازیم و کوکی می‌ذاریم.

نکته‌ی مهم: برای اینکه ویجت کار کنه، باید دامنه‌ی سایت رو با دستور
/setdomain به BotFather بدی (توضیح کامل تو README).
"""
import hashlib
import hmac
import os
import secrets
import time

from aiohttp import web

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

COOKIE_NAME = "kh_session"
SESSION_TTL = 30 * 24 * 3600  # ۳۰ روز
AUTH_MAX_AGE = 86400  # حداکثر قدمت داده‌ی ورود تلگرام (ثانیه)

# بعد از bot.get_me() توی main() پر میشه، برای نمایش تو صفحه‌ی لاگین
BOT_USERNAME: str = ""

# سشن‌ها فقط تو حافظه نگه داشته میشن؛ با ری‌استارت شدن سرویس، باید دوباره لاگین کنی.
_sessions: dict[str, dict] = {}


def panel_enabled() -> bool:
    """پنل وب فقط وقتی فعاله که حداقل یک ADMIN_IDS ست شده باشه؛ وگرنه قفل می‌مونه."""
    return bool(ADMIN_IDS)


def verify_telegram_login(data: dict) -> int | None:
    """داده‌ی برگشتی از Telegram Login Widget رو وریفای می‌کنه.
    در صورت معتبر و مجاز بودن، user_id رو برمی‌گردونه، وگرنه None."""
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


_PUBLIC_PANEL_PATHS = {"/panel/login", "/panel/auth/callback", "/panel/logout"}


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

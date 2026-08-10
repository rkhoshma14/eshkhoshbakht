"""
احراز هویت پنل وب:
- Telegram Login Widget
- ورود با یوزرنیم / پسورد (متغیرهای محیطی PANEL_USER و PANEL_PASSWORD)
- Telegram Mini App (initData) — برای وقتی پنل داخل خودِ تلگرام باز میشه

نحوه‌ی کار تلگرام (Login Widget):
۱. کاربر تو صفحه‌ی لاگین رو دکمه‌ی "ورود با تلگرام" می‌زنه.
۲. تلگرام کاربر رو با id/hash/... به /panel/auth/callback ریدایرکت می‌کنه.
۳. hash رو با HMAC-SHA256 و کلید مشتق‌شده از BOT_TOKEN وریفای می‌کنیم.
۴. اگه id توی ADMIN_IDS بود، یه سشن می‌سازیم و کوکی می‌ذاریم.

نحوه‌ی کار یوزرنیم/پسورد:
۱. کاربر فرم لاگین رو پر می‌کنه و به /panel/auth/password پست می‌کنه.
۲. با PANEL_USER و PANEL_PASSWORD مقایسه می‌شه (مقایسه‌ی زمان‌ثابت).
۳. سشن با user_id مربوط به ادمین ساخته می‌شه (اولین ADMIN_IDS یا PANEL_USER_ID).

نحوه‌ی کار Mini App:
۱. کاربر از داخل ربات دکمه‌ی "باز کردن پنل" رو می‌زنه، تلگرام صفحه‌ی /panel/miniapp رو
   با initData امضاشده باز می‌کنه (بدون نیاز به کلیک یا رمز اضافه).
۲. initData یه رشته‌ی query-string مانند با فیلد hash است؛ کلید HMAC اینجا فرق داره:
   secret_key = HMAC-SHA256(key="WebAppData", msg=BOT_TOKEN)
۳. اگه معتبر بود و id توی ADMIN_IDS بود، سشن ساخته و کوکی ست میشه، دقیقا مثل بقیه.

نکته: برای ویجت تلگرام باید دامنه‌ی سایت رو با /setdomain به BotFather بدی.
"""
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import parse_qsl

from aiohttp import web

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

# ورود با یوزرنیم / پسورد (اختیاری)
PANEL_USER = os.environ.get("PANEL_USER", "").strip()
PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "").strip()
# اگر ست بشه، سشن پسوردی با این user_id ساخته می‌شه؛ وگرنه اولین ADMIN_IDS
_panel_uid_raw = os.environ.get("PANEL_USER_ID", "").strip()
PANEL_USER_ID: int | None = int(_panel_uid_raw) if _panel_uid_raw.isdigit() else None

COOKIE_NAME = "kh_session"
SESSION_TTL = 30 * 24 * 3600  # ۳۰ روز
AUTH_MAX_AGE = 86400  # حداکثر قدمت داده‌ی ورود تلگرام (ثانیه)

# بعد از bot.get_me() توی main() پر میشه، برای نمایش تو صفحه‌ی لاگین
BOT_USERNAME: str = ""

# سشن‌ها فقط تو حافظه نگه داشته میشن؛ با ری‌استارت شدن سرویس، باید دوباره لاگین کنی.
_sessions: dict[str, dict] = {}


def password_login_enabled() -> bool:
    return bool(PANEL_USER and PANEL_PASSWORD)


def panel_enabled() -> bool:
    """پنل وقتی فعاله که یا ADMIN_IDS ست شده باشه یا یوزرنیم/پسورد پنل."""
    return bool(ADMIN_IDS) or password_login_enabled()


def _password_session_user_id() -> int | None:
    """user_id که برای سشن پسوردی استفاده می‌شه تا داده‌ها با ربات یکی باشن."""
    if PANEL_USER_ID is not None:
        return PANEL_USER_ID
    if ADMIN_IDS:
        return sorted(ADMIN_IDS)[0]
    # فقط پسورد ست شده و ADMIN_IDS خالیه — یه id ثابت
    return 1


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


def verify_webapp_init_data(raw_init_data: str) -> int | None:
    """initData ارسالی از Telegram Mini App رو وریفای می‌کنه.
    برخلاف Login Widget، اینجا کلید HMAC از "WebAppData" + BOT_TOKEN مشتق میشه
    (طبق مستندات رسمی تلگرام برای Web Apps)."""
    if not raw_init_data:
        return None

    try:
        pairs = parse_qsl(raw_init_data, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return None
    data = dict(pairs)

    received_hash = data.pop("hash", None)
    if not received_hash:
        return None

    check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
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
        user_obj = json.loads(data.get("user", "{}"))
        user_id = int(user_obj["id"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None

    if not ADMIN_IDS or user_id not in ADMIN_IDS:
        return None

    return user_id


def verify_password_login(username: str, password: str) -> int | None:
    """یوزرنیم/پسورد رو چک می‌کنه؛ در صورت موفقیت user_id سشن رو برمی‌گردونه."""
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
    "/panel/miniapp",
    "/api/auth/webapp",
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

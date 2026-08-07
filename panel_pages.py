"""
صفحات HTML پنل وب: صفحه‌ی لاگین (با Telegram Login Widget) و شل اصلی SPA.
"""
from pathlib import Path

from aiohttp import web

import auth

STATIC_DIR = Path(__file__).parent / "static"


def _login_html() -> str:
    bot_username = auth.BOT_USERNAME
    if bot_username:
        widget = f"""
        <script async src="https://telegram.org/js/telegram-widget.js?22"
          data-telegram-login="{bot_username}"
          data-size="large"
          data-radius="12"
          data-auth-url="/panel/auth/callback"
          data-request-access="write"></script>
        """
    else:
        widget = "<p class='muted'>یوزرنیم ربات هنوز مشخص نشده، چند لحظه صبر کن و صفحه رو رفرش کن.</p>"

    disabled_notice = ""
    if not auth.panel_enabled():
        disabled_notice = (
            "<p class='warn'>⚠️ پنل غیرفعاله چون ADMIN_IDS ست نشده. "
            "این متغیر رو تو Railway تنظیم کن و دوباره دیپلوی کن.</p>"
        )

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>ورود به پنل خوشبخت</title>
<link rel="stylesheet" href="/panel/static/style.css"/>
</head>
<body class="login-body">
  <div class="login-card">
    <div class="logo">⚡</div>
    <h1>پنل خوشبخت</h1>
    <p class="muted">برای ورود، با اکانت تلگرام ادمین وارد شو</p>
    <div class="widget-wrap">{widget}</div>
    {disabled_notice}
  </div>
</body>
</html>"""


async def handle_login_page(request: web.Request) -> web.Response:
    return web.Response(text=_login_html(), content_type="text/html", charset="utf-8")


async def handle_auth_callback(request: web.Request) -> web.Response:
    data = dict(request.query)
    user_id = auth.verify_telegram_login(data)
    if user_id is None:
        return web.Response(
            text=(
                "<div style='font-family:tahoma;text-align:center;margin-top:20vh;color:#f87171'>"
                "<h2>ورود ناموفق بود</h2><p>یا داده‌ی ورود نامعتبره، یا این آیدی تلگرام تو ADMIN_IDS نیست.</p>"
                "<a href='/panel/login' style='color:#38bdf8'>برگشت به صفحه‌ی ورود</a></div>"
            ),
            content_type="text/html",
            charset="utf-8",
            status=403,
        )

    session_id = auth.create_session(user_id)
    resp = web.HTTPFound("/panel")
    resp.set_cookie(
        auth.COOKIE_NAME,
        session_id,
        max_age=auth.SESSION_TTL,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )
    return resp


async def handle_logout(request: web.Request) -> web.Response:
    session_id = request.cookies.get(auth.COOKIE_NAME)
    auth.destroy_session(session_id)
    resp = web.HTTPFound("/panel/login")
    resp.del_cookie(auth.COOKIE_NAME, path="/")
    return resp


async def handle_panel_index(request: web.Request) -> web.Response:
    index_path = STATIC_DIR / "panel.html"
    return web.Response(text=index_path.read_text(encoding="utf-8"), content_type="text/html", charset="utf-8")


def add_routes(app: web.Application) -> None:
    app.router.add_get("/panel/login", handle_login_page)
    app.router.add_get("/panel/auth/callback", handle_auth_callback)
    app.router.add_get("/panel/logout", handle_logout)
    app.router.add_get("/panel", handle_panel_index)
    app.router.add_static("/panel/static/", path=STATIC_DIR, name="panel_static")

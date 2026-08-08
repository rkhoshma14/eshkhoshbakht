"""
صفحات HTML پنل وب: لاگین (تلگرام + یوزرنیم/پسورد) و شل اصلی SPA.
"""
from pathlib import Path
from html import escape as html_escape
from urllib.parse import quote, unquote

from aiohttp import web

import auth

STATIC_DIR = Path(__file__).parent / "static"


def _login_html(error: str = "") -> str:
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
            "<p class='warn'>⚠️ پنل غیرفعاله. یا <code>ADMIN_IDS</code> رو ست کن "
            "یا <code>PANEL_USER</code> و <code>PANEL_PASSWORD</code> رو در Railway تنظیم کن.</p>"
        )

    error_html = f"<p class='error-msg'>{html_escape(error)}</p>" if error else ""

    password_form = ""
    if auth.password_login_enabled():
        password_form = """
    <div class="divider"><span>یا</span></div>
    <form class="login-form" method="post" action="/panel/auth/password" autocomplete="on">
      <label for="username">یوزرنیم</label>
      <input type="text" id="username" name="username" required autocomplete="username" dir="ltr"/>
      <label for="password">پسورد</label>
      <input type="password" id="password" name="password" required autocomplete="current-password" dir="ltr"/>
      <button type="submit" class="btn login-btn">ورود با یوزرنیم</button>
    </form>
        """

    if auth.ADMIN_IDS:
        tg_section = f"""
    <p class="muted">ورود با اکانت تلگرام ادمین</p>
    <div class="widget-wrap">{widget}</div>
        """
    elif auth.password_login_enabled():
        tg_section = "<p class='muted'>با یوزرنیم و پسورد وارد شو</p>"
    else:
        tg_section = f"<div class='widget-wrap'>{widget}</div>"

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
    {tg_section}
    {error_html}
    {password_form}
    {disabled_notice}
  </div>
</body>
</html>"""


async def handle_login_page(request: web.Request) -> web.Response:
    err = unquote(request.query.get("error", "") or "")
    return web.Response(text=_login_html(error=err), content_type="text/html", charset="utf-8")


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


async def handle_password_login(request: web.Request) -> web.Response:
    if request.method != "POST":
        raise web.HTTPFound("/panel/login")

    if not auth.password_login_enabled():
        raise web.HTTPFound("/panel/login?error=" + quote("ورود با یوزرنیم غیرفعاله"))

    data = await request.post()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user_id = auth.verify_password_login(username, password)
    if user_id is None:
        raise web.HTTPFound("/panel/login?error=" + quote("یوزرنیم یا پسورد اشتباهه"))

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
    app.router.add_post("/panel/auth/password", handle_password_login)
    app.router.add_get("/panel/logout", handle_logout)
    app.router.add_get("/panel", handle_panel_index)
    app.router.add_static("/panel/static/", path=STATIC_DIR, name="panel_static")

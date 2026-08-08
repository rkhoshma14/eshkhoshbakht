"""
صفحات HTML پنل وب: لاگین (تلگرام + یوزرنیم/پسورد) و شل اصلی SPA.
"""
from pathlib import Path
from html import escape as html_escape
from urllib.parse import quote, unquote

from aiohttp import web

import auth

STATIC_DIR = Path(__file__).parent / "static"

_LOGO_SVG = """<svg viewBox="0 0 24 24" stroke="currentColor" fill="none" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
  <path d="M12 3l2.2 4.5L19 8.3l-3.5 3.4.8 4.8L12 14.5 7.7 16.5l.8-4.8L5 8.3l4.8-.8L12 3z"/>
  <circle cx="12" cy="12" r="2"/>
</svg>"""


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
        widget = "<p class='muted'>یوزرنیم ربات هنوز مشخص نشده؛ چند لحظه صبر کن و صفحه را رفرش کن.</p>"

    disabled_notice = ""
    if not auth.panel_enabled():
        disabled_notice = (
            "<p class='warn'>پنل غیرفعال است. یا <code>ADMIN_IDS</code> را ست کن "
            "یا <code>PANEL_USER</code> و <code>PANEL_PASSWORD</code> را در Railway تنظیم کن.</p>"
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
      <button type="submit" class="btn login-btn">ورود</button>
    </form>
        """

    if auth.ADMIN_IDS:
        tg_section = f"""
    <p class="tagline">ورود امن با اکانت تلگرام ادمین</p>
    <div class="widget-wrap">{widget}</div>
        """
    elif auth.password_login_enabled():
        tg_section = '<p class="tagline">با یوزرنیم و پسورد وارد شو</p>'
    else:
        tg_section = f'<div class="widget-wrap">{widget}</div>'

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="theme-color" content="#0a1628"/>
<title>ورود · خوشبخت</title>
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin/>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Vazirmatn:wght@400;500;600;700&display=swap" rel="stylesheet"/>
<link rel="stylesheet" href="/panel/static/style.css"/>
</head>
<body class="login-body">
  <div class="login-card">
    <div class="login-logo">{_LOGO_SVG}</div>
    <h1>خوشبخت</h1>
    <p class="tagline" style="margin-bottom:4px">پنل مدیریت تونل و اشتراک</p>
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
                "<!DOCTYPE html><html lang='fa' dir='rtl'><head><meta charset='utf-8'/>"
                "<link href='https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;700&display=swap' rel='stylesheet'/>"
                "<style>body{font-family:Vazirmatn,Tahoma,sans-serif;background:#070f1a;color:#e8eef6;"
                "display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;text-align:center}"
                "a{color:#d4af37}</style></head><body><div>"
                "<h2 style='color:#f07178'>ورود ناموفق بود</h2>"
                "<p style='color:#6b8299'>دادهٔ ورود نامعتبر است یا این آیدی در ADMIN_IDS نیست.</p>"
                "<a href='/panel/login'>برگشت به صفحهٔ ورود</a></div></body></html>"
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
        raise web.HTTPFound("/panel/login?error=" + quote("ورود با یوزرنیم غیرفعال است"))

    data = await request.post()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user_id = auth.verify_password_login(username, password)
    if user_id is None:
        raise web.HTTPFound("/panel/login?error=" + quote("یوزرنیم یا پسورد اشتباه است"))

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

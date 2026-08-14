import asyncio
import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from html import escape

import httpx
import qrcode
from aiohttp import web

import auth
import panel_api
import panel_pages
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

import storage
from config_parser import (
    config_fingerprint,
    decode_subscription,
    encode_subscription,
    get_protocol,
    get_remark,
    make_expiry_info_config,
    remaining_time_text,
    rename_config,
)
from pinger import ping_configs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
PAGE_SIZE = 8

# آدرس پایه عمومی (مثلاً https://xxx.up.railway.app)
# اگر ست نکنی، از هدر Host درخواست استفاده می‌شه
BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

BTN_ADD_SUB = "➕ افزودن اشتراک"
BTN_LIST = "📋 لیست اشتراک‌ها"
BTN_MY_GENERATED = "🛠 اشتراک‌های من"
BTN_MULTI_BUILD = "🌐 ساخت از چند منبع"
BTN_BACKUP = "💾 بک‌آپ / بازیابی"
BTN_NOTE_SKIP = "بدون یادداشت"
BTN_BACK = "« بازگشت به اشتراک‌ها"
BTN_REFRESH = "🔄 بروزرسانی"
BTN_PING = "📶 پینگ کانفیگ‌ها"
BTN_OPEN_PANEL = "🌐 باز کردن پنل"
BTN_EDIT_NOTE = "📝 یادداشت"
BTN_DELETE = "🗑 حذف اشتراک"
BTN_EXPORT = "📤 خروجی اشتراک"
BTN_DELETE_DEAD = "🧹 حذف کانفیگ‌های مرده"
BTN_BUILD_CUSTOM = "🛠 ساخت اشتراک سفارشی"

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class AddSubState(StatesGroup):
    waiting_for_link = State()
    waiting_for_name = State()
    waiting_for_note = State()


class EditNoteState(StatesGroup):
    waiting_for_note = State()


class RenameState(StatesGroup):
    waiting_for_name = State()


class GenEditState(StatesGroup):
    waiting_cfg_name = State()
    waiting_note = State()


class BackupState(StatesGroup):
    waiting_restore_file = State()


class BuildCustomState(StatesGroup):
    selecting = State()          # انتخاب کانفیگ‌ها (تک‌منبع یا چندمنبع)
    selecting_sources = State()  # انتخاب چند اشتراک منبع
    renaming = State()           # رنیم یکی‌یکی
    waiting_sub_name = State()   # اسم کلی اشتراک سفارشی
    waiting_expiry = State()     # انتخاب تاریخ انقضا


MENU_BUTTONS = {BTN_ADD_SUB, BTN_LIST, BTN_MY_GENERATED, BTN_MULTI_BUILD, BTN_BACKUP}


async def bail_if_menu_button(message: Message, state: FSMContext) -> bool:
    if message.text not in MENU_BUTTONS:
        return False
    await state.clear()
    if message.text == BTN_ADD_SUB:
        await ask_for_link(message, state)
    elif message.text == BTN_LIST:
        await list_subs(message)
    elif message.text == BTN_MY_GENERATED:
        await list_my_generated(message)
    elif message.text == BTN_MULTI_BUILD:
        await start_multi_build(message, state)
    elif message.text == BTN_BACKUP:
        await show_backup_menu(message, state)
    return True


def is_admin(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


def main_menu() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text=BTN_ADD_SUB), KeyboardButton(text=BTN_MULTI_BUILD)],
        [KeyboardButton(text=BTN_LIST), KeyboardButton(text=BTN_MY_GENERATED)],
        [KeyboardButton(text=BTN_BACKUP)],
    ]
    if BASE_URL:
        rows.append([KeyboardButton(text=BTN_OPEN_PANEL, web_app=WebAppInfo(url=f"{BASE_URL}/panel"))])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def _format_updated_at(iso_str: str) -> str:
    if not iso_str:
        return "نامشخص"
    try:
        dt = datetime.fromisoformat(iso_str)
        tehran = dt + timedelta(hours=3, minutes=30)
        return tehran.strftime("%Y/%m/%d  %H:%M")
    except Exception:
        return iso_str[:16]


def build_subs_keyboard(subs: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    chunk = subs[start : start + PAGE_SIZE]
    rows = []
    for i, sub in enumerate(chunk, start=start):
        label = f"{i + 1}. {sub['name']} ({sub['config_count']} کانفیگ)"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"sub_open:{sub['id']}")])

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="« قبلی", callback_data=f"subs_page:{page - 1}"))
    if start + PAGE_SIZE < len(subs):
        nav.append(InlineKeyboardButton(text="بعدی »", callback_data=f"subs_page:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_generated_keyboard(gens: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    """لیست دکمه‌ای اشتراک‌های سفارشی (مثل لیست اشتراک‌ها)."""
    start = page * PAGE_SIZE
    chunk = gens[start : start + PAGE_SIZE]
    rows = []
    for i, g in enumerate(chunk, start=start):
        label = f"{i + 1}. {g['name']} ({g['config_count']} کانفیگ)"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"gen_open:{g['id']}")])

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="« قبلی", callback_data=f"gens_page:{page - 1}"))
    if start + PAGE_SIZE < len(gens):
        nav.append(InlineKeyboardButton(text="بعدی »", callback_data=f"gens_page:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gen_detail_text(g: dict) -> str:
    url = make_public_url(g["token"]) if BASE_URL else f"/sub/{g['token']}"
    created = _format_updated_at(g.get("created_at", ""))
    exp = g.get("expires_at")
    remaining = remaining_time_text(exp)
    if exp:
        if storage.is_generated_expired(g):
            exp_line = f"⏰ انقضا: <b>منقضی شده</b> ({_format_updated_at(exp)})\n"
        else:
            exp_line = f"⏰ انقضا: {_format_updated_at(exp)}\n"
    else:
        exp_line = "⏰ انقضا: بدون محدودیت\n"
    live_line = "🔄 همگام با منبع\n" if g.get("items") else "📌 ثابت (snapshot)\n"
    note = (g.get("note") or "").strip()
    note_line = f"📝 یادداشت: {escape(note)}\n" if note else ""
    text = (
        f"🛠 <b>{escape(g['name'])}</b>\n"
        f"📦 {len(g['configs'])} کانفیگ\n"
        f"🕒 ساخته‌شده: {created}\n"
        f"{exp_line}"
        f"📊 {escape(remaining)}\n"
        f"{live_line}"
        f"{note_line}\n"
        f"🔗 لینک اشتراک:\n<code>{url}</code>"
    )
    return text


def build_expiry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="♾ بدون انقضا", callback_data="expiry:0")],
            [
                InlineKeyboardButton(text="۷ روز", callback_data="expiry:7"),
                InlineKeyboardButton(text="۳۰ روز", callback_data="expiry:30"),
            ],
            [
                InlineKeyboardButton(text="۹۰ روز", callback_data="expiry:90"),
                InlineKeyboardButton(text="۱۸۰ روز", callback_data="expiry:180"),
            ],
            [InlineKeyboardButton(text="❌ انصراف", callback_data="gens_back")],
        ]
    )


def build_sources_keyboard(subs: list[dict], selected: set[int], page: int = 0) -> InlineKeyboardMarkup:
    """انتخاب چند اشتراک به‌عنوان منبع."""
    start = page * PAGE_SIZE
    chunk = subs[start : start + PAGE_SIZE]
    rows = []
    for sub in chunk:
        mark = "✅" if sub["id"] in selected else "☐"
        label = f"{mark} {sub['name']} ({sub['config_count']})"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"src_toggle:{sub['id']}:{page}")]
        )
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="« قبلی", callback_data=f"src_page:{page - 1}"))
    if start + PAGE_SIZE < len(subs):
        nav.append(InlineKeyboardButton(text="بعدی »", callback_data=f"src_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text="✅ همه", callback_data="src_all"),
            InlineKeyboardButton(text="☐ هیچکدام", callback_data="src_none"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"ادامه ({len(selected)} منبع) ←",
                callback_data="src_done",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_multi")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_multi_select_keyboard(
    pool: list[dict], selected: set[int], page: int = 0
) -> InlineKeyboardMarkup:
    """pool: [{raw, source_name, key}] — انتخاب کانفیگ از چند منبع."""
    start = page * PAGE_SIZE
    chunk = list(range(start, min(start + PAGE_SIZE, len(pool))))
    rows = []
    for i in chunk:
        item = pool[i]
        remark = get_remark(item["raw"]) or "(بدون نام)"
        proto = get_protocol(item["raw"])
        mark = "✅" if i in selected else "☐"
        src = item["source_name"][:12]
        label = f"{mark} [{proto}] {remark[:20]} · {src}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"msel_toggle:{i}:{page}")]
        )
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="« قبلی", callback_data=f"msel_page:{page - 1}"))
    if start + PAGE_SIZE < len(pool):
        nav.append(InlineKeyboardButton(text="بعدی »", callback_data=f"msel_page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append(
        [
            InlineKeyboardButton(text="✅ همه", callback_data="msel_all"),
            InlineKeyboardButton(text="☐ هیچکدام", callback_data="msel_none"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"ادامه ({len(selected)} انتخاب‌شده) ←",
                callback_data="msel_done",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data="cancel_multi")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_gen_detail_keyboard(gen_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📋 مدیریت کانفیگ‌ها", callback_data=f"gen_cfgs:{gen_id}:0")],
            [InlineKeyboardButton(text="📱 نمایش QR Code", callback_data=f"gen_qr:{gen_id}")],
            [InlineKeyboardButton(text="➕ افزودن کانفیگ از یک اشتراک دیگه", callback_data=f"gen_add:{gen_id}")],
            [
                InlineKeyboardButton(text="⏰ تغییر انقضا", callback_data=f"gen_expiry:{gen_id}"),
                InlineKeyboardButton(text="📝 یادداشت", callback_data=f"gen_note:{gen_id}"),
            ],
            [InlineKeyboardButton(text="🗑 حذف این اشتراک", callback_data=f"gen_delete:{gen_id}")],
            [InlineKeyboardButton(text="« بازگشت به لیست", callback_data="gens_back")],
        ]
    )


def build_gen_configs_keyboard(gen_id: int, configs: list[str], page: int = 0) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    chunk = configs[start : start + PAGE_SIZE]
    rows = []
    for i, raw in enumerate(chunk, start=start):
        remark = get_remark(raw) or "(بدون نام)"
        proto = get_protocol(raw)
        label = f"{i + 1}. [{proto}] {remark[:28]}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"gen_cfg:{gen_id}:{i}")])
    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="« قبلی", callback_data=f"gen_cfgs:{gen_id}:{page - 1}"))
    if start + PAGE_SIZE < len(configs):
        nav.append(InlineKeyboardButton(text="بعدی »", callback_data=f"gen_cfgs:{gen_id}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="« بازگشت", callback_data=f"gen_open:{gen_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_gen_cfg_action_keyboard(gen_id: int, idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✏️ تغییر اسم", callback_data=f"gen_cfg_rename:{gen_id}:{idx}")],
            [InlineKeyboardButton(text="🗑 حذف این کانفیگ", callback_data=f"gen_cfg_del:{gen_id}:{idx}")],
            [InlineKeyboardButton(text="« بازگشت به لیست کانفیگ‌ها", callback_data=f"gen_cfgs:{gen_id}:0")],
        ]
    )


def build_configs_keyboard(sub_id: int, configs: list[str], page: int = 0) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    chunk = configs[start : start + PAGE_SIZE]
    rows = []
    for i, raw in enumerate(chunk, start=start):
        remark = get_remark(raw) or "(بدون نام)"
        proto = get_protocol(raw)
        label = f"{i + 1}. [{proto}] {remark[:30]}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"cfg_pick:{sub_id}:{i}")])

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="« قبلی", callback_data=f"cfg_page:{sub_id}:{page - 1}"))
    if start + PAGE_SIZE < len(configs):
        nav.append(InlineKeyboardButton(text="بعدی »", callback_data=f"cfg_page:{sub_id}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(text=BTN_PING, callback_data=f"sub_ping:{sub_id}"),
            InlineKeyboardButton(text=BTN_DELETE_DEAD, callback_data=f"sub_delete_dead:{sub_id}"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text=BTN_EXPORT, callback_data=f"sub_export:{sub_id}"),
            InlineKeyboardButton(text=BTN_EDIT_NOTE, callback_data=f"sub_note:{sub_id}"),
        ]
    )
    rows.append(
        [InlineKeyboardButton(text=BTN_BUILD_CUSTOM, callback_data=f"build_custom:{sub_id}")]
    )
    rows.append(
        [
            InlineKeyboardButton(text=BTN_REFRESH, callback_data=f"sub_refresh:{sub_id}"),
            InlineKeyboardButton(text=BTN_BACK, callback_data="subs_back"),
        ]
    )
    rows.append([InlineKeyboardButton(text=BTN_DELETE, callback_data=f"sub_delete:{sub_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_select_keyboard(
    sub_id: int, configs: list[str], selected: set[int], page: int = 0
) -> InlineKeyboardMarkup:
    """کیبورد انتخاب چندتایی کانفیگ‌ها برای ساخت اشتراک سفارشی."""
    start = page * PAGE_SIZE
    chunk = list(range(start, min(start + PAGE_SIZE, len(configs))))
    rows = []
    for i in chunk:
        remark = get_remark(configs[i]) or "(بدون نام)"
        proto = get_protocol(configs[i])
        mark = "✅" if i in selected else "☐"
        label = f"{mark} {i + 1}. [{proto}] {remark[:26]}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"sel_toggle:{sub_id}:{i}:{page}")]
        )

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="« قبلی", callback_data=f"sel_page:{sub_id}:{page - 1}"))
    if start + PAGE_SIZE < len(configs):
        nav.append(InlineKeyboardButton(text="بعدی »", callback_data=f"sel_page:{sub_id}:{page + 1}"))
    if nav:
        rows.append(nav)

    rows.append(
        [
            InlineKeyboardButton(text="✅ انتخاب همه", callback_data=f"sel_all:{sub_id}"),
            InlineKeyboardButton(text="☐ حذف همه", callback_data=f"sel_none:{sub_id}"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text=f"ادامه ({len(selected)} انتخاب‌شده) ←",
                callback_data=f"sel_done:{sub_id}",
            )
        ]
    )
    rows.append([InlineKeyboardButton(text="❌ انصراف", callback_data=f"sub_open:{sub_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sub_detail_text(sub: dict) -> str:
    text = f"📦 <b>{escape(sub['name'])}</b>\n"
    if sub.get("note"):
        text += f"📝 {escape(sub['note'])}\n"
    updated = _format_updated_at(sub.get("updated_at", ""))
    text += f"🕒 آخرین بروزرسانی: {updated}\n"
    text += f"\n{len(sub['configs'])} کانفیگ — یکی رو انتخاب کن:"
    return text


async def fetch_configs(sub_url: str) -> tuple[bool, list[str] | str]:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(sub_url)
            resp.raise_for_status()
            configs = decode_subscription(resp.text)
    except Exception as e:
        return False, f"خطا در دریافت لینک: {escape(str(e))}"

    if not configs:
        return False, "هیچ کانفیگی توی این اشتراک پیدا نشد."
    return True, configs


def make_public_url(token: str, request_host: str | None = None) -> str:
    if BASE_URL:
        return f"{BASE_URL}/sub/{token}"
    if request_host:
        scheme = "https"
        return f"{scheme}://{request_host}/sub/{token}"
    # fallback
    return f"/sub/{token}"


def make_qr_png(data: str, box_size: int = 8) -> bytes:
    """تولید تصویر PNG کد QR از یک رشته (معمولاً لینک ساب)."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def send_sub_qr(message_or_cb_message, url: str, title: str = "") -> None:
    """ارسال عکس QR لینک اشتراک به چت."""
    try:
        png = make_qr_png(url)
        photo = BufferedInputFile(png, filename="qr.png")
        caption = f"📱 کد QR"
        if title:
            caption = f"📱 کد QR — {title}"
        caption += f"\n\n<code>{escape(url)}</code>"
        await message_or_cb_message.answer_photo(photo, caption=caption, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"send QR failed: {e}")
        await message_or_cb_message.answer(
            f"نتونستم QR بسازم.\nلینک:\n<code>{escape(url)}</code>",
            parse_mode="HTML",
        )


# ===================== HTTP Server (برای سرو سابسکریپشن) =====================

def _wants_html(request: web.Request) -> bool:
    """اگر درخواست از مرورگر باشه True، وگرنه (کلاینت VPN) False."""
    if request.query.get("raw") in ("1", "true", "yes"):
        return False
    accept = (request.headers.get("Accept") or "").lower()
    if "text/html" in accept:
        return True
    ua = (request.headers.get("User-Agent") or "").lower()
    browser_hints = ("mozilla", "chrome", "safari", "firefox", "edge", "opera")
    if any(h in ua for h in browser_hints) and "v2ray" not in ua and "clash" not in ua:
        return True
    return False


def _build_panel_html(gen: dict, public_url: str) -> str:
    """پنل HTML خوشگل برای نمایش اشتراک در مرورگر."""
    name = escape(gen["name"])
    configs = gen["configs"]
    encoded = encode_subscription(configs)
    created = _format_updated_at(gen.get("created_at", ""))
    exp = gen.get("expires_at")
    if exp:
        if storage.is_generated_expired(gen):
            exp_label = f"منقضی‌شده ({_format_updated_at(exp)})"
        else:
            exp_label = _format_updated_at(exp)
    else:
        exp_label = "بدون محدودیت"

    rows_html = []
    for i, raw in enumerate(configs):
        remark = escape(get_remark(raw) or "(بدون نام)")
        proto = escape(get_protocol(raw))
        rows_html.append(
            f"""
            <div class="card" data-idx="{i}">
              <div class="card-top">
                <span class="badge">{proto}</span>
                <span class="remark">{remark}</span>
              </div>
              <button class="btn btn-sm" onclick="copyConfig({i})">کپی کانفیگ</button>
              <script type="application/json" id="cfg-{i}">{escape(raw)}</script>
            </div>
            """
        )

    configs_block = "\n".join(rows_html) if rows_html else '<p class="empty">کانفیگی نیست.</p>'

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{name}</title>
<style>
  :root {{
    --bg: #0b0f19;
    --card: #141b2d;
    --border: #1e293b;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --accent2: #818cf8;
    --ok: #34d399;
    --danger: #f87171;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: "Vazirmatn", "Tahoma", system-ui, sans-serif;
    background: radial-gradient(ellipse at top, #1a1f35 0%, var(--bg) 60%);
    color: var(--text);
    min-height: 100vh;
    padding: 24px 16px 48px;
  }}
  .wrap {{ max-width: 720px; margin: 0 auto; }}
  header {{ text-align: center; margin-bottom: 28px; }}
  header .logo {{
    width: 56px; height: 56px; border-radius: 16px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    display: inline-flex; align-items: center; justify-content: center;
    font-size: 28px; margin-bottom: 12px;
    box-shadow: 0 8px 24px rgba(56,189,248,.25);
  }}
  h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: 6px; }}
  .meta {{ color: var(--muted); font-size: .9rem; }}
  .panel {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 16px; padding: 20px; margin-bottom: 20px;
  }}
  .panel h2 {{
    font-size: .85rem; color: var(--muted);
    text-transform: uppercase; letter-spacing: .04em; margin-bottom: 12px;
  }}
  .url-box {{
    display: flex; gap: 8px; align-items: stretch;
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 10px; padding: 4px 4px 4px 12px;
  }}
  .url-box code {{
    flex: 1; font-size: .8rem; word-break: break-all;
    color: var(--accent); line-height: 1.5; padding: 8px 0;
    direction: ltr; text-align: left;
  }}
  .btn {{
    border: none; cursor: pointer;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: #0b0f19; font-weight: 700;
    padding: 10px 16px; border-radius: 8px;
    font-size: .85rem; white-space: nowrap;
    transition: transform .15s, opacity .15s;
  }}
  .btn:hover {{ opacity: .9; transform: translateY(-1px); }}
  .btn:active {{ transform: scale(.97); }}
  .btn-sm {{ padding: 6px 12px; font-size: .75rem; background: var(--border); color: var(--text); }}
  .btn-sm:hover {{ background: #334155; }}
  .actions {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }}
  .actions .btn {{ flex: 1; min-width: 120px; text-align: center; }}
  .grid {{ display: flex; flex-direction: column; gap: 10px; }}
  .qr-wrap {{
    display: flex; align-items: center; justify-content: center;
    background: #ffffff; border-radius: 14px; padding: 16px;
    margin-bottom: 10px;
  }}
  .qr-wrap svg {{ width: 200px; height: 200px; display: block; }}
  .qr-hint {{ color: var(--muted); font-size: .78rem; text-align: center; }}
  .card {{
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 12px; padding: 12px 14px;
    display: flex; align-items: center; gap: 12px;
  }}
  .card-top {{ flex: 1; min-width: 0; }}
  .badge {{
    display: inline-block; font-size: .7rem; font-weight: 700;
    padding: 2px 8px; border-radius: 6px;
    background: rgba(56,189,248,.15); color: var(--accent);
    margin-bottom: 4px; text-transform: uppercase;
  }}
  .remark {{ display: block; font-size: .9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .empty {{ color: var(--muted); text-align: center; padding: 24px; }}
  .toast {{
    position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%) translateY(80px);
    background: var(--ok); color: #064e3b; padding: 10px 20px; border-radius: 999px;
    font-weight: 700; font-size: .9rem; opacity: 0; transition: .3s; z-index: 99;
    box-shadow: 0 8px 24px rgba(0,0,0,.3);
  }}
  .toast.show {{ opacity: 1; transform: translateX(-50%) translateY(0); }}
  footer {{ text-align: center; margin-top: 32px; color: var(--muted); font-size: .75rem; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo">⚡</div>
    <h1>{name}</h1>
    <p class="meta">{len(configs)} کانفیگ · ساخته‌شده {created} · انقضا: {exp_label}</p>
  </header>
  <div class="panel">
    <h2>لینک اشتراک</h2>
    <div class="url-box">
      <code id="sub-url">{escape(public_url)}</code>
      <button class="btn" onclick="copyText(document.getElementById('sub-url').textContent)">کپی لینک</button>
    </div>
    <div class="actions">
      <button class="btn" onclick="copyText(SUB_B64)">کپی محتوای اشتراک</button>
      <a class="btn" href="?raw=1" style="text-decoration:none;display:inline-block;text-align:center">دانلود فایل</a>
    </div>
  </div>
  <div class="panel">
    <h2>کد QR</h2>
    <div class="qr-wrap" id="qr-wrap"></div>
    <p class="qr-hint">با اسکن این کد توسط اپلیکیشن VPN، اشتراک مستقیم اضافه می‌شود</p>
  </div>
  <div class="panel">
    <h2>کانفیگ‌ها</h2>
    <div class="grid">{configs_block}</div>
  </div>
  <footer>برای استفاده در کلاینت، لینک اشتراک را اضافه کنید</footer>
</div>
<div class="toast" id="toast">کپی شد ✓</div>
<script src="/panel/static/vendor/qrcode.js"></script>
<script>
const SUB_B64 = {repr(encoded)};
const SUB_URL = {repr(public_url)};
function showToast(msg) {{
  const t = document.getElementById('toast');
  t.textContent = msg || 'کپی شد ✓';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}}
async function copyText(text) {{
  try {{
    await navigator.clipboard.writeText(text);
    showToast('کپی شد ✓');
  }} catch (e) {{
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); document.body.removeChild(ta);
    showToast('کپی شد ✓');
  }}
}}
function copyConfig(i) {{
  const el = document.getElementById('cfg-' + i);
  if (el) copyText(el.textContent);
}}
(function renderQr() {{
  try {{
    const qr = qrcode(0, 'M');
    qr.addData(SUB_URL);
    qr.make();
    document.getElementById('qr-wrap').innerHTML = qr.createSvgTag({{ cellSize: 5, margin: 2 }});
  }} catch (e) {{
    document.getElementById('qr-wrap').outerHTML = '';
  }}
}})();
</script>
</body>
</html>
"""


def _sub_userinfo(gen: dict) -> str:
    expire = 0
    exp = gen.get("expires_at")
    if exp:
        try:
            expire = int(datetime.fromisoformat(exp).timestamp())
        except Exception:
            expire = 0
    return f"upload=0; download=0; total=0; expire={expire}"


async def refresh_source_subs_for_gen(gen: dict) -> None:
    """قبل از resolve، لینک‌های منبع را از اینترنت دوباره می‌گیرد تا کانفیگ‌ها لایو باشند."""
    if not gen.get("items"):
        return
    user_id = gen.get("user_id")
    if user_id is None:
        return
    for sub_id in storage.get_source_sub_ids(gen):
        sub = storage.get_sub(sub_id, user_id)
        if not sub or not sub.get("sub_url"):
            continue
        ok, result = await fetch_configs(sub["sub_url"])
        if ok and isinstance(result, list):
            storage.update_configs(sub_id, user_id, result)


async def handle_sub(request: web.Request) -> web.Response:
    # پاک‌سازی خودکار اشتراک‌های منقضی‌شدهٔ قدیمی (بیش از ۷ روز)
    try:
        storage.cleanup_old_expired_generated(grace_days=7)
    except Exception:
        pass

    token = request.match_info.get("token", "")
    gen = storage.get_generated_by_token(token)
    if not gen:
        return web.Response(text="Subscription not found", status=404, content_type="text/plain")

    # کانفیگ فیک نمایش‌دهنده وضعیت اعتبار (مشترک با config_parser)
    info_cfg = make_expiry_info_config(gen.get("expires_at"))

    if storage.is_generated_expired(gen):
        # وقتی منقضی شد: فقط کانفیگ فیک «منقضی شده» باقی می‌ماند و بقیه پاک می‌شوند
        live_configs = [info_cfg]
    else:
        # ۱) ری‌فچ منابع از اینترنت  ۲) ساخت کانفیگ‌های لایو از روی recipe
        try:
            await refresh_source_subs_for_gen(gen)
        except Exception as e:
            logger.warning(f"refresh sources failed for token={token}: {e}")
        live_configs = storage.resolve_generated_configs(gen, persist=True)
        # اضافه کردن کانفیگ فیک در ابتدای لیست
        live_configs = [info_cfg] + live_configs

    body = encode_subscription(live_configs)

    sub_headers = {
        "profile-title": gen["name"],
        "subscription-userinfo": _sub_userinfo(gen),
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache",
        "profile-update-interval": "1",
    }

    if not _wants_html(request):
        return web.Response(
            text=body,
            content_type="text/plain",
            charset="utf-8",
            headers=sub_headers,
        )

    host = request.headers.get("Host", "")
    if BASE_URL:
        public_url = make_public_url(token)
    else:
        public_url = make_public_url(token, request_host=host)

    gen = dict(gen)
    gen["configs"] = live_configs
    html = _build_panel_html(gen, public_url)
    return web.Response(text=html, content_type="text/html", charset="utf-8", headers={
        "Cache-Control": "no-cache, no-store, must-revalidate",
    })


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_web_app() -> web.Application:
    app = web.Application(middlewares=[auth.auth_middleware])
    app.router.add_get("/sub/{token}", handle_sub)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_health)
    panel_pages.add_routes(app)
    panel_api.add_routes(app)
    return app# ===================== Telegram Handlers =====================

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer("سلام! از دکمه‌های زیر استفاده کن:", reply_markup=main_menu())


# ---------- افزودن اشتراک ----------

@dp.message(F.text == BTN_ADD_SUB)
async def ask_for_link(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await message.answer("اجازه دسترسی نداری.")
    await state.set_state(AddSubState.waiting_for_link)
    await message.answer("لینک اشتراک رو بفرست:")


@dp.message(AddSubState.waiting_for_link)
async def receive_link(message: Message, state: FSMContext):
    if await bail_if_menu_button(message, state):
        return
    sub_url = message.text.strip()
    status = await message.answer("در حال بررسی لینک...")
    ok, result = await fetch_configs(sub_url)
    if not ok:
        return await status.edit_text(result)
    await status.edit_text(f"{len(result)} کانفیگ پیدا شد.")
    await state.update_data(sub_url=sub_url, configs=result)
    await state.set_state(AddSubState.waiting_for_name)
    await message.answer("یه اسم برای این اشتراک بذار (مثلا: آمریکا - محمد):")


@dp.message(AddSubState.waiting_for_name)
async def receive_name(message: Message, state: FSMContext):
    if await bail_if_menu_button(message, state):
        return
    name = message.text.strip()
    await state.update_data(name=name)
    await state.set_state(AddSubState.waiting_for_note)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_NOTE_SKIP, callback_data="note_skip")]]
    )
    await message.answer("اگه یادداشتی هم می‌خوای بذاری بفرست، وگرنه دکمه زیر رو بزن:", reply_markup=kb)


async def _finish_add_sub(user_id: int, state: FSMContext, note: str) -> str:
    data = await state.get_data()
    storage.add_sub(user_id, data["name"], note, data["sub_url"], data["configs"])
    await state.clear()
    return f"اشتراک «{data['name']}» با {len(data['configs'])} کانفیگ ذخیره شد."


@dp.message(AddSubState.waiting_for_note)
async def receive_note(message: Message, state: FSMContext):
    if await bail_if_menu_button(message, state):
        return
    note = message.text.strip()
    text = await _finish_add_sub(message.from_user.id, state, note)
    await message.answer(text, reply_markup=main_menu())


@dp.callback_query(F.data == "note_skip", AddSubState.waiting_for_note)
async def skip_note(callback: CallbackQuery, state: FSMContext):
    text = await _finish_add_sub(callback.from_user.id, state, "")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(text, reply_markup=main_menu())
    await callback.answer()


# ---------- لیست اشتراک‌ها ----------

@dp.message(F.text == BTN_LIST)
async def list_subs(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("اجازه دسترسی نداری.")
    subs = storage.list_subs(message.from_user.id)
    if not subs:
        return await message.answer(f"هنوز اشتراکی اضافه نکردی. از دکمه «{BTN_ADD_SUB}» استفاده کن.")
    await message.answer("یکی از اشتراک‌ها رو انتخاب کن:", reply_markup=build_subs_keyboard(subs))


@dp.callback_query(F.data.startswith("subs_page:"))
async def paginate_subs(callback: CallbackQuery):
    page = int(callback.data.split(":")[1])
    subs = storage.list_subs(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=build_subs_keyboard(subs, page))
    await callback.answer()


@dp.callback_query(F.data == "subs_back")
async def back_to_subs(callback: CallbackQuery):
    subs = storage.list_subs(callback.from_user.id)
    await callback.message.edit_text("یکی از اشتراک‌ها رو انتخاب کن:", reply_markup=build_subs_keyboard(subs))
    await callback.answer()


@dp.callback_query(F.data.startswith("sub_open:"))
async def open_sub(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    await callback.message.edit_text(
        sub_detail_text(sub), reply_markup=build_configs_keyboard(sub_id, sub["configs"]), parse_mode="HTML"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("cfg_page:"))
async def paginate_configs(callback: CallbackQuery):
    _, sub_id, page = callback.data.split(":")
    sub_id, page = int(sub_id), int(page)
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=build_configs_keyboard(sub_id, sub["configs"], page))
    await callback.answer()


@dp.callback_query(F.data.startswith("sub_refresh:"))
async def refresh_sub(callback: CallbackQuery):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    await callback.answer("در حال بروزرسانی...")
    ok, result = await fetch_configs(sub["sub_url"])
    if not ok:
        return await callback.message.answer(result)
    storage.update_configs(sub_id, callback.from_user.id, result)
    sub = storage.get_sub(sub_id, callback.from_user.id)
    await callback.message.edit_text(
        sub_detail_text(sub), reply_markup=build_configs_keyboard(sub_id, result), parse_mode="HTML"
    )


# ---------- خروجی اشتراک ----------

@dp.callback_query(F.data.startswith("sub_export:"))
async def export_sub(callback: CallbackQuery):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    if not sub["configs"]:
        return await callback.answer("هیچ کانفیگی برای خروجی وجود نداره.", show_alert=True)

    encoded = encode_subscription(sub["configs"])
    text = (
        f"📤 <b>خروجی اشتراک «{escape(sub['name'])}»</b>\n"
        f"تعداد کانفیگ: {len(sub['configs'])}\n\n"
        f"<code>{encoded}</code>"
    )
    if len(text) > 4000:
        await callback.message.answer(
            f"📤 خروجی اشتراک «{escape(sub['name'])}» ({len(sub['configs'])} کانفیگ):\n\n"
            f"<code>{encoded[:3500]}</code>",
            parse_mode="HTML",
        )
        remaining = encoded[3500:]
        while remaining:
            chunk = remaining[:3500]
            remaining = remaining[3500:]
            await callback.message.answer(f"<code>{chunk}</code>", parse_mode="HTML")
    else:
        await callback.message.answer(text, parse_mode="HTML")
    await callback.answer("خروجی آماده شد ✅")


# ---------- ساخت اشتراک سفارشی ----------

@dp.callback_query(F.data.startswith("build_custom:"))
async def start_build_custom(callback: CallbackQuery, state: FSMContext):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    if not sub["configs"]:
        return await callback.answer("هیچ کانفیگی وجود نداره.", show_alert=True)

    await state.set_state(BuildCustomState.selecting)
    await state.update_data(
        sub_id=sub_id, selected=set(), rename_queue=[], renamed_configs=[],
        multi_pool=None, multi_fake_configs=None,
    )

    await callback.message.edit_text(
        f"🛠 <b>ساخت اشتراک سفارشی از «{escape(sub['name'])}»</b>\n\n"
        "کانفیگ‌هایی که می‌خوای رو تیک بزن، بعد «ادامه» رو بزن:",
        reply_markup=build_select_keyboard(sub_id, sub["configs"], set()),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("sel_toggle:"), BuildCustomState.selecting)
async def toggle_select(callback: CallbackQuery, state: FSMContext):
    _, sub_id, idx, page = callback.data.split(":")
    sub_id, idx, page = int(sub_id), int(idx), int(page)
    data = await state.get_data()
    selected: set = data.get("selected", set())
    if idx in selected:
        selected.discard(idx)
    else:
        selected.add(idx)
    await state.update_data(selected=selected)

    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("اشتراک پیدا نشد.", show_alert=True)

    await callback.message.edit_reply_markup(
        reply_markup=build_select_keyboard(sub_id, sub["configs"], selected, page)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("sel_page:"), BuildCustomState.selecting)
async def select_page(callback: CallbackQuery, state: FSMContext):
    _, sub_id, page = callback.data.split(":")
    sub_id, page = int(sub_id), int(page)
    data = await state.get_data()
    selected = data.get("selected", set())
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("اشتراک پیدا نشد.", show_alert=True)
    await callback.message.edit_reply_markup(
        reply_markup=build_select_keyboard(sub_id, sub["configs"], selected, page)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("sel_all:"), BuildCustomState.selecting)
async def select_all(callback: CallbackQuery, state: FSMContext):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("اشتراک پیدا نشد.", show_alert=True)
    selected = set(range(len(sub["configs"])))
    await state.update_data(selected=selected)
    await callback.message.edit_reply_markup(
        reply_markup=build_select_keyboard(sub_id, sub["configs"], selected)
    )
    await callback.answer(f"{len(selected)} کانفیگ انتخاب شد")


@dp.callback_query(F.data.startswith("sel_none:"), BuildCustomState.selecting)
async def select_none(callback: CallbackQuery, state: FSMContext):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("اشتراک پیدا نشد.", show_alert=True)
    await state.update_data(selected=set())
    await callback.message.edit_reply_markup(
        reply_markup=build_select_keyboard(sub_id, sub["configs"], set())
    )
    await callback.answer("انتخاب‌ها پاک شد")


@dp.callback_query(F.data.startswith("sel_done:"), BuildCustomState.selecting)
async def select_done(callback: CallbackQuery, state: FSMContext):
    sub_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected: set = data.get("selected", set())
    if not selected:
        return await callback.answer("حداقل یک کانفیگ انتخاب کن.", show_alert=True)

    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("اشتراک پیدا نشد.", show_alert=True)

    queue = sorted(selected)
    source_items = []
    for idx in queue:
        raw = sub["configs"][idx]
        source_items.append({
            "sub_id": sub_id,
            "index": idx,
            "fp": config_fingerprint(raw),
            "name": "",
        })
    await state.update_data(
        rename_queue=queue,
        renamed_configs=[],
        current_rename_idx=0,
        source_items=source_items,
    )
    await state.set_state(BuildCustomState.renaming)

    first_idx = queue[0]
    raw = sub["configs"][first_idx]
    remark = get_remark(raw) or "(بدون نام)"
    proto = get_protocol(raw)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ رد کردن (همان اسم قبلی)", callback_data="rename_skip")],
            [InlineKeyboardButton(text="❌ انصراف از ساخت", callback_data=f"sub_open:{sub_id}")],
        ]
    )
    await callback.message.edit_text(
        f"✏️ رنیم کانفیگ ۱ از {len(queue)}\n\n"
        f"[{proto}] {escape(remark)}\n\n"
        "اسم جدید رو بفرست، یا «رد کردن» رو بزن:",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


def _resolve_rename_context(data: dict, user_id: int):
    """برای تک‌منبع از DB، برای چندمنبع از multi_fake_configs."""
    sub_id = data.get("sub_id")
    fake = data.get("multi_fake_configs")
    if fake is not None and sub_id == -1:
        return {"id": -1, "configs": fake}
    return storage.get_sub(sub_id, user_id)


@dp.message(BuildCustomState.renaming)
async def receive_rename(message: Message, state: FSMContext):
    if await bail_if_menu_button(message, state):
        return

    data = await state.get_data()
    queue: list = data["rename_queue"]
    current = data["current_rename_idx"]
    renamed: list = data.get("renamed_configs", [])

    sub = _resolve_rename_context(data, message.from_user.id)
    if not sub:
        await state.clear()
        return await message.answer("اشتراک پیدا نشد.")

    idx = queue[current]
    raw = sub["configs"][idx]
    new_name = message.text.strip()
    renamed.append(rename_config(raw, new_name))
    source_items = list(data.get("source_items") or [])
    if current < len(source_items):
        source_items[current]["name"] = new_name
        await state.update_data(source_items=source_items)

    await _advance_rename(message, state, sub, queue, current, renamed)


@dp.callback_query(F.data == "rename_skip", BuildCustomState.renaming)
async def rename_skip(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    queue: list = data["rename_queue"]
    current = data["current_rename_idx"]
    renamed: list = data.get("renamed_configs", [])

    sub = _resolve_rename_context(data, callback.from_user.id)
    if not sub:
        await state.clear()
        return await callback.answer("اشتراک پیدا نشد.", show_alert=True)

    idx = queue[current]
    renamed.append(sub["configs"][idx])

    await callback.message.edit_reply_markup(reply_markup=None)
    await _advance_rename(callback.message, state, sub, queue, current, renamed, from_callback=True)
    await callback.answer()


async def _advance_rename(
    message_or_cb_msg,
    state: FSMContext,
    sub: dict,
    queue: list,
    current: int,
    renamed: list,
    from_callback: bool = False,
):
    next_idx = current + 1
    if next_idx >= len(queue):
        await state.update_data(renamed_configs=renamed)
        await state.set_state(BuildCustomState.waiting_sub_name)
        await message_or_cb_msg.answer(
            f"✅ {len(renamed)} کانفیگ آماده شد.\n\n"
            "حالا یک <b>اسم</b> برای این اشتراک سفارشی بفرست\n"
            "(مثلاً: انتخابی من - گیمینگ):",
            parse_mode="HTML",
        )
        return

    await state.update_data(current_rename_idx=next_idx, renamed_configs=renamed)
    idx = queue[next_idx]
    raw = sub["configs"][idx]
    remark = get_remark(raw) or "(بدون نام)"
    proto = get_protocol(raw)

    cancel_cb = "cancel_multi" if sub.get("id") == -1 else f"sub_open:{sub['id']}"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ رد کردن (همان اسم قبلی)", callback_data="rename_skip")],
            [InlineKeyboardButton(text="❌ انصراف از ساخت", callback_data=cancel_cb)],
        ]
    )
    await message_or_cb_msg.answer(
        f"✏️ رنیم کانفیگ {next_idx + 1} از {len(queue)}\n\n"
        f"[{proto}] {escape(remark)}\n\n"
        "اسم جدید رو بفرست، یا «رد کردن» رو بزن:",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.message(BuildCustomState.waiting_sub_name)
async def receive_custom_name(message: Message, state: FSMContext):
    if await bail_if_menu_button(message, state):
        return

    name = message.text.strip()
    if not name:
        return await message.answer("اسم نمی‌تونه خالی باشه. دوباره بفرست:")

    data = await state.get_data()
    renamed = data.get("renamed_configs", [])
    if not renamed:
        await state.clear()
        return await message.answer("خطا: هیچ کانفیگی آماده نشد.")

    await state.update_data(custom_name=name)
    await state.set_state(BuildCustomState.waiting_expiry)
    await message.answer(
        f"اسم: <b>{escape(name)}</b>\n"
        f"تعداد کانفیگ: {len(renamed)}\n\n"
        "مدت اعتبار این اشتراک رو انتخاب کن:",
        reply_markup=build_expiry_keyboard(),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.startswith("expiry:"), BuildCustomState.waiting_expiry)
async def finish_custom_with_expiry(callback: CallbackQuery, state: FSMContext):
    days = int(callback.data.split(":")[1])
    data = await state.get_data()
    name = data.get("custom_name", "")
    renamed = data.get("renamed_configs", [])
    if not name or not renamed:
        await state.clear()
        return await callback.answer("خطا — دوباره شروع کن.", show_alert=True)

    expires_at = None
    if days > 0:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()

    source_items = data.get("source_items")
    gen_id, token = storage.create_generated_sub(
        callback.from_user.id, name, renamed, expires_at=expires_at, items=source_items
    )
    await state.clear()

    exp_txt = f"{days} روز" if days else "بدون انقضا"
    if BASE_URL:
        url = make_public_url(token)
        msg = (
            f"✅ اشتراک سفارشی «{escape(name)}» ساخته شد.\n"
            f"📦 {len(renamed)} کانفیگ · ⏰ {exp_txt}\n\n"
            f"🔗 لینک اشتراک:\n<code>{url}</code>\n\n"
            "این لینک رو می‌تونی مستقیم توی کلاینت‌ها اضافه کنی."
        )
    else:
        url = f"/sub/{token}"
        msg = (
            f"✅ اشتراک سفارشی «{escape(name)}» ساخته شد.\n"
            f"📦 {len(renamed)} کانفیگ · ⏰ {exp_txt}\n\n"
            f"لینک:\n<code>https://YOUR-RAILWAY-DOMAIN/sub/{token}</code>\n\n"
            "⚠️ متغیر <code>BASE_URL</code> رو در Railway ست کن."
        )

    await callback.message.edit_text(msg, parse_mode="HTML")
    if BASE_URL:
        await send_sub_qr(callback.message, url, title=name)
    await callback.message.answer(
        "منوی اصلی:",
        reply_markup=main_menu(),
    )
    await callback.answer("ساخته شد ✅")


# ---------- ساخت از چند منبع ----------

@dp.message(F.text == BTN_MULTI_BUILD)
async def start_multi_build(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await message.answer("اجازه دسترسی نداری.")
    subs = storage.list_subs(message.from_user.id)
    if len(subs) < 1:
        return await message.answer("اول حداقل یک اشتراک اضافه کن.")
    await state.set_state(BuildCustomState.selecting_sources)
    await state.update_data(selected_sources=set(), multi_pool=[], selected=set(), target_gen_id=None)
    await message.answer(
        "🌐 <b>ساخت اشتراک از چند منبع</b>\n\n"
        "اشتراک‌هایی که می‌خوای ازشون کانفیگ بگیری رو تیک بزن:",
        reply_markup=build_sources_keyboard(subs, set()),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.regexp(r"^gen_add:\d+$"))
async def start_add_to_generated(callback: CallbackQuery, state: FSMContext):
    gen_id = int(callback.data.split(":")[1])
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)

    subs = storage.list_subs(callback.from_user.id)
    if len(subs) < 1:
        return await callback.answer("اول حداقل یک اشتراک اضافه کن.", show_alert=True)

    await state.set_state(BuildCustomState.selecting_sources)
    await state.update_data(
        selected_sources=set(), multi_pool=[], selected=set(), target_gen_id=gen_id
    )
    await callback.message.edit_text(
        f"➕ <b>افزودن کانفیگ به «{escape(g['name'])}»</b>\n\n"
        "اشتراک‌هایی که می‌خوای ازشون کانفیگ برداری رو تیک بزن:",
        reply_markup=build_sources_keyboard(subs, set()),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data == "cancel_multi")
async def cancel_multi(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("انصراف داده شد.")
    await callback.message.answer("منوی اصلی:", reply_markup=main_menu())
    await callback.answer()


@dp.callback_query(F.data.startswith("src_toggle:"), BuildCustomState.selecting_sources)
async def src_toggle(callback: CallbackQuery, state: FSMContext):
    _, sid, page = callback.data.split(":")
    sid, page = int(sid), int(page)
    data = await state.get_data()
    selected: set = set(data.get("selected_sources", set()))
    if sid in selected:
        selected.discard(sid)
    else:
        selected.add(sid)
    await state.update_data(selected_sources=selected)
    subs = storage.list_subs(callback.from_user.id)
    await callback.message.edit_reply_markup(
        reply_markup=build_sources_keyboard(subs, selected, page)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("src_page:"), BuildCustomState.selecting_sources)
async def src_page(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected = set(data.get("selected_sources", set()))
    subs = storage.list_subs(callback.from_user.id)
    await callback.message.edit_reply_markup(
        reply_markup=build_sources_keyboard(subs, selected, page)
    )
    await callback.answer()


@dp.callback_query(F.data == "src_all", BuildCustomState.selecting_sources)
async def src_all(callback: CallbackQuery, state: FSMContext):
    subs = storage.list_subs(callback.from_user.id)
    selected = {s["id"] for s in subs}
    await state.update_data(selected_sources=selected)
    await callback.message.edit_reply_markup(
        reply_markup=build_sources_keyboard(subs, selected)
    )
    await callback.answer(f"{len(selected)} منبع انتخاب شد")


@dp.callback_query(F.data == "src_none", BuildCustomState.selecting_sources)
async def src_none(callback: CallbackQuery, state: FSMContext):
    await state.update_data(selected_sources=set())
    subs = storage.list_subs(callback.from_user.id)
    await callback.message.edit_reply_markup(
        reply_markup=build_sources_keyboard(subs, set())
    )
    await callback.answer("انتخاب‌ها پاک شد")


@dp.callback_query(F.data == "src_done", BuildCustomState.selecting_sources)
async def src_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    selected = set(data.get("selected_sources", set()))
    if not selected:
        return await callback.answer("حداقل یک منبع انتخاب کن.", show_alert=True)

    pool = []
    for sid in sorted(selected):
        sub = storage.get_sub(sid, callback.from_user.id)
        if not sub:
            continue
        for raw in sub["configs"]:
            pool.append({"raw": raw, "source_name": sub["name"], "sub_id": sid})

    if not pool:
        return await callback.answer("این منابع کانفیگی ندارن.", show_alert=True)

    await state.update_data(multi_pool=pool, selected=set(), sub_id=None)
    await state.set_state(BuildCustomState.selecting)
    await callback.message.edit_text(
        f"🌐 <b>{len(selected)} منبع</b> · {len(pool)} کانفیگ\n\n"
        "کانفیگ‌هایی که می‌خوای رو تیک بزن:",
        reply_markup=build_multi_select_keyboard(pool, set()),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("msel_toggle:"), BuildCustomState.selecting)
async def msel_toggle(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pool = data.get("multi_pool")
    if not pool:
        return
    _, idx, page = callback.data.split(":")
    idx, page = int(idx), int(page)
    selected: set = set(data.get("selected", set()))
    if idx in selected:
        selected.discard(idx)
    else:
        selected.add(idx)
    await state.update_data(selected=selected)
    await callback.message.edit_reply_markup(
        reply_markup=build_multi_select_keyboard(pool, selected, page)
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("msel_page:"), BuildCustomState.selecting)
async def msel_page(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pool = data.get("multi_pool")
    if not pool:
        return
    page = int(callback.data.split(":")[1])
    selected = set(data.get("selected", set()))
    await callback.message.edit_reply_markup(
        reply_markup=build_multi_select_keyboard(pool, selected, page)
    )
    await callback.answer()


@dp.callback_query(F.data == "msel_all", BuildCustomState.selecting)
async def msel_all(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pool = data.get("multi_pool")
    if not pool:
        return
    selected = set(range(len(pool)))
    await state.update_data(selected=selected)
    await callback.message.edit_reply_markup(
        reply_markup=build_multi_select_keyboard(pool, selected)
    )
    await callback.answer(f"{len(selected)} کانفیگ")


@dp.callback_query(F.data == "msel_none", BuildCustomState.selecting)
async def msel_none(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pool = data.get("multi_pool")
    if not pool:
        return
    await state.update_data(selected=set())
    await callback.message.edit_reply_markup(
        reply_markup=build_multi_select_keyboard(pool, set())
    )
    await callback.answer("پاک شد")


@dp.callback_query(F.data == "msel_done", BuildCustomState.selecting)
async def msel_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    pool = data.get("multi_pool")
    if not pool:
        return
    selected = set(data.get("selected", set()))
    if not selected:
        return await callback.answer("حداقل یک کانفیگ انتخاب کن.", show_alert=True)

    queue = sorted(selected)
    picked_raw = [pool[i]["raw"] for i in queue]

    target_gen_id = data.get("target_gen_id")
    if target_gen_id:
        g = storage.get_generated_by_id(target_gen_id, callback.from_user.id)
        await state.clear()
        if not g:
            return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
        new_items = []
        for i in queue:
            it = pool[i]
            new_items.append({
                "sub_id": it["sub_id"],
                "index": it.get("index", 0),
                "fp": config_fingerprint(it["raw"]),
                "name": "",
            })
        total = storage.add_configs_to_generated(
            target_gen_id, callback.from_user.id, picked_raw, new_items=new_items
        )
        g = storage.get_generated_by_id(target_gen_id, callback.from_user.id)
        await callback.message.edit_text(
            f"✅ {len(picked_raw)} کانفیگ به «{escape(g['name'])}» اضافه شد.\n"
            f"📦 مجموع الان: {total} کانفیگ\n\n"
            "لینک اشتراک همون قبلیه، عوض نشده.",
            parse_mode="HTML",
        )
        await callback.message.answer(
            gen_detail_text(g), reply_markup=build_gen_detail_keyboard(target_gen_id), parse_mode="HTML"
        )
        await callback.answer("اضافه شد ✅")
        return

    fake_configs = picked_raw
    source_items = []
    for i in queue:
        it = pool[i]
        source_items.append({
            "sub_id": it["sub_id"],
            "index": it.get("index", 0),
            "fp": config_fingerprint(it["raw"]),
            "name": "",
        })
    await state.update_data(
        rename_queue=list(range(len(fake_configs))),
        renamed_configs=[],
        current_rename_idx=0,
        multi_fake_configs=fake_configs,
        sub_id=-1,
        source_items=source_items,
    )
    await state.set_state(BuildCustomState.renaming)

    raw = fake_configs[0]
    remark = get_remark(raw) or "(بدون نام)"
    proto = get_protocol(raw)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ رد کردن (همان اسم قبلی)", callback_data="rename_skip")],
            [InlineKeyboardButton(text="❌ انصراف از ساخت", callback_data="cancel_multi")],
        ]
    )
    await callback.message.edit_text(
        f"✏️ رنیم کانفیگ ۱ از {len(fake_configs)}\n\n"
        f"[{proto}] {escape(remark)}\n\n"
        "اسم جدید رو بفرست، یا «رد کردن» رو بزن:",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


# ---------- لیست اشتراک‌های سفارشی من ----------

@dp.message(F.text == BTN_MY_GENERATED)
async def list_my_generated(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("اجازه دسترسی نداری.")

    gens = storage.list_generated_subs(message.from_user.id)
    if not gens:
        return await message.answer(
            "هنوز اشتراک سفارشی نساختی.\n"
            "از داخل یکی از اشتراک‌ها دکمه «🛠 ساخت اشتراک سفارشی» رو بزن."
        )

    await message.answer(
        "یکی از اشتراک‌های سفارشی رو انتخاب کن:",
        reply_markup=build_generated_keyboard(gens),
    )


@dp.callback_query(F.data.startswith("gens_page:"))
async def paginate_gens(callback: CallbackQuery):
    page = int(callback.data.split(":")[1])
    gens = storage.list_generated_subs(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=build_generated_keyboard(gens, page))
    await callback.answer()


@dp.callback_query(F.data == "gens_back")
async def back_to_gens(callback: CallbackQuery):
    gens = storage.list_generated_subs(callback.from_user.id)
    if not gens:
        await callback.message.edit_text("هیچ اشتراک سفارشی‌ای نداری.")
        await callback.answer()
        return
    await callback.message.edit_text(
        "یکی از اشتراک‌های سفارشی رو انتخاب کن:",
        reply_markup=build_generated_keyboard(gens),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("gen_open:"))
async def open_generated(callback: CallbackQuery):
    gen_id = int(callback.data.split(":")[1])
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)

    # همگام‌سازی لایو با منابع
    try:
        await refresh_source_subs_for_gen(g)
        storage.resolve_generated_configs(g, persist=True)
        g = storage.get_generated_by_id(gen_id, callback.from_user.id) or g
    except Exception:
        pass

    await callback.message.edit_text(
        gen_detail_text(g),
        reply_markup=build_gen_detail_keyboard(gen_id),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.regexp(r"^gen_qr:\d+$"))
async def send_generated_qr(callback: CallbackQuery):
    gen_id = int(callback.data.split(":")[1])
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    if not BASE_URL:
        return await callback.answer("BASE_URL ست نشده — لینک کامل ساخته نمی‌شود.", show_alert=True)
    url = make_public_url(g["token"])
    await callback.answer()
    await send_sub_qr(callback.message, url, title=g["name"])


@dp.callback_query(F.data.regexp(r"^gen_cfgs:\d+:\d+$"))
async def gen_list_configs(callback: CallbackQuery):
    _, gen_id, page = callback.data.split(":")
    gen_id, page = int(gen_id), int(page)
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    try:
        await refresh_source_subs_for_gen(g)
    except Exception:
        pass
    configs = storage.resolve_generated_configs(g, persist=True)
    if not configs:
        return await callback.answer("کانفیگی نیست.", show_alert=True)
    await callback.message.edit_text(
        f"📋 کانفیگ‌های «{escape(g['name'])}» — یکی را انتخاب کن:",
        reply_markup=build_gen_configs_keyboard(gen_id, configs, page),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.regexp(r"^gen_cfg:\d+:\d+$"))
async def gen_cfg_pick(callback: CallbackQuery):
    _, gen_id, idx = callback.data.split(":")
    gen_id, idx = int(gen_id), int(idx)
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    configs = storage.resolve_generated_configs(g, persist=True)
    if idx < 0 or idx >= len(configs):
        return await callback.answer("ایندکس نامعتبر.", show_alert=True)
    raw = configs[idx]
    remark = get_remark(raw) or "(بدون نام)"
    proto = get_protocol(raw)
    await callback.message.edit_text(
        f"⚙️ <b>[{escape(proto)}]</b> {escape(remark)}\n\n"
        f"اشتراک: {escape(g['name'])}",
        reply_markup=build_gen_cfg_action_keyboard(gen_id, idx),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.regexp(r"^gen_cfg_del:\d+:\d+$"))
async def gen_cfg_del_ask(callback: CallbackQuery):
    _, gen_id, idx = callback.data.split(":")
    gen_id, idx = int(gen_id), int(idx)
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    configs = storage.resolve_generated_configs(g, persist=True)
    if idx < 0 or idx >= len(configs):
        return await callback.answer("ایندکس نامعتبر.", show_alert=True)
    remark = get_remark(configs[idx]) or "(بدون نام)"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ حذف کن", callback_data=f"gen_cfg_del_yes:{gen_id}:{idx}"),
                InlineKeyboardButton(text="❌ انصراف", callback_data=f"gen_cfg:{gen_id}:{idx}"),
            ]
        ]
    )
    await callback.message.edit_text(
        f"مطمئنی می‌خوای کانفیگ «{escape(remark)}» از این اشتراک حذف بشه؟",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.regexp(r"^gen_cfg_del_yes:\d+:\d+$"))
async def gen_cfg_del_yes(callback: CallbackQuery):
    _, gen_id, idx = callback.data.split(":")
    gen_id, idx = int(gen_id), int(idx)
    total = storage.delete_config_from_generated(gen_id, callback.from_user.id, idx)
    if total is None:
        return await callback.answer("خطا در حذف.", show_alert=True)
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    await callback.answer("حذف شد ✅")
    if not g or total == 0:
        await callback.message.edit_text(
            gen_detail_text(g) if g else "اشتراک خالی شد.",
            reply_markup=build_gen_detail_keyboard(gen_id) if g else main_menu(),
            parse_mode="HTML",
        )
        return
    configs = storage.resolve_generated_configs(g, persist=True)
    await callback.message.edit_text(
        f"✅ حذف شد. باقی‌مانده: {total}\n\n📋 کانفیگ‌های «{escape(g['name'])}»:",
        reply_markup=build_gen_configs_keyboard(gen_id, configs, 0),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.regexp(r"^gen_cfg_rename:\d+:\d+$"))
async def gen_cfg_rename_start(callback: CallbackQuery, state: FSMContext):
    _, gen_id, idx = callback.data.split(":")
    gen_id, idx = int(gen_id), int(idx)
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    configs = storage.resolve_generated_configs(g, persist=True)
    if idx < 0 or idx >= len(configs):
        return await callback.answer("ایندکس نامعتبر.", show_alert=True)
    await state.set_state(GenEditState.waiting_cfg_name)
    await state.update_data(edit_gen_id=gen_id, edit_cfg_idx=idx)
    remark = get_remark(configs[idx]) or "(بدون نام)"
    await callback.message.edit_text(
        f"✏️ اسم جدید برای «{escape(remark)}» را بفرست:",
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(GenEditState.waiting_cfg_name)
async def gen_cfg_rename_apply(message: Message, state: FSMContext):
    data = await state.get_data()
    gen_id = data.get("edit_gen_id")
    idx = data.get("edit_cfg_idx")
    new_name = (message.text or "").strip()
    await state.clear()
    if not new_name or gen_id is None or idx is None:
        return await message.answer("لغو شد.", reply_markup=main_menu())
    remark = storage.rename_config_in_generated(gen_id, message.from_user.id, idx, new_name)
    if remark is None:
        return await message.answer("خطا در رنیم.", reply_markup=main_menu())
    g = storage.get_generated_by_id(gen_id, message.from_user.id)
    await message.answer(f"✅ اسم شد: <b>{escape(remark)}</b>", parse_mode="HTML")
    if g:
        await message.answer(
            gen_detail_text(g),
            reply_markup=build_gen_detail_keyboard(gen_id),
            parse_mode="HTML",
        )


# ---------- تغییر انقضای اشتراک سفارشی ----------

@dp.callback_query(F.data.regexp(r"^gen_expiry:\d+$"))
async def gen_change_expiry(callback: CallbackQuery):
    gen_id = int(callback.data.split(":")[1])
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="♾ بدون انقضا", callback_data=f"gen_set_expiry:{gen_id}:0")],
            [
                InlineKeyboardButton(text="۷ روز", callback_data=f"gen_set_expiry:{gen_id}:7"),
                InlineKeyboardButton(text="۳۰ روز", callback_data=f"gen_set_expiry:{gen_id}:30"),
            ],
            [
                InlineKeyboardButton(text="۹۰ روز", callback_data=f"gen_set_expiry:{gen_id}:90"),
                InlineKeyboardButton(text="۱۸۰ روز", callback_data=f"gen_set_expiry:{gen_id}:180"),
            ],
            [InlineKeyboardButton(text="« انصراف", callback_data=f"gen_open:{gen_id}")],
        ]
    )
    await callback.message.edit_text(
        f"تاریخ انقضای جدید برای «{escape(g['name'])}» را انتخاب کن:\n"
        f"(از همین لحظه محاسبه می‌شود)",
        reply_markup=kb,
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.regexp(r"^gen_set_expiry:\d+:\d+$"))
async def gen_set_expiry(callback: CallbackQuery):
    parts = callback.data.split(":")
    gen_id, days = int(parts[1]), int(parts[2])
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    expires_at = None
    if days > 0:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
    storage.update_generated_expiry(gen_id, callback.from_user.id, expires_at)
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    await callback.message.edit_text(
        gen_detail_text(g),
        reply_markup=build_gen_detail_keyboard(gen_id),
        parse_mode="HTML",
    )
    await callback.answer("انقضا بروزرسانی شد ✅")


# ---------- یادداشت اشتراک سفارشی ----------

@dp.callback_query(F.data.regexp(r"^gen_note:\d+$"))
async def gen_ask_note(callback: CallbackQuery, state: FSMContext):
    gen_id = int(callback.data.split(":")[1])
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    await state.update_data(gen_id=gen_id)
    await state.set_state(GenEditState.waiting_note)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 پاک کردن یادداشت", callback_data=f"gen_note_clear:{gen_id}")],
            [InlineKeyboardButton(text="« انصراف", callback_data=f"gen_open:{gen_id}")],
        ]
    )
    current = (g.get("note") or "").strip()
    if current:
        prompt = (
            f"یادداشت جدید برای «{escape(g['name'])}» را بفرست "
            f"(جایگزین یادداشت فعلی می‌شود):\n\n📝 فعلی:\n{escape(current)}"
        )
    else:
        prompt = f"یادداشت خصوصی برای «{escape(g['name'])}» را بفرست:"
    await callback.message.answer(prompt, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


@dp.message(GenEditState.waiting_note)
async def gen_save_note(message: Message, state: FSMContext):
    if await bail_if_menu_button(message, state):
        return
    data = await state.get_data()
    gen_id = data.get("gen_id")
    if not gen_id:
        await state.clear()
        return await message.answer("خطا. دوباره تلاش کن.")
    note = (message.text or "").strip()
    storage.update_generated_note(gen_id, message.from_user.id, note)
    await state.clear()
    g = storage.get_generated_by_id(gen_id, message.from_user.id)
    if not g:
        return await message.answer("اشتراک پیدا نشد.")
    await message.answer(
        gen_detail_text(g),
        reply_markup=build_gen_detail_keyboard(gen_id),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.regexp(r"^gen_note_clear:\d+$"))
async def gen_clear_note(callback: CallbackQuery, state: FSMContext):
    gen_id = int(callback.data.split(":")[1])
    storage.update_generated_note(gen_id, callback.from_user.id, "")
    await state.clear()
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("پیدا نشد.", show_alert=True)
    await callback.message.edit_text(
        gen_detail_text(g),
        reply_markup=build_gen_detail_keyboard(gen_id),
        parse_mode="HTML",
    )
    await callback.answer("یادداشت پاک شد")


@dp.callback_query(F.data.regexp(r"^gen_delete:\d+$"))
async def delete_generated(callback: CallbackQuery):
    gen_id = int(callback.data.split(":")[1])
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ بله، حذف کن", callback_data=f"gen_delete_yes:{gen_id}"),
                InlineKeyboardButton(text="❌ انصراف", callback_data=f"gen_open:{gen_id}"),
            ]
        ]
    )
    await callback.message.edit_text(
        f"مطمئنی می‌خوای اشتراک سفارشی «{escape(g['name'])}» حذف بشه؟\n"
        "لینکش دیگه کار نمی‌کنه.",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data.regexp(r"^gen_delete_yes:\d+$"))
async def delete_generated_confirm(callback: CallbackQuery):
    gen_id = int(callback.data.split(":")[1])
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)

    storage.delete_generated_sub(gen_id, callback.from_user.id)
    gens = storage.list_generated_subs(callback.from_user.id)

    if gens:
        await callback.message.edit_text(
            f"اشتراک «{escape(g['name'])}» حذف شد.\n\nیکی از اشتراک‌های سفارشی رو انتخاب کن:",
            reply_markup=build_generated_keyboard(gens),
        )
    else:
        await callback.message.edit_text(
            f"اشتراک «{escape(g['name'])}» حذف شد.\n\nهیچ اشتراک سفارشی‌ای نداری."
        )
    await callback.answer("حذف شد ✅")


# ---------- ویرایش یادداشت ----------

@dp.callback_query(F.data.startswith("sub_note:"))
async def ask_edit_note(callback: CallbackQuery, state: FSMContext):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    await state.update_data(sub_id=sub_id)
    await state.set_state(EditNoteState.waiting_for_note)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🗑 حذف کامل یادداشت", callback_data="note_clear")]]
    )
    if sub["note"]:
        prompt = (
            f"چیزی که بفرستی به یادداشت فعلی «{escape(sub['name'])}» اضافه میشه:\n\n"
            f"📝 یادداشت فعلی:\n{escape(sub['note'])}"
        )
    else:
        prompt = f"یادداشت برای «{escape(sub['name'])}» رو بفرست:"
    await callback.message.answer(prompt, reply_markup=kb, parse_mode="HTML")
    await callback.answer()


async def _save_note_and_show(user_id: int, state: FSMContext, note: str, send_via, append: bool = False) -> None:
    data = await state.get_data()
    sub_id = data["sub_id"]
    if append:
        existing = storage.get_sub(sub_id, user_id)
        if existing and existing["note"]:
            note = f"{existing['note']}\n{note}"
    storage.update_note(sub_id, user_id, note)
    await state.clear()
    sub = storage.get_sub(sub_id, user_id)
    await send_via(
        sub_detail_text(sub), reply_markup=build_configs_keyboard(sub_id, sub["configs"]), parse_mode="HTML"
    )


@dp.message(EditNoteState.waiting_for_note)
async def receive_edited_note(message: Message, state: FSMContext):
    if await bail_if_menu_button(message, state):
        return
    note = message.text.strip()
    await _save_note_and_show(message.from_user.id, state, note, message.answer, append=True)


@dp.callback_query(F.data == "note_clear", EditNoteState.waiting_for_note)
async def clear_note(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await _save_note_and_show(callback.from_user.id, state, "", callback.message.answer)
    await callback.answer("یادداشت حذف شد.")


# ---------- پینگ ----------

def _format_ping_lines(sub_name: str, configs: list[str], results: dict[int, float | None]) -> list[str]:
    rows = []
    for i, raw in enumerate(configs):
        remark = get_remark(raw) or "(بدون نام)"
        proto = get_protocol(raw)
        ms = results.get(i)
        rows.append((i, proto, remark, ms))
    rows.sort(key=lambda r: (r[3] is None, r[3] if r[3] is not None else 0))
    lines = [f"📶 نتیجه پینگ «{escape(sub_name)}»:\n"]
    for i, proto, remark, ms in rows:
        status = f"{ms:.0f} ms" if ms is not None else "❌ تایم‌اوت"
        lines.append(f"{i + 1}. [{proto}] {escape(remark[:28])} — {status}")
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > 3500:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)
    return chunks


@dp.callback_query(F.data.startswith("sub_ping:"))
async def ping_sub(callback: CallbackQuery):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    await callback.answer()
    status = await callback.message.answer(f"در حال پینگ {len(sub['configs'])} کانفیگ...")
    results = await ping_configs(sub["configs"])
    chunks = _format_ping_lines(sub["name"], sub["configs"], results)
    await status.edit_text(chunks[0], parse_mode="HTML")
    for chunk in chunks[1:]:
        await callback.message.answer(chunk, parse_mode="HTML")


# ---------- حذف کانفیگ‌های مرده ----------

@dp.callback_query(F.data.regexp(r"^sub_delete_dead:\d+$"))
async def delete_dead_start(callback: CallbackQuery):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    if not sub["configs"]:
        return await callback.answer("هیچ کانفیگی وجود نداره.", show_alert=True)

    await callback.answer()
    status = await callback.message.answer(f"در حال پینگ {len(sub['configs'])} کانفیگ برای پیدا کردن مرده‌ها...")
    results = await ping_configs(sub["configs"])
    dead_indices = [i for i, ms in results.items() if ms is None]

    if not dead_indices:
        await status.edit_text("✅ همه کانفیگ‌ها زنده‌ان! چیزی برای حذف وجود نداره.")
        return

    lines = [f"🧹 <b>{len(dead_indices)} کانفیگ مرده</b> پیدا شد:\n"]
    for i in dead_indices[:30]:
        remark = get_remark(sub["configs"][i]) or "(بدون نام)"
        proto = get_protocol(sub["configs"][i])
        lines.append(f"• [{proto}] {escape(remark[:35])}")
    if len(dead_indices) > 30:
        lines.append(f"... و {len(dead_indices) - 30} تا دیگه")

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ حذف {len(dead_indices)} کانفیگ مرده",
                    callback_data=f"sub_delete_dead_yes:{sub_id}",
                )
            ],
            [InlineKeyboardButton(text="❌ انصراف", callback_data=f"sub_delete_dead_no:{sub_id}")],
        ]
    )
    await status.edit_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")


@dp.callback_query(F.data.regexp(r"^sub_delete_dead_yes:\d+$"))
async def delete_dead_confirm(callback: CallbackQuery):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    await callback.answer("در حال حذف...")
    results = await ping_configs(sub["configs"])
    dead_indices = {i for i, ms in results.items() if ms is None}
    if not dead_indices:
        await callback.message.edit_text("✅ همه کانفیگ‌ها زنده‌ان! چیزی حذف نشد.")
        return
    alive_configs = [cfg for i, cfg in enumerate(sub["configs"]) if i not in dead_indices]
    storage.update_configs(sub_id, callback.from_user.id, alive_configs)
    sub = storage.get_sub(sub_id, callback.from_user.id)
    removed = len(dead_indices)
    await callback.message.edit_text(
        f"✅ {removed} کانفیگ مرده حذف شد.\n\n{sub_detail_text(sub)}",
        reply_markup=build_configs_keyboard(sub_id, sub["configs"]),
        parse_mode="HTML",
    )


@dp.callback_query(F.data.regexp(r"^sub_delete_dead_no:\d+$"))
async def delete_dead_cancel(callback: CallbackQuery):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    await callback.message.edit_text(
        sub_detail_text(sub), reply_markup=build_configs_keyboard(sub_id, sub["configs"]), parse_mode="HTML"
    )
    await callback.answer("انصراف داده شد.")


# ---------- حذف اشتراک ----------

@dp.callback_query(F.data.regexp(r"^sub_delete:\d+$"))
async def confirm_delete(callback: CallbackQuery):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ بله، حذف کن", callback_data=f"sub_delete_yes:{sub_id}"),
                InlineKeyboardButton(text="❌ انصراف", callback_data=f"sub_delete_no:{sub_id}"),
            ]
        ]
    )
    await callback.message.edit_text(
        f"مطمئنی می‌خوای اشتراک «{escape(sub['name'])}» با {len(sub['configs'])} کانفیگ حذف بشه؟"
        "\nاین کار قابل بازگشت نیست.",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data.regexp(r"^sub_delete_yes:\d+$"))
async def do_delete(callback: CallbackQuery):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    storage.delete_sub(sub_id, callback.from_user.id)
    subs = storage.list_subs(callback.from_user.id)
    if subs:
        await callback.message.edit_text(
            f"اشتراک «{escape(sub['name'])}» حذف شد.\n\nیکی از اشتراک‌ها رو انتخاب کن:",
            reply_markup=build_subs_keyboard(subs),
        )
    else:
        await callback.message.edit_text(
            f"اشتراک «{escape(sub['name'])}» حذف شد.\n\nهیچ اشتراکی نداری. از دکمه «{BTN_ADD_SUB}» استفاده کن."
        )
    await callback.answer("حذف شد.")


@dp.callback_query(F.data.regexp(r"^sub_delete_no:\d+$"))
async def cancel_delete(callback: CallbackQuery):
    sub_id = int(callback.data.split(":")[1])
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)
    await callback.message.edit_text(
        sub_detail_text(sub), reply_markup=build_configs_keyboard(sub_id, sub["configs"]), parse_mode="HTML"
    )
    await callback.answer("انصراف داده شد.")


# ---------- رنیم تکی ----------

@dp.callback_query(F.data.startswith("cfg_pick:"))
async def pick_config(callback: CallbackQuery, state: FSMContext):
    _, sub_id, idx = callback.data.split(":")
    sub_id, idx = int(sub_id), int(idx)
    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub or idx >= len(sub["configs"]):
        return await callback.answer("نامعتبر", show_alert=True)
    await state.update_data(sub_id=sub_id, config_index=idx)
    await state.set_state(RenameState.waiting_for_name)
    await callback.message.answer("اسمی که میخوای روی این کانفیگ بذاری رو بفرست:")
    await callback.answer()


@dp.message(RenameState.waiting_for_name)
async def do_rename(message: Message, state: FSMContext):
    if await bail_if_menu_button(message, state):
        return
    data = await state.get_data()
    sub_id, idx = data["sub_id"], data["config_index"]
    sub = storage.get_sub(sub_id, message.from_user.id)
    if not sub or idx >= len(sub["configs"]):
        await state.clear()
        return await message.answer("این کانفیگ دیگه معتبر نیست، دوباره از لیست انتخاب کن.")
    new_name = message.text.strip()
    renamed = rename_config(sub["configs"][idx], new_name)
    await message.answer(f"<code>{escape(renamed)}</code>", parse_mode="HTML")
    await state.clear()


# ---------- بک‌آپ / بازیابی ----------

async def show_backup_menu(message: Message, state: FSMContext | None = None):
    if state:
        await state.clear()
    data = storage.export_full_backup()
    stats = data.get("stats") or {}
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📥 دانلود بک‌آپ کامل", callback_data="backup_download")],
            [InlineKeyboardButton(text="📤 بازیابی از فایل JSON", callback_data="backup_restore")],
        ]
    )
    await message.answer(
        "💾 <b>بک‌آپ و جابه‌جایی سرور</b>\n\n"
        f"📦 اشتراک اصلی: <b>{stats.get('subs_count', 0)}</b>\n"
        f"🛠 اشتراک سفارشی: <b>{stats.get('generated_count', 0)}</b>\n"
        f"🔗 کانفیگ‌های سفارشی: <b>{stats.get('generated_configs', 0)}</b>\n\n"
        "با دانلود بک‌آپ، همه چیز (از جمله <b>توکن لینک‌های سفارشی</b>) ذخیره می‌شود.\n"
        "روی سرور جدید همان فایل را بازیابی کن و <code>BASE_URL</code> / دامنه را "
        "مثل قبل ست کن تا لینک‌ها عوض نشوند.",
        reply_markup=kb,
        parse_mode="HTML",
    )


@dp.message(F.text == BTN_BACKUP)
async def backup_menu_msg(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await message.answer("دسترسی نداری.")
    await show_backup_menu(message, state)


@dp.callback_query(F.data == "backup_download")
async def backup_download(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("دسترسی نداری.", show_alert=True)
    await callback.answer()
    data = storage.export_full_backup()
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    fname = f"eshkhoshbakht-backup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    doc = BufferedInputFile(body, filename=fname)
    stats = data.get("stats") or {}
    await callback.message.answer_document(
        doc,
        caption=(
            f"✅ بک‌آپ آماده است.\n"
            f"📦 {stats.get('subs_count', 0)} اشتراک · "
            f"🛠 {stats.get('generated_count', 0)} سفارشی\n\n"
            "این فایل را جای امن نگه دار. برای جابه‌جایی سرور، روی پنل/ربات جدید بازیابی کن."
        ),
    )


@dp.callback_query(F.data == "backup_restore")
async def backup_restore_ask(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return await callback.answer("دسترسی نداری.", show_alert=True)
    await state.set_state(BackupState.waiting_restore_file)
    await callback.message.answer(
        "📤 فایل JSON بک‌آپ را به صورت <b>سند (Document)</b> بفرست.\n\n"
        "⚠️ بازیابی، داده‌های فعلی این سرور را <b>جایگزین</b> می‌کند.\n"
        "توکن‌های لینک سفارشی از بک‌آپ حفظ می‌شوند.",
        parse_mode="HTML",
    )
    await callback.answer()


@dp.message(BackupState.waiting_restore_file, F.document)
async def backup_restore_file(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return await message.answer("دسترسی نداری.")
    doc = message.document
    if not doc:
        return await message.answer("یک فایل JSON بفرست.")
    name = (doc.file_name or "").lower()
    if name and not (name.endswith(".json") or name.endswith(".txt")):
        return await message.answer("فایل باید JSON باشد.")

    try:
        file = await bot.get_file(doc.file_id)
        buf = io.BytesIO()
        await bot.download_file(file.file_path, buf)
        raw = buf.getvalue().decode("utf-8")
        data = json.loads(raw)
        result = storage.import_full_backup(data, replace=True)
        await state.clear()
        await message.answer(
            "✅ بازیابی انجام شد.\n\n"
            f"📦 اشتراک اصلی: {result.get('subs_restored', 0)}\n"
            f"🛠 اشتراک سفارشی: {result.get('generated_restored', 0)}\n"
            f"🔑 توکن حفظ‌شده: {result.get('tokens_preserved', 0)}\n\n"
            "اگر دامنه/`BASE_URL` مثل سرور قبلی باشد، لینک‌های مشتریان همان قبلی می‌ماند.",
            reply_markup=main_menu(),
        )
    except json.JSONDecodeError:
        await message.answer("فایل JSON معتبر نیست. دوباره بفرست یا /start بزن.")
    except ValueError as e:
        await message.answer(f"خطا: {escape(str(e))}", parse_mode="HTML")
    except Exception as e:
        logger.exception("backup restore failed")
        await message.answer(f"خطا در بازیابی: {escape(str(e))}", parse_mode="HTML")


@dp.message(BackupState.waiting_restore_file)
async def backup_restore_need_doc(message: Message, state: FSMContext):
    if await bail_if_menu_button(message, state):
        return
    await message.answer("لطفاً فایل بک‌آپ را به صورت سند (Document) بفرست، نه متن.")


# ===================== Main =====================

async def main():
    me = await bot.get_me()
    auth.BOT_USERNAME = me.username
    logger.info(f"Bot username: @{me.username}")
    if not auth.panel_enabled():
        logger.warning("ADMIN_IDS ست نشده — پنل وب غیرفعال می‌مونه.")

    try:
        removed = storage.cleanup_old_expired_generated(grace_days=7)
        if removed:
            logger.info(f"پاک‌سازی خودکار: {removed} اشتراک منقضی‌شدهٔ قدیمی حذف شد.")
    except Exception as e:
        logger.warning(f"cleanup failed: {e}")

    port = int(os.environ.get("PORT", 8080))
    web_app = create_web_app()
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"HTTP server started on port {port}")

    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())

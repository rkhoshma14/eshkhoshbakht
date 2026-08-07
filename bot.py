import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from html import escape

import httpx
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

import storage
from config_parser import (
    decode_subscription,
    encode_subscription,
    get_protocol,
    get_remark,
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
BTN_NOTE_SKIP = "بدون یادداشت"
BTN_BACK = "« بازگشت به اشتراک‌ها"
BTN_REFRESH = "🔄 بروزرسانی"
BTN_PING = "📶 پینگ کانفیگ‌ها"
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


class BuildCustomState(StatesGroup):
    selecting = State()          # انتخاب کانفیگ‌ها
    renaming = State()           # رنیم یکی‌یکی
    waiting_sub_name = State()   # اسم کلی اشتراک سفارشی


MENU_BUTTONS = {BTN_ADD_SUB, BTN_LIST, BTN_MY_GENERATED}


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
    return True


def is_admin(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ADD_SUB)],
            [KeyboardButton(text=BTN_LIST)],
            [KeyboardButton(text=BTN_MY_GENERATED)],
        ],
        resize_keyboard=True,
    )


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
    text = (
        f"🛠 <b>{escape(g['name'])}</b>\n"
        f"📦 {len(g['configs'])} کانفیگ\n"
        f"🕒 ساخته‌شده: {created}\n\n"
        f"🔗 لینک اشتراک:\n<code>{url}</code>"
    )
    return text


def build_gen_detail_keyboard(gen_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 حذف این اشتراک", callback_data=f"gen_delete:{gen_id}")],
            [InlineKeyboardButton(text="« بازگشت به لیست", callback_data="gens_back")],
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


# ===================== HTTP Server (برای سرو سابسکریپشن) =====================

async def handle_sub(request: web.Request) -> web.Response:
    token = request.match_info.get("token", "")
    gen = storage.get_generated_by_token(token)
    if not gen:
        return web.Response(text="Subscription not found", status=404)

    body = encode_subscription(gen["configs"])
    return web.Response(
        text=body,
        content_type="text/plain",
        headers={
            "profile-title": gen["name"],
            "content-disposition": f'attachment; filename="{gen["name"]}.txt"',
        },
    )


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def create_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/sub/{token}", handle_sub)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/", handle_health)
    return app


# ===================== Telegram Handlers =====================

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
    await state.update_data(sub_id=sub_id, selected=set(), rename_queue=[], renamed_configs=[])

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
    await callback.answer(f"{len(selected)} کانفیگ انتخاب شد")@dp.callback_query(F.data.startswith("sel_none:"), BuildCustomState.selecting)
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

    # صف رنیم: لیست ایندکس‌های انتخاب‌شده (مرتب)
    queue = sorted(selected)
    await state.update_data(
        rename_queue=queue,
        renamed_configs=[],
        current_rename_idx=0,
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


@dp.message(BuildCustomState.renaming)
async def receive_rename(message: Message, state: FSMContext):
    if await bail_if_menu_button(message, state):
        return

    data = await state.get_data()
    sub_id = data["sub_id"]
    queue: list = data["rename_queue"]
    current = data["current_rename_idx"]
    renamed: list = data.get("renamed_configs", [])

    sub = storage.get_sub(sub_id, message.from_user.id)
    if not sub:
        await state.clear()
        return await message.answer("اشتراک پیدا نشد.")

    idx = queue[current]
    raw = sub["configs"][idx]
    new_name = message.text.strip()
    renamed.append(rename_config(raw, new_name))

    await _advance_rename(message, state, sub, queue, current, renamed)


@dp.callback_query(F.data == "rename_skip", BuildCustomState.renaming)
async def rename_skip(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    sub_id = data["sub_id"]
    queue: list = data["rename_queue"]
    current = data["current_rename_idx"]
    renamed: list = data.get("renamed_configs", [])

    sub = storage.get_sub(sub_id, callback.from_user.id)
    if not sub:
        await state.clear()
        return await callback.answer("اشتراک پیدا نشد.", show_alert=True)

    idx = queue[current]
    # بدون تغییر اسم
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
        # همه رنیم شدن → برو به مرحله اسم کلی اشتراک
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

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⏭ رد کردن (همان اسم قبلی)", callback_data="rename_skip")],
            [InlineKeyboardButton(text="❌ انصراف از ساخت", callback_data=f"sub_open:{sub['id']}")],
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
async def finish_custom_sub(message: Message, state: FSMContext):
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

    gen_id, token = storage.create_generated_sub(message.from_user.id, name, renamed)
    await state.clear()

    # ساخت لینک
    if BASE_URL:
        url = make_public_url(token)
        text = (
            f"✅ اشتراک سفارشی «{escape(name)}» ساخته شد.\n"
            f"تعداد کانفیگ: {len(renamed)}\n\n"
            f"🔗 لینک اشتراک:\n<code>{url}</code>\n\n"
            "این لینک رو می‌تونی مستقیم توی کلاینت‌ها اضافه کنی."
        )
    else:
        text = (
            f"✅ اشتراک سفارشی «{escape(name)}» ساخته شد.\n"
            f"تعداد کانفیگ: {len(renamed)}\n\n"
            f"لینک:\n<code>https://YOUR-RAILWAY-DOMAIN/sub/{token}</code>\n\n"
            "⚠️ متغیر محیطی <code>BASE_URL</code> رو در Railway ست کن "
            "(مثلاً <code>https://xxx.up.railway.app</code>) تا لینک کامل ساخته بشه."
        )

    await message.answer(text, parse_mode="HTML", reply_markup=main_menu())


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

    await callback.message.edit_text(
        gen_detail_text(g),
        reply_markup=build_gen_detail_keyboard(gen_id),
        parse_mode="HTML",
    )
    await callback.answer()


@dp.callback_query(F.data.regexp(r"^gen_delete:\d+$"))
async def delete_generated(callback: CallbackQuery):
    gen_id = int(callback.data.split(":")[1])
    g = storage.get_generated_by_id(gen_id, callback.from_user.id)
    if not g:
        return await callback.answer("این اشتراک پیدا نشد.", show_alert=True)

    # تأیید حذف
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


# ===================== Main =====================

async def main():
    # وب‌سرور برای سرو سابسکریپشن‌ها
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

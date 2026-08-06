import asyncio
import logging
import os
from html import escape

import httpx
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
from config_parser import decode_subscription, get_protocol, get_remark, rename_config

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
PAGE_SIZE = 8

BTN_ADD_SUB = "➕ افزودن اشتراک"
BTN_LIST = "📋 لیست اشتراک‌ها"
BTN_NOTE_SKIP = "بدون یادداشت"
BTN_BACK = "« بازگشت به اشتراک‌ها"
BTN_REFRESH = "🔄 بروزرسانی"

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class AddSubState(StatesGroup):
    waiting_for_link = State()
    waiting_for_name = State()
    waiting_for_note = State()


class RenameState(StatesGroup):
    waiting_for_name = State()


def is_admin(user_id: int) -> bool:
    # اگر ADMIN_IDS خالی باشه یعنی همه اجازه دارن (برای تست). حتما پرش کن.
    return not ADMIN_IDS or user_id in ADMIN_IDS


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_ADD_SUB)], [KeyboardButton(text=BTN_LIST)]],
        resize_keyboard=True,
    )


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
            InlineKeyboardButton(text=BTN_REFRESH, callback_data=f"sub_refresh:{sub_id}"),
            InlineKeyboardButton(text=BTN_BACK, callback_data="subs_back"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sub_detail_text(sub: dict) -> str:
    text = f"📦 <b>{escape(sub['name'])}</b>\n"
    if sub["note"]:
        text += f"📝 {escape(sub['note'])}\n"
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
    name = message.text.strip()
    await state.update_data(name=name)
    await state.set_state(AddSubState.waiting_for_note)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_NOTE_SKIP, callback_data="note_skip")]]
    )
    await message.answer("اگه یادداشتی هم می‌خوای بذاری بفرست، وگرنه دکمه زیر رو بزن:", reply_markup=kb)


async def _finish_add_sub(user_id: int, state: FSMContext, note: str) -> str:
    data = await state.get_data()
    sub_id = storage.add_sub(user_id, data["name"], note, data["sub_url"], data["configs"])
    await state.clear()
    return f"اشتراک «{data['name']}» با {len(data['configs'])} کانفیگ ذخیره شد."


@dp.message(AddSubState.waiting_for_note)
async def receive_note(message: Message, state: FSMContext):
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
async def open_sub(callback: CallbackQuery):
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
    sub["configs"] = result
    await callback.message.edit_text(
        sub_detail_text(sub), reply_markup=build_configs_keyboard(sub_id, result), parse_mode="HTML"
    )


# ---------- انتخاب و رنیم کانفیگ ----------

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


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

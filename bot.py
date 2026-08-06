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
BTN_LIST = "📋 لیست کانفیگ‌ها"
BTN_REFRESH = "🔄 بروزرسانی"

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class AddSubState(StatesGroup):
    waiting_for_link = State()


class RenameState(StatesGroup):
    waiting_for_name = State()


def is_admin(user_id: int) -> bool:
    # اگر ADMIN_IDS خالی باشه یعنی همه اجازه دارن (برای تست). حتما پرش کن.
    return not ADMIN_IDS or user_id in ADMIN_IDS


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ADD_SUB)],
            [KeyboardButton(text=BTN_LIST), KeyboardButton(text=BTN_REFRESH)],
        ],
        resize_keyboard=True,
    )


def build_list_keyboard(configs: list[str], page: int = 0) -> InlineKeyboardMarkup:
    start = page * PAGE_SIZE
    chunk = configs[start : start + PAGE_SIZE]
    rows = []
    for i, raw in enumerate(chunk, start=start):
        remark = get_remark(raw) or "(بدون نام)"
        proto = get_protocol(raw)
        label = f"{i + 1}. [{proto}] {remark[:30]}"
        rows.append([InlineKeyboardButton(text=label, callback_data=f"pick:{i}")])

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton(text="« قبلی", callback_data=f"page:{page - 1}"))
    if start + PAGE_SIZE < len(configs):
        nav.append(InlineKeyboardButton(text="بعدی »", callback_data=f"page:{page + 1}"))
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def fetch_and_save(user_id: int, sub_url: str) -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(sub_url)
            resp.raise_for_status()
            configs = decode_subscription(resp.text)
    except Exception as e:
        return False, f"خطا در دریافت لینک: {escape(str(e))}"

    if not configs:
        return False, "هیچ کانفیگی توی این اشتراک پیدا نشد."

    storage.save_sub(user_id, sub_url, configs)
    return True, f"{len(configs)} کانفیگ پیدا شد. از دکمه «{BTN_LIST}» استفاده کن."


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "سلام! از دکمه‌های زیر استفاده کن:",
        reply_markup=main_menu(),
    )


@dp.message(F.text == BTN_ADD_SUB)
async def ask_for_sub(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return await message.answer("اجازه دسترسی نداری.")

    await state.set_state(AddSubState.waiting_for_link)
    await message.answer("لینک اشتراک رو بفرست:")


@dp.message(AddSubState.waiting_for_link)
async def receive_sub_link(message: Message, state: FSMContext):
    sub_url = message.text.strip()
    status = await message.answer("در حال دریافت و پردازش اشتراک...")
    ok, text = await fetch_and_save(message.from_user.id, sub_url)
    await status.edit_text(text)
    if ok:
        await state.clear()
        await message.answer("آماده‌ست.", reply_markup=main_menu())
    # اگه خطا خورد، توی همین state می‌مونه تا دوباره لینک درست بفرسته


@dp.message(F.text == BTN_REFRESH)
async def refresh(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("اجازه دسترسی نداری.")

    sub_url, _ = storage.get_sub(message.from_user.id)
    if not sub_url:
        return await message.answer(f"اول از دکمه «{BTN_ADD_SUB}» یه لینک اشتراک اضافه کن.")

    status = await message.answer("در حال بروزرسانی...")
    ok, text = await fetch_and_save(message.from_user.id, sub_url)
    await status.edit_text(text if not ok else text.replace("پیدا شد", "بروزرسانی شد"))


@dp.message(F.text == BTN_LIST)
async def list_configs(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("اجازه دسترسی نداری.")

    _, configs = storage.get_sub(message.from_user.id)
    if not configs:
        return await message.answer(f"هنوز اشتراکی اضافه نکردی. از دکمه «{BTN_ADD_SUB}» استفاده کن.")

    await message.answer("یکی از کانفیگ‌ها رو انتخاب کن:", reply_markup=build_list_keyboard(configs))


@dp.callback_query(F.data.startswith("page:"))
async def paginate(callback: CallbackQuery):
    page = int(callback.data.split(":")[1])
    _, configs = storage.get_sub(callback.from_user.id)
    await callback.message.edit_reply_markup(reply_markup=build_list_keyboard(configs, page))
    await callback.answer()


@dp.callback_query(F.data.startswith("pick:"))
async def pick_config(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    _, configs = storage.get_sub(callback.from_user.id)
    if idx >= len(configs):
        return await callback.answer("نامعتبر", show_alert=True)

    await state.update_data(config_index=idx)
    await state.set_state(RenameState.waiting_for_name)
    await callback.message.answer("اسمی که میخوای روی این کانفیگ بذاری رو بفرست:")
    await callback.answer()


@dp.message(RenameState.waiting_for_name)
async def do_rename(message: Message, state: FSMContext):
    data = await state.get_data()
    idx = data["config_index"]
    _, configs = storage.get_sub(message.from_user.id)

    if idx >= len(configs):
        await state.clear()
        return await message.answer(f"این کانفیگ دیگه معتبر نیست، دوباره از «{BTN_LIST}» انتخاب کن.")

    new_name = message.text.strip()
    renamed = rename_config(configs[idx], new_name)

    await message.answer(f"<code>{escape(renamed)}</code>", parse_mode="HTML")
    await state.clear()


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

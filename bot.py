import asyncio
import logging
import os
from html import escape

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import storage
from config_parser import decode_subscription, get_protocol, get_remark, rename_config

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}
PAGE_SIZE = 8

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


class RenameState(StatesGroup):
    waiting_for_name = State()


def is_admin(user_id: int) -> bool:
    # اگر ADMIN_IDS خالی باشه یعنی همه اجازه دارن (برای تست). حتما پرش کن.
    return not ADMIN_IDS or user_id in ADMIN_IDS


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


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "سلام!\n\n"
        "۱. اول لینک اشتراکت رو با این دستور بده:\n"
        "<code>/addsub لینک_اشتراک</code>\n\n"
        "۲. بعد با /list لیست کانفیگ‌ها رو ببین و هرکدوم رو بزن تا اسم دلخواهتو ازت بپرسه.\n\n"
        "برای بروزرسانی لیست از /refresh استفاده کن.",
        parse_mode="HTML",
    )


@dp.message(Command("addsub"))
async def add_sub(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("اجازه دسترسی نداری.")

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return await message.answer("فرمت درست: /addsub لینک_اشتراک")

    sub_url = parts[1].strip()
    status = await message.answer("در حال دریافت و پردازش اشتراک...")

    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(sub_url)
            resp.raise_for_status()
            configs = decode_subscription(resp.text)
    except Exception as e:
        return await status.edit_text(f"خطا در دریافت لینک: {escape(str(e))}")

    if not configs:
        return await status.edit_text("هیچ کانفیگی توی این اشتراک پیدا نشد.")

    storage.save_sub(message.from_user.id, sub_url, configs)
    await status.edit_text(f"{len(configs)} کانفیگ پیدا شد. برای دیدن لیست /list رو بزن.")


@dp.message(Command("refresh"))
async def refresh(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("اجازه دسترسی نداری.")

    sub_url, _ = storage.get_sub(message.from_user.id)
    if not sub_url:
        return await message.answer("اول با /addsub یه لینک اشتراک اضافه کن.")

    status = await message.answer("در حال بروزرسانی...")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(sub_url)
            resp.raise_for_status()
            configs = decode_subscription(resp.text)
    except Exception as e:
        return await status.edit_text(f"خطا: {escape(str(e))}")

    storage.save_sub(message.from_user.id, sub_url, configs)
    await status.edit_text(f"بروزرسانی شد. {len(configs)} کانفیگ.")


@dp.message(Command("list"))
async def list_configs(message: Message):
    if not is_admin(message.from_user.id):
        return await message.answer("اجازه دسترسی نداری.")

    _, configs = storage.get_sub(message.from_user.id)
    if not configs:
        return await message.answer("هنوز اشتراکی اضافه نکردی. از /addsub استفاده کن.")

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
        return await message.answer("این کانفیگ دیگه معتبر نیست، دوباره /list رو بزن.")

    new_name = message.text.strip()
    renamed = rename_config(configs[idx], new_name)

    await message.answer(f"<code>{escape(renamed)}</code>", parse_mode="HTML")
    await state.clear()


async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

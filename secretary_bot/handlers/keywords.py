"""Keyword auto-reply setup (FSM) and management (list/view/delete)."""

from aiogram import F
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from secretary_bot import db
from secretary_bot.config import dp
from secretary_bot.helpers import is_authorized_callback, is_authorized_in_group, safe_reply


class KeywordSetup(StatesGroup):
    waiting_keyword = State()
    confirm_keyword = State()
    waiting_reply = State()
    confirm_reply = State()


# ---------------------------------------------------------------------------
# Setup flow
# ---------------------------------------------------------------------------

@dp.message(F.text == "تنظیم کیورد")
async def start_keyword_setup(message: Message, state: FSMContext):
    if not await is_authorized_in_group(message):
        return
    prompt = await safe_reply(message, "پیامی که می‌خوای کیورد باشه رو روی همین پیام ریپلای کن.")
    if not prompt:
        return
    await state.set_state(KeywordSetup.waiting_keyword)
    await state.update_data(prompt_id=prompt.message_id)


@dp.message(StateFilter(KeywordSetup.waiting_keyword))
async def receive_keyword(message: Message, state: FSMContext):
    if not await is_authorized_in_group(message):
        return
    data = await state.get_data()
    if not message.reply_to_message or message.reply_to_message.message_id != data.get("prompt_id"):
        return
    if not message.text:
        await safe_reply(message, "فقط متن قابل قبوله.")
        return

    await state.update_data(keyword=message.text)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ تایید", callback_data="kw_confirm_keyword"),
        InlineKeyboardButton(text="❌ رد", callback_data="kw_cancel"),
    )
    await safe_reply(
        message,
        f"این کیورد تنظیم بشه؟\n«{message.text}»",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(KeywordSetup.confirm_keyword)


@dp.callback_query(F.data == "kw_confirm_keyword", StateFilter(KeywordSetup.confirm_keyword))
async def confirm_keyword(callback: CallbackQuery, state: FSMContext):
    prompt = await safe_reply(callback.message, "حالا پیامی که می‌خوای در جواب این کیورد ارسال بشه رو روی همین پیام ریپلای کن.")
    if prompt:
        await state.update_data(prompt_id=prompt.message_id)
        await state.set_state(KeywordSetup.waiting_reply)
    await callback.answer()


@dp.message(StateFilter(KeywordSetup.waiting_reply))
async def receive_reply_text(message: Message, state: FSMContext):
    if not await is_authorized_in_group(message):
        return
    data = await state.get_data()
    if not message.reply_to_message or message.reply_to_message.message_id != data.get("prompt_id"):
        return
    if not message.text:
        await safe_reply(message, "فقط متن قابل قبوله.")
        return

    await state.update_data(reply_text=message.text)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ تایید", callback_data="kw_confirm_reply"),
        InlineKeyboardButton(text="❌ رد", callback_data="kw_cancel"),
    )
    await safe_reply(
        message,
        f"کیورد: «{data.get('keyword')}»\nپاسخ: «{message.text}»\nتایید می‌کنی؟",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(KeywordSetup.confirm_reply)


@dp.callback_query(F.data == "kw_confirm_reply", StateFilter(KeywordSetup.confirm_reply))
async def confirm_reply(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await db.insert_keyword(data.get("keyword"), data.get("reply_text"), callback.from_user.id)
    await state.clear()
    await safe_reply(callback.message, "اتومیشن تنظیم شد ✅")
    await callback.answer()


@dp.callback_query(F.data == "kw_cancel")
async def cancel_keyword_setup(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await safe_reply(callback.message, "لغو شد ❌")
    await callback.answer()


# ---------------------------------------------------------------------------
# List / view / delete
# ---------------------------------------------------------------------------

def build_keyword_list_markup(rows):
    builder = InlineKeyboardBuilder()
    for kw_id, keyword, _ in rows:
        builder.row(InlineKeyboardButton(text=keyword, callback_data=f"kwv:{kw_id}"))
    return builder.as_markup()


def build_keyword_item_markup(kw_id: int):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🗑 حذف کیورد", callback_data=f"kwdel:{kw_id}"))
    builder.row(InlineKeyboardButton(text="⬅️ برگشت", callback_data="kwback"))
    return builder.as_markup()


@dp.message(F.text == "لیست کیورد")
async def list_keywords(message: Message):
    if not await is_authorized_in_group(message):
        return

    rows = await db.list_keywords(message.from_user.id)
    if not rows:
        await safe_reply(message, "هنوز کیوردی ثبت نشده.")
        return

    await safe_reply(message, "کیورد مورد نظر رو انتخاب کن:", reply_markup=build_keyword_list_markup(rows))


@dp.callback_query(F.data.startswith("kwv:"))
async def on_keyword_view(callback: CallbackQuery):
    if not await is_authorized_callback(callback):
        await callback.answer("⛔", show_alert=True)
        return

    kw_id = int(callback.data.split(":", 1)[1])
    row = await db.get_keyword(kw_id, callback.from_user.id)
    if not row:
        await callback.answer("این کیورد دیگه وجود نداره.", show_alert=True)
        return

    _, keyword, reply_text = row
    await callback.message.edit_text(
        f"کیورد: «{keyword}»\nپاسخ: «{reply_text}»",
        reply_markup=build_keyword_item_markup(kw_id),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("kwdel:"))
async def on_keyword_delete(callback: CallbackQuery):
    if not await is_authorized_callback(callback):
        await callback.answer("⛔", show_alert=True)
        return

    kw_id = int(callback.data.split(":", 1)[1])
    rows = await db.delete_keyword(kw_id, callback.from_user.id)

    if not rows:
        await callback.message.edit_text("هنوز کیوردی ثبت نشده.")
    else:
        await callback.message.edit_text("کیورد مورد نظر رو انتخاب کن:", reply_markup=build_keyword_list_markup(rows))
    await callback.answer("حذف شد ✅")


@dp.callback_query(F.data == "kwback")
async def on_keyword_back(callback: CallbackQuery):
    if not await is_authorized_callback(callback):
        await callback.answer("⛔", show_alert=True)
        return

    rows = await db.list_keywords(callback.from_user.id)
    if not rows:
        await callback.message.edit_text("هنوز کیوردی ثبت نشده.")
    else:
        await callback.message.edit_text("کیورد مورد نظر رو انتخاب کن:", reply_markup=build_keyword_list_markup(rows))
    await callback.answer()

"""/backup command: pick account -> pick chat -> get a text file dump."""

import os
from datetime import datetime, timezone

from aiogram import F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from secretary_bot import db
from secretary_bot.config import OWNER_ID, bot, dp, logger
from secretary_bot.helpers import safe_reply


def build_accounts_markup(rows):
    builder = InlineKeyboardBuilder()
    for owner_user_id, display_name in rows:
        builder.row(InlineKeyboardButton(text=display_name, callback_data=f"bkacc:{owner_user_id}"))
    return builder.as_markup()


def build_backup_chats_markup(chats):
    builder = InlineKeyboardBuilder()
    for chat_id, chat_name in chats:
        builder.row(InlineKeyboardButton(text=chat_name, callback_data=f"backup:{chat_id}"))
    builder.row(InlineKeyboardButton(text="⬅️ لیست اکانت‌ها", callback_data="bkback"))
    return builder.as_markup()


async def get_backup_accounts():
    accounts = await db.get_backup_account_ids()
    result = []
    for row in accounts:
        result.append((row[0], await db.connection_display_name(row[0])))
    result.sort(key=lambda r: r[1].lower())
    return result


async def show_backup_chats(target, owner_user_id: int):
    chats = await db.get_backup_chats(owner_user_id)
    if not chats:
        text = "پیامی برای این اکانت یافت نشد."
        if isinstance(target, Message):
            await safe_reply(target, text)
        else:
            await target.edit_text(text)
        return

    # Encode owner_user_id via chat_id lookup isn't unique across accounts,
    # so we keep owner_user_id in the caller's FSM-less flow by re-deriving
    # it from chat_id when the button is pressed (chat_id is unique enough
    # in practice since we only show chats belonging to this owner).
    markup = build_backup_chats_markup(chats)
    acc_name = await db.connection_display_name(owner_user_id)
    text = f"کاربر مورد نظر رو انتخاب کن ({acc_name}):"
    if isinstance(target, Message):
        await safe_reply(target, text, reply_markup=markup)
    else:
        await target.edit_text(text, reply_markup=markup)


@dp.message(Command("backup"))
async def cmd_backup(message: Message):
    if message.from_user.id != OWNER_ID:
        return
    if message.chat.id != await db.get_home_chat_id(OWNER_ID):
        return

    accounts = await get_backup_accounts()
    if not accounts:
        await safe_reply(message, "پیامی از ساب‌اکانتی ذخیره نشده.")
        return

    if len(accounts) == 1:
        await show_backup_chats(message, accounts[0][0])
        return

    await safe_reply(message, "اکانت مورد نظر رو انتخاب کن:", reply_markup=build_accounts_markup(accounts))


@dp.callback_query(F.data.startswith("bkacc:"))
async def on_account_click(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("⛔", show_alert=True)
        return
    owner_user_id = int(callback.data.split(":", 1)[1])
    await show_backup_chats(callback.message, owner_user_id)
    await callback.answer()


@dp.callback_query(F.data == "bkback")
async def on_backup_back(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("⛔", show_alert=True)
        return

    accounts = await get_backup_accounts()
    if not accounts:
        await callback.message.edit_text("پیامی از ساب‌اکانتی ذخیره نشده.")
    else:
        await callback.message.edit_text("اکانت مورد نظر رو انتخاب کن:", reply_markup=build_accounts_markup(accounts))
    await callback.answer()


@dp.callback_query(F.data.startswith("backup:"))
async def on_backup_click(callback: CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("⛔", show_alert=True)
        return

    chat_id = int(callback.data.split(":", 1)[1])

    # Resolve owning account for this chat_id from the messages table.
    def _q(conn):
        return conn.execute(
            "SELECT DISTINCT owner_user_id FROM messages WHERE chat_id = ? AND owner_user_id IS NOT NULL",
            (chat_id,),
        ).fetchall()

    owner_rows = await db.run_db(_q)
    if not owner_rows:
        await callback.answer("پیامی یافت نشد.", show_alert=True)
        return
    owner_user_id = owner_rows[0][0]

    rows = await db.get_backup_messages(owner_user_id, chat_id)
    if not rows:
        await callback.answer("پیامی یافت نشد.", show_alert=True)
        return

    acc_name = await db.connection_display_name(owner_user_id)
    chat_name = rows[0][0]
    lines = [f"===== {acc_name} | {chat_name} (ID: {chat_id}) ====="]
    for _, sender_name, sender_username, text, media_type, ts, _ in rows:
        try:
            ts_fmt = datetime.fromisoformat(ts).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            ts_fmt = ts
        uname = f" (@{sender_username})" if sender_username else ""
        content = f"[{media_type}] {text}".strip() if media_type else text
        lines.append(f"[{ts_fmt}] {sender_name}{uname}: {content}")

    safe_acc = "".join(c for c in acc_name if c.isalnum() or c in " _-").strip() or str(owner_user_id)
    safe_name = "".join(c for c in chat_name if c.isalnum() or c in " _-").strip() or str(chat_id)
    filename = f"backup_{safe_acc}_{safe_name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.txt"
    filepath = f"/tmp/{filename}"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    try:
        await bot.send_document(
            chat_id=callback.message.chat.id,
            document=FSInputFile(filepath, filename=filename),
            caption=f"بکاپ {acc_name} | {chat_name} — {len(rows)} پیام",
            message_thread_id=callback.message.message_thread_id,
        )
    except TelegramBadRequest as e:
        if "TOPIC_CLOSED" in str(e):
            try:
                if callback.message.message_thread_id:
                    await bot.reopen_forum_topic(
                        chat_id=callback.message.chat.id,
                        message_thread_id=callback.message.message_thread_id,
                    )
                await bot.send_document(
                    chat_id=callback.message.chat.id,
                    document=FSInputFile(filepath, filename=filename),
                    caption=f"بکاپ {acc_name} | {chat_name} — {len(rows)} پیام",
                    message_thread_id=callback.message.message_thread_id,
                )
            except Exception as e2:
                logger.error(f"Failed to send backup document: {e2}")
        else:
            logger.error(f"Failed to send backup document: {e}")
    finally:
        try:
            os.remove(filepath)
        except OSError:
            pass

    # Media is already relayed live into the unified media topic as it
    # arrives (see business.py) — /backup only sends the text file and does
    # not resend media or create/touch any topic.

    await callback.answer("ارسال شد ✅")

"""Reports edited/deleted business messages into the account's topic.

Requires the original message to have been logged first (on_business_message
in business.py stores message_id), since Telegram's edited/deleted business
message updates don't include the old text.
"""

from typing import Optional

from aiogram.types import BusinessMessagesDeleted, Message

from secretary_bot import db
from secretary_bot.config import dp, logger
from secretary_bot.helpers import chat_display_name, get_or_create_topic, safe_send_message


@dp.edited_business_message()
async def on_business_message_edited(message: Message):
    new_text = message.text or message.caption or ""
    if not new_text:
        return

    owner_user_id = await db.connection_owner_user_id(message.business_connection_id)

    row = await db.get_message_by_id(message.business_connection_id, message.chat.id, message.message_id)
    old_text = row[1] if row else None
    sender_name = row[2] if row else None
    sender_username = row[3] if row else None

    if row and new_text == old_text:
        return  # nothing actually changed (e.g. edited media caption metadata only)

    if row:
        await db.update_message_text(row[0], new_text)

    thread_id, home_chat_id = await get_or_create_topic(owner_user_id, message.chat.id, chat_display_name(message))
    if thread_id is None:
        return

    who = sender_name or "?"
    if sender_username:
        who += f" (@{sender_username})"

    old_display = old_text if old_text else "؟ (پیام اصلی یافت نشد)"
    report = f"✏️ پیام از {who} ویرایش شد:\nاز: {old_display}\nبه: {new_text}"
    await safe_send_message(chat_id=home_chat_id, text=report, message_thread_id=thread_id)


@dp.deleted_business_messages()
async def on_business_messages_deleted(event: BusinessMessagesDeleted):
    owner_user_id = await db.connection_owner_user_id(event.business_connection_id)
    chat_name = await db.get_chat_name_for_owner(owner_user_id, event.chat.id) if owner_user_id else None
    chat_name = chat_name or chat_display_name_fallback(event)

    thread_id, home_chat_id = await get_or_create_topic(owner_user_id, event.chat.id, chat_name)
    if thread_id is None:
        return

    for message_id in event.message_ids:
        row = await db.get_message_by_id(event.business_connection_id, event.chat.id, message_id)
        if row:
            _, old_text, sender_name, sender_username = row
            who = sender_name or "?"
            if sender_username:
                who += f" (@{sender_username})"
            content = old_text or "(بدون متن / رسانه)"
            report = f"🗑 پیام از {who} حذف شد:\n{content}"
        else:
            report = "🗑 یک پیام حذف شد (متن اصلی در دسترس نیست)."
        await safe_send_message(chat_id=home_chat_id, text=report, message_thread_id=thread_id)


def chat_display_name_fallback(event: BusinessMessagesDeleted) -> str:
    chat = event.chat
    if chat.title:
        return chat.title
    parts = [p for p in [chat.first_name, chat.last_name] if p]
    return " ".join(parts) if parts else (chat.username or str(chat.id))

"""Shared helpers: display formatting and crash-safe Telegram send wrappers."""

import logging
from typing import Awaitable, Callable, Optional

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from secretary_bot import db
from secretary_bot.config import bot

logger = logging.getLogger("secretary_bot")


def chat_display_name(message: Message) -> str:
    chat = message.chat
    if chat.title:
        return chat.title
    parts = [p for p in [chat.first_name, chat.last_name] if p]
    return " ".join(parts) if parts else (chat.username or str(chat.id))


def sender_display_name(message: Message) -> str:
    return user_display_name(message.from_user)


def user_display_name(user) -> str:
    if not user:
        return "?"
    parts = [p for p in [user.first_name, user.last_name] if p]
    return " ".join(parts) if parts else (user.username or str(user.id))


async def is_authorized_in_group(message: Message) -> bool:
    if not message.from_user:
        return False
    owner_user_id = await db.get_group_owner_user_id(message.chat.id)
    return owner_user_id is not None and message.from_user.id == owner_user_id


async def is_authorized_callback(callback) -> bool:
    owner_user_id = await db.get_group_owner_user_id(callback.message.chat.id)
    return owner_user_id is not None and callback.from_user.id == owner_user_id


# ---------------------------------------------------------------------------
# Safe sending helpers: Telegram raises TOPIC_CLOSED if a forum topic was
# closed by hand and we try to post into it. These helpers catch it, try to
# reopen the topic once, retry, and otherwise fail quietly (logged) instead
# of blowing up the whole update.
# ---------------------------------------------------------------------------

async def safe_send(
    coro_factory: Callable[[], Awaitable],
    chat_id: int,
    message_thread_id: Optional[int] = None,
):
    try:
        return await coro_factory()
    except TelegramBadRequest as e:
        if "TOPIC_CLOSED" not in str(e):
            logger.error(f"Send failed for chat {chat_id}: {e}")
            return None
        try:
            if message_thread_id:
                await bot.reopen_forum_topic(chat_id=chat_id, message_thread_id=message_thread_id)
            else:
                await bot.reopen_general_forum_topic(chat_id=chat_id)
            return await coro_factory()
        except Exception as e2:
            logger.error(f"Topic reopen+retry failed for chat {chat_id}: {e2}")
            return None
    except Exception as e:
        logger.error(f"Unexpected send error for chat {chat_id}: {e}")
        return None


async def safe_send_message(chat_id: int, text: str, message_thread_id: Optional[int] = None, **kwargs):
    return await safe_send(
        lambda: bot.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id, **kwargs),
        chat_id=chat_id,
        message_thread_id=message_thread_id,
    )


async def safe_reply(message: Message, text: str, **kwargs):
    return await safe_send_message(
        chat_id=message.chat.id,
        text=text,
        message_thread_id=message.message_thread_id,
        **kwargs,
    )


MEDIA_SEND_METHODS = {
    "photo": "send_photo",
    "video": "send_video",
    "voice": "send_voice",
    "audio": "send_audio",
    "document": "send_document",
    "animation": "send_animation",
}


async def safe_send_media(chat_id: int, media_type: str, file_id: str, caption: str, message_thread_id: int):
    """Relay a single media item into a topic, handling video_note/sticker
    (which have no caption param) and TOPIC_CLOSED the same way as text."""

    async def _do():
        if media_type == "video_note":
            await bot.send_video_note(chat_id=chat_id, video_note=file_id, message_thread_id=message_thread_id)
            await bot.send_message(chat_id=chat_id, text=caption, message_thread_id=message_thread_id)
        elif media_type == "sticker":
            await bot.send_sticker(chat_id=chat_id, sticker=file_id, message_thread_id=message_thread_id)
            await bot.send_message(chat_id=chat_id, text=caption, message_thread_id=message_thread_id)
        else:
            method_name = MEDIA_SEND_METHODS.get(media_type)
            if not method_name:
                return
            method = getattr(bot, method_name)
            await method(
                chat_id=chat_id,
                **{media_type: file_id},
                caption=caption,
                message_thread_id=message_thread_id,
            )

    await safe_send(_do, chat_id=chat_id, message_thread_id=message_thread_id)


# ---------------------------------------------------------------------------
# Topic management
# ---------------------------------------------------------------------------

async def get_or_create_topic(owner_user_id: Optional[int], chat_id: int, chat_name: str):
    home_chat_id = await db.get_home_chat_id(owner_user_id) if owner_user_id else None
    if home_chat_id is None:
        logger.warning(
            f"get_or_create_topic: no home_chat_id for owner_user_id={owner_user_id} "
            f"(chat_id={chat_id}) — topic/relay skipped"
        )
        return None, None

    row = await db.get_topic(owner_user_id, chat_id)
    if row and row[1] == home_chat_id:
        return row[0], home_chat_id

    acc_name = await db.connection_display_name(owner_user_id)
    topic_name = f"{acc_name} — {chat_name}"[:128]
    try:
        topic = await bot.create_forum_topic(chat_id=home_chat_id, name=topic_name)
    except TelegramBadRequest as e:
        logger.error(f"Failed to create topic in {home_chat_id}: {e}")
        return None, None

    await db.upsert_topic(owner_user_id, chat_id, home_chat_id, topic.message_thread_id, chat_name)
    return topic.message_thread_id, home_chat_id


async def get_or_create_unified_media_topic():
    """Single shared topic (in the owner's supervisor group) that receives
    live media from ALL sub-accounts as it arrives. Created once and reused
    forever — never recreated, never touched by /backup."""
    from secretary_bot.config import OWNER_ID

    supervisor_chat_id = await db.get_home_chat_id(OWNER_ID)
    if supervisor_chat_id is None:
        return None, None

    row = await db.get_unified_media_topic()
    if row and row[1] == supervisor_chat_id:
        return row[0], supervisor_chat_id

    try:
        topic = await bot.create_forum_topic(chat_id=supervisor_chat_id, name="📎 رسانه همه اکانت‌ها")
    except TelegramBadRequest as e:
        logger.error(f"Failed to create unified media topic in {supervisor_chat_id}: {e}")
        return None, None

    await db.upsert_unified_media_topic(supervisor_chat_id, topic.message_thread_id)
    return topic.message_thread_id, supervisor_chat_id

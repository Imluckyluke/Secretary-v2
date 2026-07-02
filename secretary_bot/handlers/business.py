"""Incoming business_message handling: log, relay into topics, auto-reply."""

from aiogram.types import Message

from secretary_bot import db
from secretary_bot.config import OWNER_ID, bot, dp, logger
from secretary_bot.helpers import (
    chat_display_name,
    get_or_create_topic,
    get_or_create_unified_media_topic,
    safe_send_media,
    safe_send_message,
    sender_display_name,
)

MEDIA_FIELDS = (
    ("photo", lambda m: m.photo[-1].file_id if m.photo else None),
    ("video", lambda m: m.video.file_id if m.video else None),
    ("voice", lambda m: m.voice.file_id if m.voice else None),
    ("audio", lambda m: m.audio.file_id if m.audio else None),
    ("document", lambda m: m.document.file_id if m.document else None),
    ("video_note", lambda m: m.video_note.file_id if m.video_note else None),
    ("sticker", lambda m: m.sticker.file_id if m.sticker else None),
    ("animation", lambda m: m.animation.file_id if m.animation else None),
)


def extract_media(message: Message):
    for media_type, getter in MEDIA_FIELDS:
        file_id = getter(message)
        if file_id:
            return media_type, file_id
    return None, None


@dp.business_message()
async def on_business_message(message: Message):
    text = message.text or message.caption or ""
    media_type, file_id = extract_media(message)

    owner_user_id = await db.connection_owner_user_id(message.business_connection_id)
    logger.info(
        f"business_message: business_connection_id={message.business_connection_id} "
        f"resolved owner_user_id={owner_user_id} chat_id={message.chat.id}"
    )

    await db.insert_message(
        chat_id=message.chat.id,
        chat_name=chat_display_name(message),
        sender_id=message.from_user.id if message.from_user else None,
        sender_name=sender_display_name(message),
        sender_username=message.from_user.username if message.from_user else None,
        text=text,
        media_type=media_type,
        file_id=file_id,
        ts=message.date.isoformat() if message.date else db.now_iso(),
        business_connection_id=message.business_connection_id,
        owner_user_id=owner_user_id,
        message_id=message.message_id,
    )

    try:
        thread_id, home_chat_id = await get_or_create_topic(owner_user_id, message.chat.id, chat_display_name(message))
    except Exception as e:
        logger.error(f"Failed to get/create topic: {e}")
        thread_id, home_chat_id = None, None

    if message.from_user and owner_user_id and message.from_user.id == owner_user_id:
        sender_label = "شما"
    else:
        sender_label = sender_display_name(message) + (
            f" (@{message.from_user.username})" if message.from_user and message.from_user.username else ""
        )

    if media_type and file_id:
        caption = f"از: {sender_label}" + (f"\n{text}" if text else "")
        if thread_id is not None:
            await safe_send_media(home_chat_id, media_type, file_id, caption, thread_id)

        if owner_user_id and owner_user_id != OWNER_ID:
            try:
                umt_id, umt_chat_id = await get_or_create_unified_media_topic()
            except Exception as e:
                logger.error(f"Failed to get/create unified media topic: {e}")
                umt_id, umt_chat_id = None, None
            if umt_id is not None:
                acc_name = await db.connection_display_name(owner_user_id)
                unified_caption = (
                    f"👤 {acc_name}\n💬 {chat_display_name(message)}\nاز: {sender_label}"
                    + (f"\n{text}" if text else "")
                )
                await safe_send_media(umt_chat_id, media_type, file_id, unified_caption, umt_id)
    elif text and thread_id is not None:
        await safe_send_message(
            chat_id=home_chat_id,
            text=f"از: {sender_label}\n{text}",
            message_thread_id=thread_id,
        )

    if text and (not message.from_user or not await db.is_self_account(message.from_user.id)):
        await _try_keyword_autoreply(message, owner_user_id, text)


async def _try_keyword_autoreply(message: Message, owner_user_id, text: str):
    krows = await db.get_keywords_for_matching(owner_user_id)
    for keyword, reply_text in krows:
        if keyword.lower() in text.lower():
            try:
                await bot.send_message(
                    chat_id=message.chat.id,
                    text=reply_text,
                    business_connection_id=message.business_connection_id,
                )
            except Exception as e:
                logger.error(f"Failed to send keyword auto-reply: {e}")
            break

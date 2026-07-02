"""/watch command: monitor a user's profile (name/username/bio) for changes.

Uses whichever business-connected account shares a chat with the target user
to call get_chat (Telegram only allows fetching bio/photo for users you share
a chat with). A background task polls periodically and reports diffs into
the owning account's topic.
"""

import asyncio

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.methods import GetChat
from aiogram.types import Message

from secretary_bot import db
from secretary_bot.config import OWNER_ID, PROFILE_WATCH_INTERVAL_SECONDS, bot, dp, logger
from secretary_bot.helpers import chat_display_name, get_or_create_topic, safe_reply, safe_send_message, user_display_name


async def _resolve_target_user_id(message: Message, command: CommandObject):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id, message.reply_to_message.from_user
    if command.args:
        arg = command.args.strip()
        if arg.lstrip("-").isdigit():
            return int(arg), None
    return None, None


@dp.business_message(Command("watch"))
async def cmd_watch(message: Message, command: CommandObject):
    """Usage: reply to a user's message with /watch, or /watch <user_id>."""
    if not message.from_user or message.from_user.id != OWNER_ID:
        return

    target_user_id, target_user = await _resolve_target_user_id(message, command)
    if not target_user_id:
        await bot.send_message(
            chat_id=message.chat.id,
            text="روی پیام کاربر مورد نظر ریپلای کن و /watch رو بفرست، یا آیدی عددی رو بعد از دستور بنویس.",
            business_connection_id=message.business_connection_id,
        )
        return

    owner_user_id = await db.connection_owner_user_id(message.business_connection_id)
    if not owner_user_id:
        return

    try:
        chat = await bot(GetChat(chat_id=target_user_id), business_connection_id=message.business_connection_id)
    except TelegramBadRequest as e:
        await bot.send_message(
            chat_id=message.chat.id,
            text=f"نتونستم اطلاعات این کاربر رو بگیرم: {e}",
            business_connection_id=message.business_connection_id,
        )
        return

    await db.add_watched_profile(
        owner_user_id=owner_user_id,
        watched_user_id=target_user_id,
        business_connection_id=message.business_connection_id,
        first_name=chat.first_name,
        last_name=chat.last_name,
        username=chat.username,
        bio=chat.bio,
    )

    label = user_display_name(target_user) if target_user else str(target_user_id)
    await bot.send_message(
        chat_id=message.chat.id,
        text=f"👀 پروفایل {label} برای تغییرات زیر نظر گرفته شد.",
        business_connection_id=message.business_connection_id,
    )


@dp.business_message(Command("unwatch"))
async def cmd_unwatch(message: Message, command: CommandObject):
    if not message.from_user or message.from_user.id != OWNER_ID:
        return

    target_user_id, target_user = await _resolve_target_user_id(message, command)
    if not target_user_id:
        await bot.send_message(
            chat_id=message.chat.id,
            text="روی پیام کاربر مورد نظر ریپلای کن و /unwatch رو بفرست، یا آیدی عددی رو بعد از دستور بنویس.",
            business_connection_id=message.business_connection_id,
        )
        return

    owner_user_id = await db.connection_owner_user_id(message.business_connection_id)
    if not owner_user_id:
        return

    removed = await db.remove_watched_profile(owner_user_id, target_user_id)
    text = "دیگه زیر نظر نیست." if removed else "این کاربر زیر نظر نبود."
    await bot.send_message(chat_id=message.chat.id, text=text, business_connection_id=message.business_connection_id)


def _diff_lines(label: str, old_val, new_val):
    if (old_val or "") != (new_val or ""):
        old_display = old_val or "—"
        new_display = new_val or "—"
        return f"{label}: از «{old_display}» به «{new_display}»"
    return None


async def check_watched_profiles_once():
    rows = await db.list_watched_profiles()
    for row in rows:
        row_id, owner_user_id, watched_user_id, business_connection_id, first_name, last_name, username, bio = row

        try:
            chat = await bot(GetChat(chat_id=watched_user_id), business_connection_id=business_connection_id)
        except Exception as e:
            logger.warning(f"watch: failed to fetch profile for {watched_user_id}: {e}")
            continue

        changes = [
            _diff_lines("نام", f"{first_name or ''} {last_name or ''}".strip(),
                        f"{chat.first_name or ''} {chat.last_name or ''}".strip()),
            _diff_lines("یوزرنیم", username, chat.username),
            _diff_lines("بیو", bio, chat.bio),
        ]
        changes = [c for c in changes if c]

        if changes:
            # Negative-offset pseudo chat_id so this never collides with a
            # real Telegram chat_id in the topics table.
            pseudo_chat_id = -(10_000_000_000 + watched_user_id)
            thread_id, home_chat_id = await get_or_create_topic(
                owner_user_id, pseudo_chat_id, f"👀 واچ {watched_user_id}"
            )
            if thread_id is not None:
                display_name = f"{chat.first_name or ''} {chat.last_name or ''}".strip() or str(watched_user_id)
                report = f"🔔 تغییر پروفایل {display_name}:\n" + "\n".join(changes)
                await safe_send_message(chat_id=home_chat_id, text=report, message_thread_id=thread_id)

        await db.update_watched_profile_snapshot(row_id, chat.first_name, chat.last_name, chat.username, chat.bio)


async def profile_watch_loop():
    while True:
        try:
            await check_watched_profiles_once()
        except Exception as e:
            logger.error(f"profile_watch_loop error: {e}")
        await asyncio.sleep(PROFILE_WATCH_INTERVAL_SECONDS)

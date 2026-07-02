"""/info command: shows user_id/username/name for a target user.

Usage (from a business-connected account, any chat, no bot membership needed):
- Reply to a message with /info -> info about that sender
- /info @username -> resolves via get_chat (works without shared chat history)
- /info <numeric_id> -> resolves via get_chat (only works with shared history)
- /info with no reply/args -> info about the sender

Also works the same way inside a registered home group (bot is a member).
"""

from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.methods import GetChat
from aiogram.types import Message

from secretary_bot import db
from secretary_bot.config import OWNER_ID, bot, dp, logger
from secretary_bot.helpers import is_authorized_in_group, safe_reply


def format_user_info(user_id: int, first_name: str = "", last_name: str = "",
                      username: str = "", is_bot: bool = False, extra_line: str = "") -> str:
    name = " ".join(p for p in [first_name, last_name] if p) or "?"
    lines = [
        f"👤 نام: {name}",
        f"🆔 آیدی عددی: {user_id}",
        f"یوزرنیم: @{username}" if username else "یوزرنیم: ندارد",
    ]
    if is_bot:
        lines.append("نوع: بات")
    if extra_line:
        lines.append(extra_line)
    return "\n".join(lines)


def format_user_info_from_user(user, extra_line: str = "") -> str:
    return format_user_info(
        user.id, user.first_name or "", user.last_name or "",
        user.username or "", user.is_bot, extra_line,
    )


def _parse_target_arg(command: CommandObject):
    """Returns ('id', int) or ('username', str) or None."""
    if command and command.args:
        arg = command.args.strip().split()[0]
        arg = arg.lstrip("@")
        if arg.lstrip("-").isdigit():
            return "id", int(arg)
        if arg:
            return "username", arg
    return None


async def _resolve_target(target_arg, business_connection_id: str = None):
    """Returns (chat_or_none, error_text_or_none)."""
    kind, value = target_arg
    query = f"@{value}" if kind == "username" else value
    try:
        if business_connection_id:
            chat = await bot(GetChat(chat_id=query), business_connection_id=business_connection_id)
        else:
            chat = await bot(GetChat(chat_id=query))
        return chat, None
    except TelegramBadRequest:
        if kind == "username":
            return None, "کاربری با این یوزرنیم پیدا نشد."
        return None, "نتونستم اطلاعات این کاربر رو بگیرم؛ سابقه چت مشترکی باهاش نیست."
    except Exception as e:
        logger.error(f"/info get_chat failed: {e}")
        return None, "خطا در دریافت اطلاعات."


@dp.message(Command("info"))
async def cmd_info_home_group(message: Message, command: CommandObject):
    """/info inside a registered home group (bot is a member here)."""
    if not await is_authorized_in_group(message):
        return

    if message.reply_to_message and message.reply_to_message.from_user:
        await safe_reply(message, format_user_info_from_user(message.reply_to_message.from_user))
        return

    target_arg = _parse_target_arg(command)
    if target_arg is None:
        if message.from_user:
            await safe_reply(message, format_user_info_from_user(message.from_user))
        else:
            await safe_reply(message, "نتونستم اطلاعات کاربر رو پیدا کنم.")
        return

    chat, error = await _resolve_target(target_arg)
    if error:
        await safe_reply(message, error)
        return
    await safe_reply(message, format_user_info(
        chat.id, chat.first_name or "", chat.last_name or "", chat.username or "",
    ))


@dp.business_message(Command("info"))
async def cmd_info_business(message: Message, command: CommandObject):
    """/info sent from a business-connected account."""
    connected = await db.connection_owner_user_id(message.business_connection_id)
    if connected != OWNER_ID:
        return

    fallback_user = None
    target_arg = None

    if message.reply_to_message and message.reply_to_message.from_user:
        fallback_user = message.reply_to_message.from_user
    else:
        target_arg = _parse_target_arg(command)
        if target_arg is None and message.from_user:
            fallback_user = message.from_user

    if fallback_user is None and target_arg is None:
        return

    if fallback_user is not None:
        extra = "(همین اکانت بیزینس)" if connected and fallback_user.id == connected else ""
        text = format_user_info_from_user(fallback_user, extra)
    else:
        chat, error = await _resolve_target(target_arg, message.business_connection_id)
        if error:
            text = error
        else:
            extra = "(همین اکانت بیزینس)" if connected and chat.id == connected else ""
            text = format_user_info(
                chat.id, chat.first_name or "", chat.last_name or "", chat.username or "",
                extra_line=extra,
            )

    try:
        await bot.send_message(
            chat_id=message.chat.id,
            text=text,
            business_connection_id=message.business_connection_id,
        )
    except Exception as e:
        logger.error(f"/info send failed: {e}")

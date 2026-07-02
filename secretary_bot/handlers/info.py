"""/info command: shows user_id/username/name for a replied-to user (or the
sender if no reply). Works in home groups (bot is a member) and in business
chats (bot acts through a connected business account, so it doesn't need to
be a member of the group itself).
"""

from aiogram.filters import Command
from aiogram.types import Message

from secretary_bot import db
from secretary_bot.config import bot, dp
from secretary_bot.helpers import is_authorized_in_group, safe_reply, user_display_name


def format_user_info(user, extra_line: str = "") -> str:
    lines = [
        f"👤 نام: {user_display_name(user)}",
        f"🆔 آیدی عددی: {user.id}",
        f"یوزرنیم: @{user.username}" if user.username else "یوزرنیم: ندارد",
    ]
    if user.is_bot:
        lines.append("نوع: بات")
    if extra_line:
        lines.append(extra_line)
    return "\n".join(lines)


@dp.message(Command("info"))
async def cmd_info_home_group(message: Message):
    """/info inside a registered home group (bot is a member here)."""
    if not await is_authorized_in_group(message):
        return

    target = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    if not target:
        await safe_reply(message, "نتونستم اطلاعات کاربر رو پیدا کنم.")
        return

    await safe_reply(message, format_user_info(target))


@dp.business_message(Command("info"))
async def cmd_info_business(message: Message):
    """/info sent from a business-connected account, inside any chat that
    account is in — the bot doesn't need to be a member of that chat."""
    target = message.reply_to_message.from_user if message.reply_to_message else message.from_user
    if not target:
        return

    extra = ""
    connected = await db.connection_owner_user_id(message.business_connection_id)
    if connected and target.id == connected:
        extra = "(همین اکانت بیزینس)"

    try:
        await bot.send_message(
            chat_id=message.chat.id,
            text=format_user_info(target, extra),
            business_connection_id=message.business_connection_id,
        )
    except Exception:
        pass

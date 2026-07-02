"""Business connection events and bot-added-to-group events."""

from aiogram.types import BusinessConnection, ChatMemberUpdated

from secretary_bot import db
from secretary_bot.config import OWNER_ID, dp, logger
from secretary_bot.helpers import safe_send_message


@dp.business_connection()
async def on_business_connection(business_conn: BusinessConnection):
    logger.info(f"Business connection: {business_conn.id} enabled={business_conn.is_enabled}")

    user = business_conn.user
    parts = [p for p in [user.first_name, user.last_name] if p]
    display_name = " ".join(parts) if parts else (user.username or str(user.id))

    existed = await db.upsert_connection(
        business_connection_id=business_conn.id,
        user_id=user.id,
        display_name=display_name,
        username=user.username,
        is_enabled=business_conn.is_enabled,
    )

    if existed:
        return

    notify_chat_id = await db.get_home_chat_id(user.id) or await db.get_home_chat_id(OWNER_ID)
    if notify_chat_id:
        await safe_send_message(
            chat_id=notify_chat_id,
            text=f"🔗 اکانت جدید وصل شد: {display_name}" + (f" (@{user.username})" if user.username else ""),
        )


async def _is_known_business_account(user_id: int) -> bool:
    def _q(conn):
        return conn.execute("SELECT 1 FROM connections WHERE user_id = ?", (user_id,)).fetchone()

    return (await db.run_db(_q)) is not None


@dp.my_chat_member()
async def on_bot_membership_change(event: ChatMemberUpdated):
    if event.chat.type not in ("group", "supergroup"):
        return

    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status
    just_joined = old_status in ("left", "kicked") and new_status in ("member", "administrator")
    if not just_joined:
        return

    adder = event.from_user
    if not adder:
        return

    if adder.id == OWNER_ID:
        await db.register_home_group(OWNER_ID, event.chat.id, event.chat.title)
        await safe_send_message(
            chat_id=event.chat.id,
            text="✅ این گروه به‌عنوان گروه سوپروایزر (اکانت اصلی) ثبت شد.\nبرای بکاپ گرفتن از کیورد /backup استفاده کن.",
        )
        return

    if await _is_known_business_account(adder.id):
        await db.register_home_group(adder.id, event.chat.id, event.chat.title)
        await safe_send_message(
            chat_id=event.chat.id,
            text="✅ این گروه برای این اکانت ثبت شد. پیام‌ها اینجا به تفکیک تاپیک لاگ میشن و «تنظیم کیورد» / «لیست کیورد» اینجا کار می‌کنن.",
        )
    else:
        await safe_send_message(
            chat_id=event.chat.id,
            text="⚠️ این اکانت هنوز از تنظیمات بیزینس تلگرام به بات وصل نشده، برای همین این گروه ثبت نشد.",
        )

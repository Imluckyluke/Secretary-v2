"""Application entry point: aiohttp webhook server for the bot."""

import asyncio

from aiohttp import web
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

from secretary_bot import db, handlers  # noqa: F401  (handlers import registers all @dp routes)
from secretary_bot.config import WEBHOOK_PATH, WEBHOOK_URL, PORT, bot, dp, logger
from secretary_bot.handlers.watch import profile_watch_loop

_background_tasks: set[asyncio.Task] = set()


async def on_startup(app: web.Application):
    await db.init_db()
    await bot.set_webhook(f"{WEBHOOK_URL}{WEBHOOK_PATH}")
    logger.info("Webhook set")

    task = asyncio.create_task(profile_watch_loop())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def on_shutdown(app: web.Application):
    for task in _background_tasks:
        task.cancel()
    await bot.delete_webhook()


def main():
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()

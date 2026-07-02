"""Environment configuration and shared bot/dispatcher instances."""

import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("secretary_bot")

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ["OWNER_ID"])
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
WEBHOOK_PATH = "/webhook"
PORT = int(os.environ.get("PORT", 8080))

DB_PATH = "/data/backup.db" if os.path.isdir("/data") else "backup.db"

# How often to poll watched profiles for changes (seconds).
PROFILE_WATCH_INTERVAL_SECONDS = int(os.environ.get("PROFILE_WATCH_INTERVAL_SECONDS", 300))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Telegram Secretary Bot (Business Mode)

Webhook-based Telegram bot (aiogram 3) that runs behind Telegram Business
accounts. Logs business chats into per-chat forum topics, supports keyword
auto-replies, text backups, edit/delete tracking, `/info`, and profile watch.

## Setup

1. Connect via **Settings → Telegram Business → Chatbots → @your_bot_username**
   on each account you want to manage.
2. Each connected business account gets its own home group: add the bot to a
   new group from the owner account (or, for sub-accounts, they add the bot
   themselves) and it's auto-registered as that account's home group.
3. Copy `.env.example` to `.env` and fill in `BOT_TOKEN`, `OWNER_ID`,
   `WEBHOOK_URL`.

## Run

```bash
pip install -r requirements.txt
python -m secretary_bot.main
```

## Features

- **Live logging** — every business chat message is logged to SQLite and
  relayed into a per-chat forum topic in the account's home group.
- **Keyword auto-reply** — send "تنظیم کیورد" in a home group to set up a
  keyword → auto-reply pair; "لیست کیورد" to manage them.
- **`/backup`** — owner-only, in the supervisor group: pick an account, then
  a chat, get a full text backup as a file.
- **`/info`** — reply to a message (or send with no reply) to get that
  user's numeric ID, username, and name. Works both in home groups the bot
  is a member of, and directly in any business chat (no bot membership
  needed there).
- **Edit/delete tracking** — edited or deleted business messages are
  reported into the chat's topic with before/after text.
- **`/watch` / `/unwatch`** — from a business account, reply to a user (or
  give their numeric ID) to monitor their name/username/bio for changes. A
  background task polls periodically (`PROFILE_WATCH_INTERVAL_SECONDS`,
  default 300s) and reports changes into the chat's topic. Only works for
  users who share a chat with the connected business account.

## Project layout

```
secretary_bot/
  config.py       # env vars, bot/dispatcher instances
  db.py           # all SQLite schema + queries
  helpers.py      # display formatting, safe-send wrappers, topic management
  handlers/
    membership.py # business_connection, bot added to group
    business.py   # incoming business_message: log, relay, auto-reply
    edits.py      # edited/deleted business messages
    keywords.py   # keyword setup FSM + list/view/delete
    backup.py     # /backup flow
    info.py       # /info
    watch.py      # /watch, /unwatch, background polling loop
  main.py         # aiohttp webhook server entry point
```

# config.py — bot configuration
# The token lives here, not in main.py. For production, set the BOT_TOKEN
# environment variable instead of editing this file (and never commit it).

import os

TOKEN = os.getenv("BOT_TOKEN")
PANEL_URL = os.getenv("PANEL_URL")
GAMEMENU_URL = os.getenv("GAMEMENU_URL")

# Per-chat switches, stored in the chat_settings JSON blob (no schema change).
# The bot drives them with commands; a panel can read/write the same keys.
#
# key            default  behaviour
# anti_ad        True     delete messages containing links
# chat_locked    False    chat-wide send lock (only admins can post)
# block_media    False    no photos/videos/docs/audio/stickers for members
# block_forward  False    delete forwarded messages
# block_mentions False    delete @everyone / @admin messages
CHAT_SETTINGS_DEFAULTS: dict[str, bool] = {
    "anti_ad": True,
    "chat_locked": False,
    "block_media": False,
    "block_forward": False,
    "block_mentions": False,
}

# Telegram rejects messages with more than 100 mention entities, and long
# ones hit flood limits, so /mentionall sends in chunks with a pause.
MENTION_BATCH_SIZE = 50
MENTION_DELAY_SECONDS = 1.0

# Future knobs (economy, minigames, ...) can live here too, e.g.:
# DAILY_BONUS = 100

# run the webapp server: uvicorn webapp.server:app --host 0.0.0.0 --port 8000 --reload
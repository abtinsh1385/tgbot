# config.py — bot configuration
# The token lives here, not in main.py. For production, set the BOT_TOKEN
# environment variable instead of editing this file (and never commit it).

import os

TOKEN = os.getenv("BOT_TOKEN", "8809834415:AAHJQlSe-vTM5aEH59C-eR1cKJIEY6Y_sKM")
PANEL_URL = os.getenv("PANEL_URL", "https://laughing-happiness-jvpg5p9g6xw2599q-8000.app.github.dev/panel")

# Future knobs (economy, minigames, ...) can live here too, e.g.:
# DAILY_BONUS = 100

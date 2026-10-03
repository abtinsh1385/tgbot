# Telegram Group Bot

A Telegram bot for group moderation and member management, with a web-based
settings panel, a games menu, mini-games, and a coin economy. The project has
two processes: the bot and the FastAPI web app.

## Features

- Group moderation tools, including anti-ad, chat lock, media, forwarding, and
  mention controls.
- Member tracking and group administration commands.
- A Telegram Web App settings panel and games menu.
- Coin balances, daily rewards, transfers, transaction history, leaderboards,
  and mini-games.
- Choose between SQLite (the default) and PostgreSQL for persistent storage.

## Requirements

- Python 3.10 or newer.
- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- Public HTTPS URLs for the settings panel and games menu when using Telegram
  Web Apps.

## Setup

Run these commands from the project root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Set the following environment variables before starting either process:

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | Yes | Telegram bot token. |
| `PANEL_URL` | Yes for the panel | Public HTTPS URL of the web app's `/panel` page, for example `https://your-domain.example/panel`. |
| `GAMEMENU_URL` | Yes for the menu | Public HTTPS URL of the web app's `/gamemenu` page, for example `https://your-domain.example/gamemenu`. |
| `DATABASE_URL` | PostgreSQL only | Connection URL for your PostgreSQL database. Use the same value for both processes. |

### Choose a database

You can use either SQLite or PostgreSQL:

- **SQLite (default):** no database service or extra configuration is needed.
  The app creates `bot.db` in the project directory on first run.
- **PostgreSQL:** use the PostgreSQL implementation provided in
  `database.py-postgresql` as the active `database.py` module, then set
  `DATABASE_URL` in the environment for both the bot and web app. The
  PostgreSQL driver (`asyncpg`) is included in `requirements.txt`.

Both processes must use the same database. With SQLite, make sure they can
access the same persistent `bot.db` file. With PostgreSQL, configure both
processes to use the same `DATABASE_URL`.

For local development, you can export them in the shell:

```bash
export BOT_TOKEN="your-telegram-bot-token"
export PANEL_URL="https://your-public-domain.example/panel"
export GAMEMENU_URL="https://your-public-domain.example/gamemenu"
```

Telegram Web Apps must be reachable over HTTPS. For local testing, expose the
web app through an HTTPS tunnel and use that public URL for `PANEL_URL` and
`GAMEMENU_URL`.

## Run locally

Start the bot and web app in **separate terminals**, from the project root.
Both processes need the environment variables above.

Terminal 1 — bot:

```bash
python main.py
```

Terminal 2 — web app:

```bash
uvicorn webapp.server:app --host 0.0.0.0 --port 8000 --reload
```

The web app serves the home page at `/`, the panel at `/panel`, and the games
menu at `/gamemenu`. The web app's port must be reachable through your host or
reverse proxy. Use the matching public HTTPS URLs in `PANEL_URL` and
`GAMEMENU_URL`.

## Deploy

Configure `BOT_TOKEN`, `PANEL_URL`, and `GAMEMENU_URL` in the deployment
environment, then run **both commands concurrently as separate tasks/services**:

**Task 1 — Telegram bot**

```bash
python main.py
```

**Task 2 — web app**

```bash
uvicorn webapp.server:app --host 0.0.0.0 --port [PORT] --reload
```

Replace `[PORT]` with the port assigned by your hosting provider. Configure
your public HTTPS domain or reverse proxy to route to the web app on that
port. The bot and web app must use the same `PANEL_URL` and `GAMEMENU_URL`
values, and must connect to the same database. If using SQLite, persist
`bot.db` across restarts and make it accessible to both tasks. If the tasks
run in separate containers or hosts, use shared persistent storage for the
SQLite file, or choose PostgreSQL and set the same `DATABASE_URL` for both.

For group moderation, add the bot to the relevant groups and grant the
permissions required for the moderation features you intend to use.
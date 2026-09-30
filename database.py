"""
database.py — async SQLite layer for the Abtin Telegram bot.

Design notes:
- aiosqlite: one async connection, WAL mode. Zero setup, fast enough for a
  Telegram bot, and easy to swap for Postgres later if the bot grows.
- Versioned migrations via PRAGMA user_version: to change the schema in the
  future, append a new SQL script to MIGRATIONS (never edit old ones) and the
  change applies on next startup.
- Economy is global (one wallet per user across all chats) with a full
  transaction ledger, so every coin movement is auditable.
- Chat management (welcome messages, rules, toggles...) is stored as a JSON
  blob per chat in chat_settings — add keys freely, no schema change needed.

Run `python database.py` to execute a quick self-test against a temp DB.
"""

from __future__ import annotations

import json
import logging
import time
import tempfile
from pathlib import Path
from typing import Any, Optional

import aiosqlite

log = logging.getLogger(__name__)

# DB file lives next to this module, so the bot works no matter the CWD.
DB_PATH = Path(__file__).with_name("bot.db")


def _now() -> int:
    """Unix timestamp as int — used for all time columns."""
    return int(time.time())


class InsufficientFunds(Exception):
    """A debit/transfer would push a wallet below zero."""


class UnknownUser(Exception):
    """The target user has no wallet (transfers require a known recipient)."""


# ---------------------------------------------------------------------------
# Schema migrations
# ---------------------------------------------------------------------------

MIGRATIONS: list[str] = [
    # v1 — core schema: users, chats, global economy, minigame stats
    """
    CREATE TABLE IF NOT EXISTS users (
        user_id     INTEGER PRIMARY KEY,          -- Telegram user id
        username    TEXT,
        first_name  TEXT,
        created_at  INTEGER NOT NULL,
        updated_at  INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS chats (
        chat_id     INTEGER PRIMARY KEY,          -- Telegram chat id
        chat_type   TEXT NOT NULL,                -- private / group / supergroup / channel
        title       TEXT,
        created_at  INTEGER NOT NULL,
        updated_at  INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS chat_settings (
        chat_id     INTEGER PRIMARY KEY
                    REFERENCES chats(chat_id) ON DELETE CASCADE,
        settings    TEXT NOT NULL DEFAULT '{}'    -- JSON blob; extend freely
    );

    CREATE TABLE IF NOT EXISTS wallets (
        user_id     INTEGER PRIMARY KEY
                    REFERENCES users(user_id) ON DELETE CASCADE,
        balance     INTEGER NOT NULL DEFAULT 0 CHECK (balance >= 0),
        updated_at  INTEGER NOT NULL
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id       INTEGER NOT NULL
                      REFERENCES users(user_id) ON DELETE CASCADE,
        amount        INTEGER NOT NULL,           -- positive = earn, negative = spend
        reason        TEXT NOT NULL,
        balance_after INTEGER NOT NULL,
        created_at    INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_tx_user_time
        ON transactions (user_id, created_at DESC);

    CREATE TABLE IF NOT EXISTS minigame_stats (
        user_id   INTEGER NOT NULL
                  REFERENCES users(user_id) ON DELETE CASCADE,
        game      TEXT NOT NULL,
        plays     INTEGER NOT NULL DEFAULT 0,
        wins      INTEGER NOT NULL DEFAULT 0,
        losses    INTEGER NOT NULL DEFAULT 0,
        net       INTEGER NOT NULL DEFAULT 0,     -- net coins won/lost
        PRIMARY KEY (user_id, game)
    );
    """,
    # v2 — which users the bot has seen in which chat, and in what role.
    # Needed for per-chat features such as /mentionall. Existing databases
    # already carry this table; the IF NOT EXISTS keeps both cases safe.
    """
    CREATE TABLE IF NOT EXISTS chat_members (
        chat_id       INTEGER NOT NULL
                      REFERENCES chats(chat_id) ON DELETE CASCADE,
        user_id       INTEGER NOT NULL
                      REFERENCES users(user_id) ON DELETE CASCADE,
        status        TEXT NOT NULL DEFAULT 'member',
        first_seen_at INTEGER NOT NULL,
        joined_at     INTEGER,
        left_at       INTEGER,
        last_seen_at  INTEGER NOT NULL,
        updated_at    INTEGER NOT NULL,
        source        TEXT NOT NULL DEFAULT 'live',
        PRIMARY KEY (chat_id, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_chat_members_user
        ON chat_members (user_id, status);
    CREATE INDEX IF NOT EXISTS idx_chat_members_chat_status
        ON chat_members (chat_id, status);
    """,
]


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class Database:
    def __init__(self, path: str | Path = DB_PATH) -> None:
        self._path = Path(path)
        self._conn: Optional[aiosqlite.Connection] = None

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        if self._conn is not None:
            return
        # isolation_level=None -> autocommit mode; transactions are managed
        # explicitly with BEGIN IMMEDIATE / COMMIT where multi-statement
        # atomicity is needed (e.g. transfers).
        self._conn = await aiosqlite.connect(self._path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._migrate()
        log.info("Database ready: %s", self._path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            log.info("Database closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database is not connected — call connect() first")
        return self._conn

    async def _migrate(self) -> None:
        cur = await self.conn.execute("PRAGMA user_version")
        version = (await cur.fetchone())[0]
        for i, script in enumerate(MIGRATIONS, start=1):
            if i <= version:
                continue
            await self.conn.executescript(script)
            await self.conn.execute(f"PRAGMA user_version = {i}")
            log.info("Applied database migration v%d", i)

    # -- generic helpers ---------------------------------------------------

    async def fetchone(self, sql: str, params: tuple = ()) -> Optional[aiosqlite.Row]:
        cur = await self.conn.execute(sql, params)
        return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        cur = await self.conn.execute(sql, params)
        return await cur.fetchall()

    # -- users & chats -----------------------------------------------------

    async def ensure_user(self, user_id: int, username: Optional[str],
                          first_name: Optional[str]) -> aiosqlite.Row:
        """
        Insert the user if new, otherwise refresh the profile fields.

        COALESCE matters here: group events can carry only a name and no
        username, and that must not wipe a username we already know.
        """
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = COALESCE(excluded.username, users.username),
                first_name = COALESCE(excluded.first_name, users.first_name),
                updated_at = excluded.updated_at
            WHERE users.username  IS NOT COALESCE(excluded.username, users.username)
               OR users.first_name IS NOT COALESCE(excluded.first_name, users.first_name)
            """,
            (user_id, username, first_name, now, now),
        )
        return await self.fetchone(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )

    async def get_user(self, user_id: int) -> Optional[aiosqlite.Row]:
        return await self.fetchone(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        )

    async def get_user_by_username(self, username: str) -> Optional[aiosqlite.Row]:
        """Note: Telegram usernames change often; prefer user ids when possible."""
        return await self.fetchone(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        )

    async def get_users_in_chat(self, chat_id: int) -> list[aiosqlite.Row]:
        """Users the bot has seen in this chat, with a usable username."""
        return await self.fetchall(
            """
            SELECT u.user_id, u.username, u.first_name, m.last_seen_at
            FROM users AS u
            JOIN chat_members AS m ON m.user_id = u.user_id
            WHERE m.chat_id = ?
              AND m.status NOT IN ('left', 'kicked')
              AND u.username IS NOT NULL
            ORDER BY m.last_seen_at DESC
            """,
            (chat_id,),
        )

    async def get_chats_by_type(self, *chat_types: str) -> list[aiosqlite.Row]:
        """Stored chats, optionally filtered by type (group, supergroup, ...)."""
        if not chat_types:
            return await self.fetchall("SELECT * FROM chats ORDER BY chat_id")
        placeholders = ",".join("?" for _ in chat_types)
        return await self.fetchall(
            f"SELECT * FROM chats WHERE chat_type IN ({placeholders}) "
            "ORDER BY chat_id",
            chat_types,
        )

    async def get_unlinked_users(self) -> list[aiosqlite.Row]:
        """Users the bot knows (e.g. from /start) but has never linked to a chat."""
        return await self.fetchall(
            """
            SELECT u.user_id, u.username, u.first_name
            FROM users AS u
            WHERE NOT EXISTS (
                SELECT 1 FROM chat_members AS m WHERE m.user_id = u.user_id
            )
            ORDER BY u.updated_at DESC
            """
        )

    async def upsert_chat_member(
        self,
        chat_id: int,
        user_id: int,
        *,
        status: str = "member",
        source: str = "live",
    ) -> None:
        """
        Record that a user belongs to a chat. The user and chat rows are
        created on demand, so callers can pass raw ids.

        Status changes coming from live membership events always win; a
        plain sighting (source='message') never downgrades someone who is
        already an admin, and never resurrects someone who left.
        """
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO chat_members (
                chat_id, user_id, status, first_seen_at, joined_at,
                left_at, last_seen_at, updated_at, source
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                status = CASE
                    WHEN excluded.source = 'message'
                         AND chat_members.status NOT IN ('left', 'kicked')
                        THEN chat_members.status
                    ELSE excluded.status
                END,
                joined_at = CASE
                    WHEN chat_members.status IN ('left', 'kicked')
                         AND excluded.status NOT IN ('left', 'kicked')
                        THEN excluded.updated_at
                    ELSE COALESCE(chat_members.joined_at, excluded.joined_at)
                END,
                left_at = CASE
                    WHEN excluded.status IN ('left', 'kicked')
                        THEN excluded.updated_at
                    ELSE NULL
                END,
                last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at,
                source = excluded.source
            """,
            (chat_id, user_id, status, now, now, now, now, source),
        )

    async def ensure_chat(self, chat_id: int, chat_type: str,
                          title: Optional[str] = None) -> aiosqlite.Row:
        now = _now()
        await self.conn.execute(
            """
            INSERT INTO chats (chat_id, chat_type, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title      = excluded.title,
                updated_at = excluded.updated_at
            WHERE chats.title IS NOT excluded.title
            """,
            (chat_id, chat_type, title, now, now),
        )
        await self.conn.execute(
            "INSERT OR IGNORE INTO chat_settings (chat_id) VALUES (?)", (chat_id,)
        )
        return await self.fetchone(
            "SELECT * FROM chats WHERE chat_id = ?", (chat_id,)
        )

    async def get_chat_setting(self, chat_id: int, key: str,
                               default: Any = None) -> Any:
        row = await self.fetchone(
            "SELECT settings FROM chat_settings WHERE chat_id = ?", (chat_id,)
        )
        if row is None:
            return default
        return json.loads(row["settings"]).get(key, default)

    async def set_chat_setting(self, chat_id: int, key: str, value: Any) -> None:
        await self.conn.execute(
        """
        INSERT OR IGNORE INTO chats (chat_id, chat_type, created_at, updated_at)
        VALUES (?, 'unknown', ?, ?)
        """,
        (chat_id, _now(), _now()),
        )

        row = await self.fetchone(
            "SELECT settings FROM chat_settings WHERE chat_id = ?", (chat_id,)
        )
        data = json.loads(row["settings"]) if row else {}
        data[key] = value
        await self.conn.execute(
            """
            INSERT INTO chat_settings (chat_id, settings) VALUES (?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET settings = excluded.settings
            """,
            (chat_id, json.dumps(data, ensure_ascii=False)),
        )

    # -- economy (global wallets) -------------------------------------------

    async def get_balance(self, user_id: int) -> int:
        row = await self.fetchone(
            "SELECT balance FROM wallets WHERE user_id = ?", (user_id,)
        )
        return row["balance"] if row else 0

    async def _ensure_wallet(self, user_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO wallets (user_id, balance, updated_at) "
            "VALUES (?, 0, ?)",
            (user_id, _now()),
        )

    async def add_balance(self, user_id: int, amount: int, reason: str,
                          *, allow_negative: bool = False) -> int:
        """
        Credit (+) or debit (-) a wallet and write a ledger entry.
        Raises InsufficientFunds if a debit would go below zero.
        Returns the new balance.
        """
        if amount == 0:
            raise ValueError("amount must be non-zero")
        await self._ensure_wallet(user_id)

        if allow_negative:
            cur = await self.conn.execute(
                "UPDATE wallets SET balance = balance + ?, updated_at = ? "
                "WHERE user_id = ?",
                (amount, _now(), user_id),
            )
        else:
            cur = await self.conn.execute(
                "UPDATE wallets SET balance = balance + ?, updated_at = ? "
                "WHERE user_id = ? AND balance + ? >= 0",
                (amount, _now(), user_id, amount),
            )
        if cur.rowcount == 0:
            raise InsufficientFunds(
                f"user {user_id}: not enough coins for {amount:+d}"
            )
        balance = (await self.fetchone(
            "SELECT balance FROM wallets WHERE user_id = ?", (user_id,)
        ))["balance"]
        await self.conn.execute(
            "INSERT INTO transactions (user_id, amount, reason, balance_after, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, reason, balance, _now()),
        )
        return balance

    async def transfer(self, from_user: int, to_user: int, amount: int,
                       reason: str) -> tuple[int, int]:
        """
        Atomically move coins between two wallets.
        Returns (sender_balance, receiver_balance).
        """
        if amount <= 0:
            raise ValueError("transfer amount must be positive")
        if from_user == to_user:
            raise ValueError("cannot transfer to self")
        if await self.get_balance(to_user) == 0 and await self.get_user(to_user) is None:
            raise UnknownUser(f"user {to_user} has no wallet")

        now = _now()
        await self._ensure_wallet(from_user)
        await self._ensure_wallet(to_user)

        await self.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await self.conn.execute(
                "UPDATE wallets SET balance = balance - ?, updated_at = ? "
                "WHERE user_id = ? AND balance >= ?",
                (amount, now, from_user, amount),
            )
            if cur.rowcount == 0:
                raise InsufficientFunds(
                    f"user {from_user}: not enough coins to send {amount}"
                )
            await self.conn.execute(
                "UPDATE wallets SET balance = balance + ?, updated_at = ? "
                "WHERE user_id = ?",
                (amount, now, to_user),
            )
            sender_bal = (await self.fetchone(
                "SELECT balance FROM wallets WHERE user_id = ?", (from_user,)
            ))["balance"]
            receiver_bal = (await self.fetchone(
                "SELECT balance FROM wallets WHERE user_id = ?", (to_user,)
            ))["balance"]
            for uid, bal in ((from_user, sender_bal), (to_user, receiver_bal)):
                await self.conn.execute(
                    "INSERT INTO transactions (user_id, amount, reason, "
                    "balance_after, created_at) VALUES (?, ?, ?, ?, ?)",
                    (uid, -amount if uid == from_user else amount,
                     reason, bal, now),
                )
            await self.conn.execute("COMMIT")
            return sender_bal, receiver_bal
        except BaseException:
            await self.conn.execute("ROLLBACK")
            raise

    async def get_transaction_history(
        self, user_id: int, limit: int = 10
    ) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT amount, reason, balance_after, created_at "
            "FROM transactions WHERE user_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        )

    async def get_leaderboard(self, limit: int = 10) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT w.user_id, w.balance, u.username, u.first_name "
            "FROM wallets w JOIN users u ON u.user_id = w.user_id "
            "ORDER BY w.balance DESC LIMIT ?",
            (limit,),
        )

    # -- minigames -----------------------------------------------------------

    async def record_minigame(self, user_id: int, game: str, *, won: bool,
                              delta: int) -> None:
        """Count a round and its net coin change for a given game."""
        await self.conn.execute(
            """
            INSERT INTO minigame_stats (user_id, game, plays, wins, losses, net)
            VALUES (?, ?, 1, ?, ?, ?)
            ON CONFLICT(user_id, game) DO UPDATE SET
                plays  = plays + 1,
                wins   = wins   + excluded.wins,
                losses = losses + excluded.losses,
                net    = net    + excluded.net
            """,
            (user_id, game, 1 if won else 0, 0 if won else 1, delta),
        )

    async def get_minigame_stats(
        self, user_id: int, game: Optional[str] = None
    ) -> list[aiosqlite.Row]:
        if game:
            return await self.fetchall(
                "SELECT * FROM minigame_stats WHERE user_id = ? AND game = ?",
                (user_id, game),
            )
        return await self.fetchall(
            "SELECT * FROM minigame_stats WHERE user_id = ? ORDER BY plays DESC",
            (user_id,),
        )


# ---------------------------------------------------------------------------
# Self-test: `python database.py`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio
    import os

    async def _self_test() -> None:
        logging.basicConfig(level=logging.INFO)
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "test.db")
            await db.connect()

            # users & chats
            u = await db.ensure_user(100, "abtin", "Abtin")
            assert dict(u)["username"] == "abtin"
            await db.ensure_user(100, "abtin_tm", "Abtin")  # rename -> update
            assert (await db.get_user(100))["username"] == "abtin_tm"
            await db.ensure_chat(-100123, "supergroup", "Test Group")
            await db.set_chat_setting(-100123, "welcome", "خوش اومدی!")
            assert await db.get_chat_setting(-100123, "welcome") == "خوش اومدی!"
            assert await db.get_chat_setting(-100123, "nope", "dflt") == "dflt"

            # economy
            assert await db.get_balance(100) == 0
            b = await db.add_balance(100, 500, "daily bonus")
            assert b == 500
            try:
                await db.add_balance(100, -600, "overspend")
                assert False, "should have raised"
            except InsufficientFunds:
                pass
            await db.ensure_user(200, "sara", "Sara")
            s_bal, r_bal = await db.transfer(100, 200, 300, "gift")
            assert (s_bal, r_bal) == (200, 300)
            history = await db.get_transaction_history(100)
            assert len(history) == 3  # +500, -600 denied (no row), -300
            top = await db.get_leaderboard(5)
            assert top[0]["user_id"] == 200

            # minigames
            await db.record_minigame(100, "coinflip", won=True, delta=50)
            await db.record_minigame(100, "coinflip", won=False, delta=-40)
            stats = (await db.get_minigame_stats(100, "coinflip"))[0]
            assert stats["plays"] == 2 and stats["wins"] == 1 and stats["net"] == 10

            await db.close()
        print("database self-test OK ✅")

    asyncio.run(_self_test())

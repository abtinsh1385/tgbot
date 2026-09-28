"""
modules/members.py — keep the user table populated from live group activity.

The bot used to learn a user only when that person ran /start, which meant
`/ban @username` failed for anyone who had never opened the bot's chat. That
data is already in the updates Telegram sends us, so we record it:

* every visible group message registers its sender,
* join/leave/kick/admin events register the affected user,
* Telegram's service messages ("X added Y", "Y left") register the users
  named in them.

The only members this cannot see are the ones that never speak, never join
and never leave while the bot is online — Telegram has no API for listing
them. That would need a user account (Telethon), not a bot.

Sender registration runs as middleware rather than a catch-all handler so
that anti-ad and the command handlers still receive every message.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import ChatMemberUpdated, Message

from database import Database

router = Router(name="members")
log = logging.getLogger(__name__)

_GROUP_TYPES = {ChatType.GROUP, ChatType.SUPERGROUP}

# Usernames inside Telegram's service messages, e.g. "added @someone" or
# "invited @another to the group".
_USERNAME_RE = re.compile(r"@([a-zA-Z][a-zA-Z0-9_]{4,})")


def _status_value(status: object) -> str:
    """aiogram's status may be a StrEnum; store a plain lowercase string."""
    return str(getattr(status, "value", status)).lower()


async def _record_user(
    db: Database,
    user: Any,
    *,
    chat_id: int | None = None,
    status: str = "member",
    source: str = "message",
) -> None:
    """
    Store a Telegram User object, ignoring bots.

    When chat_id is given the user is also linked to that chat in
    chat_members, which is what /mentionall reads.
    """
    user_id = getattr(user, "id", None)
    if not user_id or getattr(user, "is_bot", False):
        return
    await db.ensure_user(
        user_id,
        getattr(user, "username", None),
        getattr(user, "first_name", None),
    )
    if chat_id is not None:
        await db.upsert_chat_member(chat_id, user_id, status=status, source=source)


async def record_message_sender(
    handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
    event: Message,
    data: dict[str, Any],
) -> Any:
    """Register the sender of every visible group message, then continue."""
    db = data.get("db")
    if db is not None and event.chat.type in _GROUP_TYPES:
        if event.from_user is not None and event.from_user.id != event.bot.id:
            await _record_user(db, event.from_user, chat_id=event.chat.id)
        elif event.sender_chat is not None:
            # Anonymous admin posts: the group itself is the sender, so store
            # nothing, but make sure the chat row exists.
            await db.ensure_chat(event.chat.id, event.chat.type, event.chat.title)
        await _record_service_usernames(db, event)
    return await handler(event, data)


async def _record_service_usernames(db: Database, event: Message) -> None:
    """
    Remember usernames seen in Telegram's own service messages.

    A "user added @someone" message tells us the username but not the id, so
    there is nothing to insert into `users` yet. Instead we cache it and use
    it to resolve @username targets in /ban, /mute and friends. The real row
    appears as soon as that user posts or changes membership.
    """
    text = event.text or ""
    if not any(word in text for word in ("added", "invited", "joined", "left", "kicked")):
        return

    usernames = [name.lower() for name in _USERNAME_RE.findall(text)]
    if not usernames:
        return
    known = await db.get_chat_setting(event.chat.id, "seen_usernames", [])
    merged = sorted({*known, *usernames})
    await db.set_chat_setting(event.chat.id, "seen_usernames", merged)


@router.chat_member()
async def record_membership_change(event: ChatMemberUpdated, db: Database) -> None:
    """Record joins, leaves, kicks, bans and admin promotions."""
    if event.chat.type not in _GROUP_TYPES:
        return

    await db.ensure_chat(event.chat.id, event.chat.type, event.chat.title)

    # Both sides are User objects; the new status is what matters.
    for member in (event.old_chat_member, event.new_chat_member):
        if member is None:
            continue
        status = (
            _status_value(member.status)
            if member is event.new_chat_member
            else "member"
        )
        await _record_user(
            db,
            member.user,
            chat_id=event.chat.id,
            status=status,
            source="chat_member",
        )


@router.message(F.new_chat_members)
async def record_new_members(message: Message, db: Database) -> None:
    """Record everyone listed in an "added to group" service message."""
    if message.chat.type not in _GROUP_TYPES:
        return
    for member in message.new_chat_members or ():
        await _record_user(db, member, chat_id=message.chat.id, source="join")


@router.message(F.left_chat_member)
async def record_left_member(message: Message, db: Database) -> None:
    """Keep a departing member's profile on file (just refreshes username)."""
    if message.chat.type not in _GROUP_TYPES:
        return
    member = message.left_chat_member
    if member is not None:
        await _record_user(
            db,
            member,
            chat_id=message.chat.id,
            status="left",
            source="left",
        )


async def register_known_admins(bot: Any, db: Database, chat_id: int) -> int:
    """
    Store every current admin of a group. Admins are always retrievable, so
    this closes a real gap even for groups the bot is only watching.

    Returns how many admins were stored.
    """
    count = 0
    try:
        for member in await bot.get_chat_administrators(chat_id):
            await _record_user(
                db,
                member.user,
                chat_id=chat_id,
                status=_status_value(member.status),
                source="admin",
            )
            count += 1
    except Exception as exc:  # missing rights or kicked bot — not fatal
        log.debug("Could not list admins of %s: %s", chat_id, exc)
    return count

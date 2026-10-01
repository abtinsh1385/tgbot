from __future__ import annotations
import asyncio
import logging
import re
from datetime import timedelta
from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatPermissions, Message, User
from config import (
    CHAT_SETTINGS_DEFAULTS,
    MENTION_BATCH_SIZE,
    MENTION_DELAY_SECONDS,
)
from database import Database, _now
from permissions import describe as describe_policy
from permissions import for_chat
import permissions
from aiogram import F

router = Router(name="mod")
log = logging.getLogger(__name__)

_AD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(?:t|telegram)\.me/(?:\S+)"),
    re.compile(r"(?<![\w@])@[a-zA-Z][a-zA-Z0-9_]{4,}\b"),
    re.compile(r"(?i)\bhttps?://\S+"),
    re.compile(
        r"(?i)(?<![\w@.])"
        r"[a-z0-9][a-z0-9-]{1,61}\."
        r"(?:com|net|org|io|ir|co|me|xyz|info|biz|online|site|shop|link|live|app|dev|gg|tv|ru|pro|top|club)\b"),
    re.compile(r"(?i)(?<![\w@.])www\.[a-z0-9][a-z0-9-]*\.[a-z0-9-]"),
]


def looks_like_ad(text: str) -> bool:
    return any(x.search(text) for x in _AD_PATTERNS)


def _whitelisted(text: str, whitelist: list[str]) -> bool:
    instance=text
    for i in whitelist:
        instance=instance.replace(i,"")
    return not looks_like_ad(instance)


_DURATION_RE = re.compile(r"^(\d+)(m|min|دقیقه|h|ساعت|d|روز|w|هفته)$", re.IGNORECASE)
MIN_MUTE = timedelta(minutes=10)
MAX_MUTE = timedelta(days=30)


def parse_duration(raw: str | None) -> timedelta | None:
    if not raw:
        return None
    m = _DURATION_RE.match(raw.strip())
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    seconds = {
        "m": 60, "min": 60, "دقیقه": 60,
        "h": 3600, "ساعت": 3600,
        "d": 86400, "روز": 86400,
        "w": 604800, "هفته": 604800,
    }[unit] * value
    delta = timedelta(seconds=seconds)
    if delta < MIN_MUTE or delta > MAX_MUTE:
        return None
    return delta


def split_target_and_duration(args: str | None) -> tuple[str | None, str | None]:
    if not args:
        return None, None
    tokens = args.strip().split()
    if not tokens:
        return None, None
    if len(tokens) == 1:
        if _DURATION_RE.match(tokens[0]):
            return None, tokens[0]
        return tokens[0], None
    return tokens[0], tokens[-1]


async def _is_bot_admin_safe(message: Message) -> bool:
    try:
        me = await message.chat.get_member((await message.bot.get_me()).id)
        return me.status in ("administrator", "creator")
    except (TelegramForbiddenError, TelegramBadRequest):
        return False


async def _resolve_target(message: Message, args: str | None, db: Database) -> User | None:
    chat = message.chat

    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    if not args:
        return None
    args = args.strip()

    if args.lstrip("-").isdigit():
        numeric = int(args)
        try:
            member = await chat.get_member(numeric)
            return member.user
        except (TelegramForbiddenError, TelegramBadRequest):
            # Not a member, or a hidden profile. For mute/ban the id is enough.
            row = await db.get_user(numeric)
            if row is not None:
                return User(
                    id=numeric,
                    first_name=row["first_name"] or f"کاربر {numeric}",
                    username=row["username"],
                )
            return None

    if args.startswith("@"):
        user = await db.get_user_by_username(args[1:])
        if user is not None:
            try:
                member = await chat.get_member(user["user_id"])
                return member.user
            except (TelegramForbiddenError, TelegramBadRequest):
                # Hidden profile or no longer a member: the id we already know
                # is enough for mute/ban, so build a minimal User from the DB.
                return User(
                    id=user["user_id"],
                    first_name=user["first_name"] or f"کاربر {user['user_id']}",
                    username=user["username"],
                )
        return None

    return None


async def _require_admin(message: Message, db: Database) -> bool:
    if message.sender_chat and message.sender_chat.id == message.chat.id:
        return True
    admins = await message.chat.get_administrators()
    return any(a.user.id == message.from_user.id for a in admins)


def _fmt_delta(d: timedelta) -> str:
    total = int(d.total_seconds())
    if total % 86400 == 0:
        return f"{total // 86400} روز"
    if total % 3600 == 0:
        return f"{total // 3600} ساعت"
    if total % 60 == 0:
        return f"{total // 60} دقیقه"
    return f"{total} ثانیه"


def _perms(allowed: bool) -> ChatPermissions:
    """Permission set for a single user (used by /mute and /unmute)."""
    return permissions.full_access() if allowed else permissions.nothing_allowed()


# Labels for /settings. anti_ad has its own /antiad command; forward and
# mention stay deletion-based because Telegram's Bot API exposes no
# chat-wide permission for either.
_SETTING_LABELS = {
    "anti_ad": "🛡️ ضدتبلیغ (حذف پیام تبلیغی)",
    "chat_locked": "🔒 قفل کامل گفتگو",
    "block_media": "📎 منع رسانه (تلگرام اجازه ارسال نمی‌دهد)",
    "block_forward": "🔁 منع فوروارد (حذف پیام فورواردی)",
    "block_mentions": "📣 منع منشن همه (حذف پیام منشن)",
}

# set_chat_permissions has no until_date, so a timed lock needs a task that
# restores the previous permissions. The expiry timestamp is also written to
# chat_settings so a restart can distinguish a live lock from a stale one.
_LOCK_TASKS: dict[int, asyncio.Task] = {}


def _cancel_pending_unlock(chat_id: int) -> None:
    task = _LOCK_TASKS.pop(chat_id, None)
    if task is not None and not task.done():
        task.cancel()


async def _exempt_admins(bot: Bot, chat_id: int) -> list[str]:
    """
    مدیران را از مجوز گروهی معاف کن.

    set_chat_permissions روی همهٔ اعضا اثر می‌گذارد و برای مدیر هم
    استثنا نمی‌کند؛ پس برای اینکه مدیران بتوانند در گروه قفل‌شده پیام
    بدهند، باید جداگانه مجوزشان باز شود.
    """
    freed: list[str] = []
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        log.warning("Could not list admins of %s to exempt them: %s", chat_id, exc)
        return freed

    try:
        bot_id = (await bot.get_me()).id
    except (TelegramForbiddenError, TelegramBadRequest):
        bot_id = None

    for member in admins:
        if bot_id is not None and member.user.id == bot_id:
            continue
        try:
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=member.user.id,
                permissions=permissions.full_access(),
            )
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            # Telegram refuses to lift some admin restrictions; the chat-wide
            # permission still applies to everyone else.
            log.debug("Could not exempt admin %s: %s", member.user.id, exc)
            continue
        freed.append(member.user.full_name)
    return freed


async def _unlock_when_expired(
    bot: Bot,
    db: Database,
    chat_id: int,
    block_media: bool,
    duration: timedelta,
) -> None:
    """Restore permissions and announce once a timed lock expires."""
    try:
        await asyncio.sleep(duration.total_seconds())
        await bot.set_chat_permissions(
            chat_id=chat_id,
            permissions=for_chat(chat_locked=False, block_media=block_media),
        )
        await db.set_chat_setting(chat_id, "chat_locked", False)
        await db.set_chat_setting(chat_id, "lock_until", 0)
        await bot.send_message(chat_id, "🔓 قفل موقت تمام شد و گروه باز شد.")
    except asyncio.CancelledError:
        raise
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        log.warning("Could not auto-unlock chat %s: %s", chat_id, exc)
    finally:
        _LOCK_TASKS.pop(chat_id, None)


async def restore_expired_locks(bot: Bot, db: Database) -> None:
    """
    Call once at startup. Re-applies a still-valid timed lock (the bot was
    offline while it counted down) and releases ones that already ended, so
    a restart can never leave a group locked forever. Admins are exempted
    again, because a lock applied before the restart would otherwise trap
    them along with everyone else.
    """
    for chat in await db.get_chats_by_type("group", "supergroup"):
        chat_id = chat["chat_id"]
        if not await db.get_chat_setting(chat_id, "chat_locked", False):
            continue
        lock_until = await db.get_chat_setting(chat_id, "lock_until", 0)
        block_media = await db.get_chat_setting(chat_id, "block_media", False)
        remaining = int(lock_until) - _now()
        try:
            if remaining > 0:
                await bot.set_chat_permissions(
                    chat_id=chat_id, permissions=for_chat(True, block_media)
                )
                await _exempt_admins(bot, chat_id)
                _LOCK_TASKS[chat_id] = asyncio.create_task(
                    _unlock_when_expired(
                        bot, db, chat_id, block_media, timedelta(seconds=remaining)
                    )
                )
                log.info("Restored timed lock for %s (%ss left)", chat_id, remaining)
            else:
                await bot.set_chat_permissions(
                    chat_id=chat_id, permissions=for_chat(False, block_media)
                )
                await db.set_chat_setting(chat_id, "chat_locked", False)
                await db.set_chat_setting(chat_id, "lock_until", 0)
                log.info("Released stale lock for %s after restart", chat_id)
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            log.warning("Could not reconcile lock for %s: %s", chat_id, exc)


def _fa(number: int | str) -> str:
    """Latin digits to Persian, so message counts read naturally in Persian."""
    return str(number).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _status_of(member: object) -> str:
    """A ChatMember's status as a plain lowercase string."""
    return str(getattr(member, "status", "member")).lower()


async def _require_group_admin(message: Message, db: Database) -> bool:
    """Shared guard: a group/supergroup, and the sender is an admin."""
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("این کامند فقط داخل گروه کار میکنه")
        return False
    if not await _require_admin(message, db):
        await message.answer("فقط ادمین میتواند این کامند را وارد کند")
        return False
    return True


@router.message(Command("antiad"))
async def cmd_antiad(message: Message, db: Database) -> None:
    if message.chat.type==ChatType.PRIVATE:
        await message.answer("این چت private است!")
        return 
    admins = await message.chat.get_administrators()
    if not any(a.user.id == message.from_user.id for a in admins):
        return
    current=await db.get_chat_setting(message.chat.id,"anti_ad",True)
    await db.set_chat_setting(message.chat.id, "anti_ad", not current)
    state = "روشن ✅" if not current else "خاموش ❌"
    await message.answer(f"ضد تبلیغ: {state}")


@router.message(Command("lock", "unlock", "media", "mediaclear"))
async def cmd_chat_permissions(
    message: Message,
    command: CommandObject,
    db: Database,
) -> None:
    """
    قفل گروه یا بستن رسانه — با مجوزهای واقعی تلگرام.

    /lock [مدت]  قفل گروه. با مدت، خودکار باز می‌شود (مثلاً /lock 2h).
    /unlock      بازکردن فوری قفل.
    /media       ارسال عکس/ویدیو/فایل/صدا/استیکر ممنوع (متن آزاد می‌ماند).
    /mediaclear  آزادکردن دوبارهٔ رسانه‌ها.

    set_chat_permissions changes what members are ALLOWED to send, so blocked
    content is never created in the first place — nothing to delete later.
    """
    if not await _require_group_admin(message, db):
        return
    if not await _is_bot_admin_safe(message):
        await message.answer(
            "من ادمین این گروه نیستم! برای تغییر مجوزها، اول من رو ادمین کنید "
            "و دسترسی «Manage Chat» را بدهید."
        )
        return

    chat_id = message.chat.id
    chat_locked = await db.get_chat_setting(chat_id, "chat_locked", False)
    block_media = await db.get_chat_setting(chat_id, "block_media", False)
    duration: timedelta | None = None

    if command.command == "lock":
        chat_locked = True
        duration = parse_duration(command.args)
        if command.args and duration is None:
            await message.answer(
                "❗️ مدت زمان نامعتبر است. نمونهٔ درست: /lock 30m یا /lock 2h\n"
                "واحدها: m (دقیقه) — h (ساعت) — d (روز) — w (هفته)\n"
                "بدون مدت، قفل دائمی می‌شود."
            )
            return
    elif command.command == "unlock":
        chat_locked = False
    elif command.command == "media":
        block_media = not block_media
    else:  # mediaclear
        block_media = False

    try:
        await message.bot.set_chat_permissions(
            chat_id=chat_id,
            permissions=for_chat(chat_locked, block_media),
        )
        # Admins must keep posting, so lift the chat-wide limit for them.
        freed = (
            await _exempt_admins(message.bot, chat_id)
            if (chat_locked or block_media)
            else []
        )
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        await message.answer(
            f"❌ تغییر مجوزهای گروه ممکن نشد. احتمالاً بات ادمین با دسترسی "
            f"«Manage Chat» نیست.\nجزئیات: {exc}"
        )
        return

    await db.set_chat_setting(chat_id, "chat_locked", chat_locked)
    await db.set_chat_setting(chat_id, "block_media", block_media)

    admin_note = (
        f"\n👑 مدیران معاف شدند: {_fa(len(freed))} نفر" if freed else ""
    )

    # A new lock replaces any pending auto-unlock, and vice versa.
    _cancel_pending_unlock(chat_id)
    if chat_locked and duration is not None:
        lock_until = _now() + int(duration.total_seconds())
        await db.set_chat_setting(chat_id, "lock_until", lock_until)
        _LOCK_TASKS[chat_id] = asyncio.create_task(
            _unlock_when_expired(message.bot, db, chat_id, block_media, duration)
        )
        await message.answer(
            f"🔒 گروه به مدت {_fa(_fmt_delta(duration))} قفل شد و بعد خودکار "
            f"باز می‌شود.{admin_note}"
        )
        return
    await db.set_chat_setting(chat_id, "lock_until", 0)

    await message.answer(describe_policy(chat_locked, block_media) + admin_note)


@router.message(Command("forward", "mention"))
async def cmd_deletion_switch(
    message: Message,
    command: CommandObject,
    db: Database,
) -> None:
    """
    /forward و /mention — سوییچ حذف پیام.

    تلگرام برای فوروارد و منشن مجوز گروهی ندارد، پس این دو فقط پیامِ
    متخلف را حذف می‌کنند (برخلاف /lock و /media که اجازهٔ ارسال را می‌بندند).
    """
    if not await _require_group_admin(message, db):
        return

    key = "block_forward" if command.command == "forward" else "block_mentions"
    current = await db.get_chat_setting(message.chat.id, key, False)
    new_value = not current
    await db.set_chat_setting(message.chat.id, key, new_value)
    state = "روشن ✅" if new_value else "خاموش ❌"
    await message.answer(f"{_SETTING_LABELS[key]}: {state}")


@router.message(Command("sync", "link"))
async def cmd_sync(message: Message, db: Database) -> None:
    """
    پیوند کاربران شناخته‌شده با این گروه.

    کاربری که فقط /start زده در جدول users ثبت می‌شود ولی عضویتِ
    گروهی ندارد، و /mentionall او را نمی‌بیند. این کامند هر کاربرِ
    شناخته‌شده را که واقعاً عضو همین گروه است به آن وصل می‌کند.
    """
    if not await _require_group_admin(message, db):
        return
    if not await _is_bot_admin_safe(message):
        await message.answer("من ادمین این گروه نیستم! فقط مدیران قابل بررسی‌اند.")
        return

    chat_id = message.chat.id
    known = await db.get_unlinked_users()
    if not known:
        await message.answer("✅ همهٔ کاربران شناخته‌شده قبلاً به گروه وصل شده‌اند.")
        return

    linked = 0
    not_members: list[str] = []
    for row in known:
        try:
            member = await message.chat.get_member(row["user_id"])
        except (TelegramForbiddenError, TelegramBadRequest):
            not_members.append(
                row["username"] or row["first_name"] or str(row["user_id"])
            )
            continue
        await db.upsert_chat_member(
            chat_id,
            row["user_id"],
            status=_status_of(member),
            source="sync",
        )
        linked += 1

    lines = [f"✅ {_fa(linked)} کاربر به این گروه وصل شد."]
    if not_members:
        preview = "، ".join(not_members[:10])
        more = "" if len(not_members) <= 10 else f" و {_fa(len(not_members) - 10)} نفر دیگر"
        lines.append(f"❌ عضو این گروه نیستند: {preview}{more}")
    await message.answer("\n".join(lines))


@router.message(Command("settings"))
async def cmd_settings(message: Message, db: Database) -> None:
    """نمایش وضعیت فعلی تنظیمات این گروه."""
    if not await _require_group_admin(message, db):
        return

    lines = ["تنظیمات این گروه:"]
    for key, label in _SETTING_LABELS.items():
        default = CHAT_SETTINGS_DEFAULTS[key]
        value = await db.get_chat_setting(message.chat.id, key, default)
        mark = "✅ روشن" if value else "❌ خاموش"
        lines.append(f"{label}: {mark}")

    lock_until = await db.get_chat_setting(message.chat.id, "lock_until", 0)
    if lock_until:
        left = max(0, int(lock_until) - _now())
        if left:
            lines.append(f"⏳ قفل خودکار: {_fa(_fmt_delta(timedelta(seconds=left)))} دیگر")

    lines.append(
        "\nتغییر با کامندها:\n"
        "/antiad ضدتبلیغ (حذف پیام تبلیغی)\n"
        "/lock [مدت] و /unlock قفل کامل گفتگو\n"
        "/media و /mediaclear منع یا آزادسازی رسانه\n"
        "/forward و /mention حذف فوروارد و منشن\n"
        "/mentionall منشن کردن اعضای شناخته‌شده"
    )
    await message.answer("\n".join(lines))


@router.message(Command("who", "whoami", "members"))
async def cmd_who(message: Message, db: Database) -> None:
    """
    گزارش وضعیت اعضای ثبت‌شدهٔ این گروه.

    کمک تشخیصی: نشان می‌دهد بات چه کسانی را می‌شناسد، در جدول
    chat_members هست یا نه، و یوزرنیم عمومی دارد یا نه. /
    mentionall فقط کسانی را منشن می‌کند که هر سه شرط را داشته باشند.
    """
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("این کامند فقط داخل گروه کار میکنه")
        return

    chat_id = message.chat.id
    linked = await db.fetchall(
        """
        SELECT m.user_id, m.status, m.source, m.last_seen_at, u.username
        FROM chat_members AS m
        JOIN users AS u ON u.user_id = m.user_id
        WHERE m.chat_id = ?
        ORDER BY m.last_seen_at DESC
        """,
        (chat_id,),
    )
    known = await db.get_unlinked_users()
    mentionable = await db.get_users_in_chat(chat_id)

    lines = [f"🔎 گزارش اعضای گروه ({message.chat.title}):"]
    if linked:
        lines.append(f"در chat_members: {_fa(len(linked))} نفر")
        for row in linked:
            mark = "✅" if row["username"] else "➖"
            who = f"@{row['username']}" if row["username"] else "بدون یوزرنیم"
            lines.append(
                f"{mark} {who} · {row['status']} · {row['user_id']}"
            )
    else:
        lines.append("در chat_members: هیچ‌کس")

    lines.append(f"قابل منشن: {_fa(len(mentionable))} نفر")
    lines.append(f"شناخته‌شده ولی وصل‌نشده: {_fa(len(known))} نفر")
    for row in known:
        who = f"@{row['username']}" if row["username"] else "بدون یوزرنیم"
        lines.append(f"   ↳ {who} · {row['user_id']}")

    await message.answer("\n".join(lines))


@router.message(Command("mentionall", "call"))
async def cmd_mentionall(message: Message, db: Database) -> None:
    """
    منشن کردن همهٔ اعضایی که بات در این گروه می‌شناسد.

    Telegram caps a message at 100 mention entities and rate-limits bursts, so
    mentions go out in batches with a short pause. Members without a public
    username cannot be mentioned at all, so they are counted and skipped.
    """
    if not await _require_group_admin(message, db):
        return

    if not await _is_bot_admin_safe(message):
        await message.answer("من ادمین این گروه نیستم!")
        return

    members = await db.get_users_in_chat(message.chat.id)
    if not members:
        await message.answer(
            "❗️ هنوز هیچ عضوی در این گروه ثبت نشده. "
            "هرچه اعضا پیام بدهند یا جوین شوند، اینجا شناخته می‌شوند."
        )
        return

    names = [row["username"] for row in members if row["username"]]
    if not names:
        await message.answer(
            "❗️ هیچ‌کدام از اعضای شناخته‌شده یوزرنیم عمومی ندارند، "
            "پس منشن ممکن نیست."
        )
        return

    sent = 0
    for start in range(0, len(names), MENTION_BATCH_SIZE):
        batch = names[start:start + MENTION_BATCH_SIZE]
        try:
            await message.answer(" ".join(f"@{name}" for name in batch))
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            log.warning("mentionall stopped in %s: %s", message.chat.id, exc)
            await message.answer(f"❌ ارسال منشن متوقف شد: {exc}")
            return
        sent += len(batch)
        if start + MENTION_BATCH_SIZE < len(names):
            await asyncio.sleep(MENTION_DELAY_SECONDS)

    skipped = len(members) - len(names)
    note = f"\n(بدون یوزرنیم: {_fa(skipped)} نفر قابل منشن نبودند)" if skipped else ""
    await message.answer(f"✅ {_fa(sent)} نفر منشن شدند.{note}")


@router.message(Command("mute", "timeout"))
async def cmd_mute(message: Message, command: CommandObject, db: Database) -> None:
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("این کامند فقط داخل گروه کار میکنه")
        return
    if not await _require_admin(message, db):
        await message.answer("فقط ادمین می تواند این کامند رو وارد کند")
        return
    if not await _is_bot_admin_safe(message):
        await message.answer("من ادمین این گروه نیستم! اول من رو ادمین کنید.")
        return

    target_arg, duration_arg = split_target_and_duration(command.args)
    duration = parse_duration(duration_arg)
    if duration is None:
        await message.answer(
            "❗️ فرمت درست:\n"
            "• ریپلای روی پیام کاربر: /mute 30m\n"
            "• با یوزرنیم: /mute @user 2h\n"
            "• با آیدی عددی: /mute 123456789 1d\n"
            "واحدها: m (دقیقه) — h (ساعت) — d (روز) — w (هفته)\n"
            "حد مجاز: از ۱۰ دقیقه تا ۳۰ روز"
        )
        return

    target = await _resolve_target(message, target_arg, db)
    if target is None:
        await message.answer("❗️ کاربر پیدا نشد. ریپلای کنید یا آیدی عددی بدهید.")
        return
    if target.id == (await message.bot.get_me()).id:
        await message.answer("😂 خودم رو نمی‌تونم mute کنم!")
        return

    try:
        await message.bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
            permissions=_perms(False),
            until_date=message.date + duration,
        )
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        await message.answer(f"❌ موفق نشدم: {e}")
        return
    await message.answer(
        f"🔇 {target.full_name} تا {_fmt_delta(duration)} دیگر ساکت شد."
    )


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, command: CommandObject, db: Database) -> None:
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("این کامند فقط داخل گروه کار میکنه")
        return
    if not await _require_admin(message, db):
        await message.answer("فقط ادمین میتواند این کامند را وارد کند")
        return
    if not await _is_bot_admin_safe(message):
        await message.answer("من ادمین این گروه نیستم!")
        return
    target_arg, _ = split_target_and_duration(command.args)
    target = await _resolve_target(message, target_arg, db)
    if target is None:
        await message.answer("❗️ کاربر پیدا نشد. ریپلای کنید یا آیدی عددی بدهید.")
        return
    try:
        await message.bot.restrict_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
            permissions=_perms(True),
        )
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        await message.answer(f"❌ موفق نشدم: {e}")
        return
    await message.answer(f"🔊 {target.full_name} از حالت سکوت خارج شد.")


@router.message(Command("ban"))
async def cmd_ban(message: Message, command: CommandObject, db: Database) -> None:
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("این کامند فقط داخل گروه کار میکنه")
        return
    if not await _require_admin(message, db):
        await message.answer("فقط ادمین میتواند این کامند را وارد کند")
        return
    if not await _is_bot_admin_safe(message):
        await message.answer("من ادمین این گروه نیستم! اول من رو ادمین کنید.")
        return
    target_arg, _ = split_target_and_duration(command.args)
    target = await _resolve_target(message, target_arg, db)
    if target is None:
        await message.answer("❗️ کاربر پیدا نشد. ریپلای کنید یا آیدی عددی بدهید.")
        return
    if target.id == (await message.bot.get_me()).id:
        await message.answer("😂 اخراج خودم ممکن نیست!")
        return
    try:
        await message.bot.ban_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
            until_date=message.date + timedelta(days=366), 
        )
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        await message.answer(f"❌ موفق نشدم: {e}")
        return
    await message.answer(f"🔨 {target.full_name} از گروه اخراج شد.")


@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject, db: Database) -> None:
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.answer("این کامند فقط داخل گروه کار میکنه")
        return
    if not await _require_admin(message, db):
        await message.answer("فقط ادمین میتواند این کامند را وارد کند")
        return
    if not await _is_bot_admin_safe(message):
        await message.answer("من ادمین این گروه نیستم!")
        return
    target_arg, _ = split_target_and_duration(command.args)
    target = await _resolve_target(message, target_arg, db)
    if target is None:
        await message.answer("❗️ کاربر پیدا نشد. آیدی عددی یا @username بدهید.")
        return
    try:
        await message.bot.unban_chat_member(
            chat_id=message.chat.id,
            user_id=target.id,
            only_if_banned=True,
        )
    except (TelegramForbiddenError, TelegramBadRequest) as e:
        await message.answer(f"❌ موفق نشدم: {e}")
        return
    await message.answer(f"✅ {target.full_name} رفع اخراج شد.")


@router.message()
async def deletion_switch_watcher(message: Message, db: Database) -> None:
    """
    سوییچ‌هایی که تلگرام برایشان مجوز گروهی ندارد: فوروارد و منشن همه.
    برای این دو، «جلوگیری واقعی» با Bot API ممکن نیست؛ پس پیام حذف می‌شود.
    قفل گروه و منع رسانه با مجوز واقعی در cmd_chat_permissions هندل شده‌اند.
    """
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if message.from_user is None:
        return

    block_forward = await db.get_chat_setting(message.chat.id, "block_forward", False)
    block_mentions = await db.get_chat_setting(message.chat.id, "block_mentions", False)
    if not block_forward and not block_mentions:
        return

    reason = None
    if block_forward and any(
        getattr(message, kind, None) is not None
        for kind in ("forward_origin", "forward_from")
    ):
        reason = "ارسال پیام فورواردشده در این گروه مجاز نیست"
    elif block_mentions:
        entities = (message.entities or []) + (message.caption_entities or [])
        has_mention = any(e.type in ("mention", "text_mention") for e in entities)
        if has_mention:
            reason = "منشن کردن در این گروه مجاز نیست"
    if reason is None:
        return

    # member = await message.chat.get_member(message.from_user.id)
    # if member.status in ("administrator", "creator"):
    #     return

    try:
        await message.delete()
    except (TelegramForbiddenError, TelegramBadRequest):
        return
    await message.answer(f"🚫 {reason}.")


@router.message(F.text | F.caption, ~F.text.startswith("/"))
async def anti_ad_watcher(message: Message, db: Database) -> None:

    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    t = message.text or message.caption or ""
    if not t:
        return
    if await db.get_chat_setting(message.chat.id, "anti_ad", True) is False:
        return
    if not looks_like_ad(t):
        return

    whitelist = await db.get_chat_setting(message.chat.id, "ad_whitelist", [])
    if whitelist and _whitelisted(t, whitelist):
        return

    member = await message.chat.get_member(message.from_user.id)
    # if member.status in ("administrator", "creator"):
    #     return

    try:
        await message.delete()
    except (TelegramForbiddenError, TelegramBadRequest):
        return 
    await message.answer(
        f"⚠️ پیام {message.from_user.full_name} حذف شد: ارسال تبلیغ ممنوعه."
    )
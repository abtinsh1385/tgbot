from __future__ import annotations
import re
from datetime import timedelta
from aiogram import Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatPermissions, Message, User
from database import Database
from aiogram import F
router = Router(name="mod")

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
        try:
            member = await chat.get_member(int(args))
        except (TelegramForbiddenError, TelegramBadRequest):
            return None
        return member.user

    if args.startswith("@"):
        user = await db.get_user_by_username(args[1:])
        if user is None:
            return None
        try:
            member = await chat.get_member(user["user_id"])
        except (TelegramForbiddenError, TelegramBadRequest):
            return None
        return member.user

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
    return ChatPermissions(
        can_send_messages=allowed,
        can_send_audios=allowed,
        can_send_documents=allowed,
        can_send_photos=allowed,
        can_send_videos=allowed,
        can_send_video_notes=allowed,
        can_send_voice_notes=allowed,
        can_send_polls=allowed,
        can_send_other_messages=allowed,
        can_add_web_page_previews=allowed,
    )


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


@router.message(F.text,~F.text.startswith("/"))
async def anti_ad_watcher(message: Message, db: Database) -> None:

    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if not message.text and not message.caption:
        return     
    if await db.get_chat_setting(message.chat.id, "anti_ad", True) is False:
        return
    t=message.text or message.caption or ""
    if not looks_like_ad(t):
        return 
    
    whitlist=await db.get_chat_setting(message.chat.id,"ad_whitelist",[])
    if whitlist and _whitelisted(message.text,whitlist):
        return 

    member = await message.chat.get_member(message.from_user.id)
    if member.status in ("administrator", "creator"):
        return

    try:
        await message.delete()
    except (TelegramForbiddenError, TelegramBadRequest):
        return 
    await message.answer(
        f"⚠️ پیام {message.from_user.full_name} حذف شد: ارسال تبلیغ ممنوعه."
    )
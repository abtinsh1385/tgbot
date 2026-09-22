from __future__ import annotations
import re
from aiogram import Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import Message
from database import Database

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
    

@router.message()
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
    
    whitelist=await db.get_chat_setting(message.chat.id,"ad_whitelist",[])
    if whitelist and _whitelisted(message.text,whitelist):
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
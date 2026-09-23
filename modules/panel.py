from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatMemberStatus, ChatType
import re

router = Router()

from config import PANEL_URL

# فقط رقم قبول می‌کنه (بدون علامت منفی، چون تلگرام "-" رو توی start param نمی‌پذیره)
_PANEL_ARGS_RE = re.compile(r"^panel_(\d+)_userid_(\d+)$")


async def is_group_admin(bot, chat_id: int, user_id: int) -> bool:
    member = await bot.get_chat_member(chat_id, user_id)
    return member.status in (ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR)


@router.message(Command("panel"))
async def open_panel(message: types.Message, bot):
    if message.chat.type == ChatType.PRIVATE:
        await message.answer("این کامند فقط داخل گروه کار می‌کنه.")
        return

    is_admin = await is_group_admin(bot, message.chat.id, message.from_user.id)
    if not is_admin:
        await message.answer("⛔ فقط ادمین‌ها دسترسی دارن.")
        return

    bot_info = await bot.get_me()

    # chat_id گروه‌ها همیشه منفیه؛ علامت منفی رو حذف می‌کنیم چون تلگرام قبول نمی‌کنه
    chat_id_digits = str(message.chat.id).lstrip("-")
    deep_link = (
        f"https://t.me/{bot_info.username}"
        f"?start=panel_{chat_id_digits}_userid_{message.from_user.id}"
    )
    print(deep_link)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ باز کردن پنل (در PV)", url=deep_link)]
    ])
    await message.answer("برای دسترسی به پنل تنظیمات روی دکمه بزن:", reply_markup=keyboard)

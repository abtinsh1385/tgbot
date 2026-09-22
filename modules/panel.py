from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ChatMemberStatus, ChatType

router = Router()

from config import PANEL_URL

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
    deep_link = f"https://t.me/{bot_info.username}?start=panel_{message.chat.id}"

    # این یک دکمه‌ی معمولی url هست، نه web_app -> در گروه مشکلی نداره
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ باز کردن پنل (در PV)", url=deep_link)]
    ])
    await message.answer("برای دسترسی به پنل تنظیمات روی دکمه بزن:", reply_markup=keyboard)


@router.message(Command("start"))
async def start_cmd(message: types.Message, command: CommandObject):
    args = command.args  # مثلاً "panel_-1003962094989"
    if args and args.startswith("panel_"):
        chat_id = args.split("_", 1)[1]
        url = f"{PANEL_URL}/{chat_id}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ باز کردن پنل تنظیمات", web_app=WebAppInfo(url=url))]
        ])
        await message.answer("پنل تنظیمات گروهت:", reply_markup=keyboard)
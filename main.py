import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from config import TOKEN
from database import Database, InsufficientFunds, UnknownUser
from modules import mod, panel
from modules.panel import is_group_admin, _PANEL_ARGS_RE
from config import PANEL_URL
from aiogram.filters import CommandObject
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
import re


logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
bot = Bot(token=TOKEN)
dp = Dispatcher()
db = Database()

dp.include_router(mod.router)
dp.include_router(panel.router)

@dp.message(Command("start"))
async def cmd_start(message: types.Message, command: CommandObject):
    await db.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    await db.ensure_chat(
        message.chat.id, message.chat.type, message.chat.title
    )

    args = command.args
    if args:
        match = _PANEL_ARGS_RE.match(args)
        if match:
            chat_id = -int(match.group(1))
            origin_user_id = int(match.group(2))
            clicker_id = message.from_user.id

            if clicker_id != origin_user_id:
                await message.answer("⛔ این لینک مخصوص شما نیست.")
                return

            is_admin = await is_group_admin(bot, chat_id, clicker_id)
            if not is_admin:
                await message.answer("⛔ فقط ادمین‌های این گروه می‌توانند پنل را باز کنند.")
                return

            url = f"{PANEL_URL}?chat_id={chat_id}"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="⚙️ باز کردن پنل تنظیمات", web_app=WebAppInfo(url=url))]
            ])
            await message.answer("پنل تنظیمات گروهت:", reply_markup=keyboard)
            return

    await message.answer("سلام! من فعال هستم ✅")


async def main():
    await db.connect()        
    try:
        await dp.start_polling(bot, db=db)
    finally:
        await db.close()      

if __name__ == "__main__":
    asyncio.run(main())

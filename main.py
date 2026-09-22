import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from config import TOKEN
from database import Database, InsufficientFunds, UnknownUser
from modules import mod, panel
from aiogram.filters import CommandObject


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
    if args and args.startswith("panel_"):
        chat_id = args.split("_", 1)[1]
        from config import PANEL_URL
        from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
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

import asyncio
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

from config import TOKEN
from database import Database, InsufficientFunds, UnknownUser

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

bot = Bot(token=TOKEN)
dp = Dispatcher()
db = Database()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await db.ensure_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.first_name,
    )
    await db.ensure_chat(
        message.chat.id, message.chat.type, message.chat.title
    )
    await message.answer("سلام! من فعال هستم ✅")


async def main():
    await db.connect()          # open DB before polling starts
    try:
        await dp.start_polling(bot)
    finally:
        await db.close()        # always close on shutdown (incl. Ctrl+C)

if __name__ == "__main__":
    asyncio.run(main())

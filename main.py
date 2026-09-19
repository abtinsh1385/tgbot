import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

TOKEN = "8362879362:AAHhgyVJL5jzbiDYHPE8TEGDe4C1lmgf1ks"

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("سلام! من فعال هستم ✅")

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
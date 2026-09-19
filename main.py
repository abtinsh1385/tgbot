import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram import F
from modules import mod

TOKEN = "8362879362:AAHhgyVJL5jzbiDYHPE8TEGDe4C1lmgf1ks"

bot = Bot(token=TOKEN)
dp = Dispatcher()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer("سلام! من فعال هستم ✅")

@dp.message(F.text)
async def check_message(message: types.Message):
    text = message.text
    if mod.is_ad(text):
        try:
            await message.delete()
            await bot.restrict_chat_member(
                chat_id=message.chat.id,
                user_id=message.from_user.id,
                permissions=types.ChatPermissions(can_send_messages=False)
            )
        except Exception:
            pass
        finally:
            await message.answer(f"پیام {message.from_user.full_name} به دلیل تبلیغ حذف شد.")

async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
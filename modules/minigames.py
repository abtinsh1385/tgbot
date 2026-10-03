from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from config import GAMEMENU_URL  # یا یک متغیر جدا مثل GAMEMENU_URL

router = Router(name="minigames")

@router.message(Command("play"))
async def open_gamemenu(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 منوی بازی‌ها", web_app=WebAppInfo(url=GAMEMENU_URL))]
    ])
    await message.answer("منوی بازی‌ها:", reply_markup=keyboard)
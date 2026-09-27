from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from config import PANEL_URL  # یا یک متغیر جدا مثل GAMEMENU_URL

GAMEMENU_URL = PANEL_URL.replace("/panel", "/gamemenu")  # یا یک مقدار مستقل در .env
router = Router(name="minigames")

@router.message(Command("play"))
async def open_gamemenu(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 منوی بازی‌ها", web_app=WebAppInfo(url=GAMEMENU_URL))]
    ])
    await message.answer("منوی بازی‌ها:", reply_markup=keyboard)
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import sys, os, io
from aiogram import Bot
from config import TOKEN

from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest


sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import Database

app = FastAPI()
db = Database()
bot = Bot(token=TOKEN)


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def startup():
    await db.connect()


@app.on_event("shutdown")
async def shutdown():
    await db.close()


@app.get("/game")
async def serve_game():
    return FileResponse(os.path.join(STATIC_DIR, "gamemenu.html"))


@app.get("/")
async def mainpage():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/balance/{user_id}")
async def balance_endpoint(user_id: int):
    bal = await db.get_balance(user_id)
    return {"balance": bal}


@app.get("/panel")
async def serve_panel(chat_id: int):
    chat = await bot.get_chat(chat_id)
    # return {"chat id": chat_id, "name":chat.title}
    return FileResponse(os.path.join(STATIC_DIR, "panel.html"))

@app.get("/api/chat-info/{chat_id}")
async def get_chat_info(chat_id: int):
    try:
        chat = await bot.get_chat(chat_id)
    except (TelegramForbiddenError, TelegramBadRequest):
        return {"error": "بات به این گروه دسترسی نداره"}

    return {
        "title": chat.title,
        "has_photo": chat.photo is not None,
    }

@app.get("/api/chat-photo/{chat_id}")
async def get_chat_photo(chat_id: int):
    try:
        chat = await bot.get_chat(chat_id)
    except (TelegramForbiddenError, TelegramBadRequest):
        return {"error": "بات به این گروه دسترسی نداره"}

    if not chat.photo:
        return {"error": "no photo"}

    file_bytes = await bot.download(chat.photo.big_file_id)
    return StreamingResponse(io.BytesIO(file_bytes.read()), media_type="image/jpeg")

@app.get("/api/settings/{chat_id}")
async def get_settings_api(chat_id: int):
    return await db.get_chat_settings(chat_id)


@app.get("/api/settings/{chat_id}")
async def get_settings_api(chat_id: int):
    return await db.get_chat_settings(chat_id)


@app.post("/api/settings/{chat_id}/toggle_ad")
async def toggle_ad_api(chat_id: int):
    new_status = await db.toggle_anti_ad(chat_id)
    return {"anti_ad_enabled": new_status}
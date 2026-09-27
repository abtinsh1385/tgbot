import hashlib
import hmac
from permissions import for_chat
from modules.mod import _exempt_admins
from urllib.parse import parse_qsl
from fastapi import FastAPI, Request, Header, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import sys, os, io
from aiogram import Bot
from config import TOKEN

from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest

from webapp.auth import verify_init_data
from webapp.games import build_registry

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database import Database

app = FastAPI()
db = Database()
bot = Bot(token=TOKEN)
games = {}


STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

def verify_telegram_data(init_data: str) -> dict:
    parsed = dict(parse_qsl(init_data))
    hash_received = parsed.pop('hash', None)
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if computed_hash != hash_received:
        raise HTTPException(status_code=401, detail="Invalid Telegram data")
    return parsed


async def verify_admin(chat_id: int, init_data: str) -> int:
    import json
    parsed = verify_telegram_data(init_data)
    user = json.loads(parsed["user"])
    user_id = user["id"]

    member = await bot.get_chat_member(chat_id, user_id)
    if member.status not in ("creator", "administrator"):
        raise HTTPException(status_code=403, detail="Not an admin")
    return user_id



@app.on_event("startup")
async def startup():
    await db.connect()
    games.update(build_registry(db)) 


@app.on_event("shutdown")
async def shutdown():
    await db.close()


@app.get("/gamemenu")
async def serve_game():
    return FileResponse(os.path.join(STATIC_DIR, "gamemenu.html"))

@app.get("/games/{slug}")
async def game_page(slug: str):
    game = games.get(slug)
    if not game:
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(os.path.join(STATIC_DIR, "games", game.html_file))

@app.get("/api/games")
async def list_games():
    return [{"slug": g.slug, "name": g.name, "emoji": g.emoji} for g in games.values()]


@app.post("/api/games/{slug}/play")
async def play_game(slug: str, payload: dict = Body(...)):
    game = games.get(slug)
    if not game:
        raise HTTPException(status_code=404, detail="بازی پیدا نشد")

    init_data = payload.pop("initData", "")
    user = verify_init_data(init_data)

    return await game.play(user["id"], payload)

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

@app.get("/api/chat-users/{chat_id}")
async def get_chat_users(chat_id: int):
    users = await db.get_users_in_chat(chat_id)

    return [
        {
            "user_id": user["user_id"],
            "username": user["username"],
            "first_name": user["first_name"],
            "last_seen_at": user["last_seen_at"],
        }
        for user in users
    ]

@app.get("/api/settings/{chat_id}")
async def get_settings_api(chat_id: int):
    anti_ad = await db.get_chat_setting(chat_id, "anti_ad", True)
    chat_locked = await db.get_chat_setting(chat_id, "chat_locked", False)
    block_media = await db.get_chat_setting(chat_id, "block_media", False)
    block_forward = await db.get_chat_setting(chat_id, "block_forward", False)
    block_mentions = await db.get_chat_setting(chat_id, "block_mentions", False)
    return {"anti_ad": anti_ad, "chat_locked": chat_locked, "block_media": block_media, "block_forward": block_forward, "block_mentions":block_mentions}


@app.post("/api/settings/{chat_id}/toggle_ad")
async def toggle_ad_api(chat_id: int, x_telegram_init_data: str = Header(...)):
    await verify_admin(chat_id, x_telegram_init_data)

    current = await db.get_chat_setting(chat_id, "anti_ad", True)
    new_value = not current
    await db.set_chat_setting(chat_id, "anti_ad", new_value)
    return {"anti_ad": new_value}

@app.post("/api/settings/{chat_id}/toggle_lock")
async def toggle_lock_api(
    chat_id: int,
    x_telegram_init_data: str = Header(...)
):
    await verify_admin(chat_id, x_telegram_init_data)

    current = await db.get_chat_setting(chat_id, "chat_locked", False)
    new_value = not current

    block_media = await db.get_chat_setting(
        chat_id, "block_media", False
    )

    try:
        await bot.set_chat_permissions(
            chat_id=chat_id,
            permissions=for_chat(new_value, block_media)
        )

        # اگر lock روشن شد، ادمین‌ها همچنان بتوانند پیام بدهند
        if new_value:
            await _exempt_admins(bot, chat_id)

    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"تغییر مجوزهای گروه ممکن نشد: {exc}"
        )

    await db.set_chat_setting(
        chat_id,
        "chat_locked",
        new_value
    )

    if new_value:
        await bot.send_message(
            chat_id,
            "🔒 گروه قفل است — فقط مدیران می‌توانند پیام بدهند."
        )
    else:
        await bot.send_message(
            chat_id,
            "🔓 قفل گروه برداشته شد."
        )

    return {"chat_locked": new_value}

@app.post("/api/settings/{chat_id}/toggle_media")
async def toggle_media_api(chat_id: int, x_telegram_init_data: str = Header(...)):
    await verify_admin(chat_id, x_telegram_init_data)

    current = await db.get_chat_setting(chat_id, "block_media", True)
    new_value = not current
    await db.set_chat_setting(chat_id, "block_media", new_value)

    if new_value:
        await bot.send_message(
            chat_id,
            "📎 ارسال رسانه بسته است"
        )
    else:
        await bot.send_message(
            chat_id,
            "منع رسانه برداشته شد."
        )
    return {"block_media": new_value}

@app.post("/api/settings/{chat_id}/toggle_forward")
async def toggle_forward_api(chat_id: int, x_telegram_init_data: str = Header(...)):
    await verify_admin(chat_id, x_telegram_init_data)

    current = await db.get_chat_setting(chat_id, "block_forward", True)
    new_value = not current
    await db.set_chat_setting(chat_id, "block_forward", new_value)
    if new_value:
        await bot.send_message(
            chat_id,
            "🔁 منع فوروارد"
        )
    else:
        await bot.send_message(
            chat_id,
            "منع فوروارد برداشته شد."
        )    
    return {"block_forward": new_value}

@app.post("/api/settings/{chat_id}/toggle_mention")
async def toggle_ad_mention(chat_id: int, x_telegram_init_data: str = Header(...)):
    await verify_admin(chat_id, x_telegram_init_data)

    current = await db.get_chat_setting(chat_id, "block_mentions", True)
    new_value = not current
    await db.set_chat_setting(chat_id, "block_mentions", new_value)
    if new_value:
        await bot.send_message(
            chat_id,
            "📣 منع منشن"
        )
    else:
        await bot.send_message(
            chat_id,
            "منع منشن برداشته شد."
        )
    return {"block_mentions": new_value}
@app.get("/gamemenu")
async def gamemenu_api():
    return FileResponse(os.path.join(STATIC_DIR, "gamemenu.html"))


@app.get("/api/user-info/{user_id}")
async def get_user_info(user_id: int):
    try:
        chat = await bot.get_chat(user_id)  # برای چت خصوصی، chat_id == user_id
    except (TelegramForbiddenError, TelegramBadRequest):
        return {"error": "کاربر پیدا نشد یا بات به او دسترسی نداره"}

    balance = await db.get_balance(user_id)

    name = chat.first_name or chat.username or "کاربر"
    if chat.last_name:
        name += f" {chat.last_name}"

    return {
        "name": name,
        "username": chat.username,
        "balance": balance,
    }


@app.get("/api/user-photo/{user_id}")
async def get_user_photo(user_id: int):
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
    except (TelegramForbiddenError, TelegramBadRequest):
        return {"error": "دسترسی به عکس کاربر ممکن نیست"}

    if photos.total_count == 0:
        return {"error": "no photo"}

    # بزرگترین سایز عکس (آخرین آیتم لیست sizes)
    biggest = photos.photos[0][-1]
    file_bytes = await bot.download(biggest.file_id)
    return StreamingResponse(io.BytesIO(file_bytes.read()), media_type="image/jpeg")

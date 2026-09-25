from __future__ import annotations

from datetime import datetime, timezone

from aiogram import Router, types, F
from aiogram.filters import Command, CommandObject

from database import Database, InsufficientFunds, UnknownUser

router = Router(name="economy")

DAILY_AMOUNT = 100
DAILY_COOLDOWN_HOURS = 24


def _today_key() -> str:
    """کلید روز جاری بر اساس UTC، برای مقایسه‌ی ساده‌ی روزها."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _ts_to_day(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


@router.message(Command("balance"))
async def balance_cmd(message: types.Message, db: Database) -> None:
    await db.ensure_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    bal = await db.get_balance(message.from_user.id)
    await message.answer(f"موجودی شما: {bal} سکه 💰")


@router.message(Command("daily"))
async def daily_cmd(message: types.Message, db: Database) -> None:
    user_id = message.from_user.id

    # آخرین تراکنش با reason='daily' رو پیدا کن
    history = await db.get_transaction_history(user_id, limit=50)
    last_daily = next((tx for tx in history if tx["reason"] == "daily"), None)

    if last_daily and _ts_to_day(last_daily["created_at"]) == _today_key():
        await message.answer("امروز قبلاً جایزه‌ی روزانه رو گرفتی. فردا دوباره بیا 🙂")
        return

    new_balance = await db.add_balance(user_id, DAILY_AMOUNT, reason="daily")
    await message.answer(
        f"🎁 {DAILY_AMOUNT} سکه جایزه‌ی روزانه گرفتی!\nموجودی جدید: {new_balance} سکه"
    )


@router.message(Command("pay"))
async def pay_cmd(message: types.Message, command: CommandObject, db: Database) -> None:
    """
    استفاده: با ریپلای روی پیام کسی بزن /pay 100
    یا: /pay @username 100  (اگه یوزرنیم قبلاً در دیتابیس ثبت شده باشه)
    """
    if not command.args:
        await message.answer("استفاده: روی پیام کسی ریپلای کن و بنویس /pay <مقدار>")
        return

    parts = command.args.split()

    if message.reply_to_message:
        target_id = message.reply_to_message.from_user.id
        try:
            amount = int(parts[0])
        except (IndexError, ValueError):
            await message.answer("مقدار سکه رو درست وارد کن. مثال: /pay 100")
            return
    else:
        if len(parts) < 2:
            await message.answer("استفاده: /pay @username 100")
            return
        username = parts[0].lstrip("@")
        target = await db.get_user_by_username(username)
        if not target:
            await message.answer("کاربری با این یوزرنیم پیدا نشد.")
            return
        target_id = target["user_id"]
        try:
            amount = int(parts[1])
        except ValueError:
            await message.answer("مقدار سکه رو درست وارد کن.")
            return

    if amount <= 0:
        await message.answer("مقدار باید مثبت باشه.")
        return

    if target_id == message.from_user.id:
        await message.answer("نمی‌تونی به خودت سکه بفرستی 😄")
        return

    try:
        sender_bal, receiver_bal = await db.transfer(
            message.from_user.id, target_id, amount, reason="transfer"
        )
    except InsufficientFunds:
        await message.answer("موجودی کافی نداری.")
        return
    except UnknownUser:
        await message.answer("این کاربر هنوز با بات تعامل نداشته.")
        return

    await message.answer(
        f"✅ {amount} سکه فرستاده شد.\nموجودی جدید شما: {sender_bal} سکه"
    )


@router.message(Command("top"))
async def leaderboard_cmd(message: types.Message, db: Database) -> None:
    rows = await db.get_leaderboard(limit=10)
    if not rows:
        await message.answer("هنوز کسی سکه‌ای نداره.")
        return

    lines = []
    for i, row in enumerate(rows, start=1):
        name = row["username"] or row["first_name"] or "کاربر"
        lines.append(f"{i}. {name} — {row['balance']} سکه")

    await message.answer("🏆 جدول برترین‌ها (سراسری):\n" + "\n".join(lines))


@router.message(Command("history"))
async def history_cmd(message: types.Message, db: Database) -> None:
    rows = await db.get_transaction_history(message.from_user.id, limit=10)
    if not rows:
        await message.answer("هنوز هیچ تراکنشی نداری.")
        return

    lines = []
    for tx in rows:
        sign = "+" if tx["amount"] > 0 else ""
        lines.append(f"{sign}{tx['amount']} ({tx['reason']}) → موجودی: {tx['balance_after']}")

    await message.answer("📜 آخرین تراکنش‌های شما:\n" + "\n".join(lines))
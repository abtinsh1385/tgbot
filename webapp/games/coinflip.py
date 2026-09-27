import random
from fastapi import HTTPException
from .base import BaseGame

MIN_BET = 10
MAX_BET = 1000


class CoinflipGame(BaseGame):
    slug = "coinflip"
    name = "شیر یا خط"
    emoji = "🪙"
    html_file = "coinflip.html"

    async def play(self, user_id: int, payload: dict) -> dict:
        choice = payload.get("choice")
        amount = payload.get("amount")

        if choice not in ("heads", "tails"):
            raise HTTPException(status_code=400, detail="انتخاب باید شیر یا خط باشه")

        try:
            amount = int(amount)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="مقدار شرط نامعتبره")

        if not (MIN_BET <= amount <= MAX_BET):
            raise HTTPException(
                status_code=400,
                detail=f"شرط باید بین {MIN_BET} تا {MAX_BET} سکه باشه",
            )

        balance = await self.db.get_balance(user_id)
        if balance < amount:
            raise HTTPException(status_code=400, detail="موجودی کافی نیست")

        # --- منطق اصلی: کاملاً سمت سرور، غیرقابل دستکاری از کلاینت ---
        result = random.choice(["heads", "tails"])
        won = result == choice
        delta = amount if won else -amount

        new_balance = await self.db.add_balance(user_id, delta, reason="coinflip")
        await self.db.record_minigame(user_id, self.slug, won=won, delta=delta)

        return {
            "result": result,
            "won": won,
            "delta": delta,
            "balance": new_balance,
        }
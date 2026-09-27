import random
from fastapi import HTTPException
from .base import BaseGame
from .sessions import set_session, get_session, clear_session

MIN_BET = 10
MAX_BET = 1000

RANKS = ["2", "3", "4", "5", "6", "7", "8", "9", "10"]


def draw_card() -> str:
    return random.choice(RANKS)


def card_value(card: str) -> int:
    return int(card)


def hand_value(cards: list[str]) -> int:
    total = sum(card_value(c) for c in cards)
    aces = cards.count("A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


class BlackjackGame(BaseGame):
    slug = "blackjack"
    name = "بلک‌جک"
    emoji = "🎴"
    html_file = "blackjack.html"

    async def play(self, user_id: int, payload: dict) -> dict:
        action = payload.get("action")

        if action == "start":
            return await self._start(user_id, payload)
        elif action == "hit":
            return await self._hit(user_id)
        elif action == "stand":
            return await self._stand(user_id)

        raise HTTPException(status_code=400, detail="action نامعتبره")

    # ---------------------------------------------------------------

    async def _start(self, user_id: int, payload: dict) -> dict:
        if get_session(user_id):
            raise HTTPException(status_code=400, detail="یک بازی نیمه‌کاره داری، اول تمومش کن")

        amount = payload.get("amount")
        try:
            amount = int(amount)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="مقدار شرط نامعتبره")

        if not (MIN_BET <= amount <= MAX_BET):
            raise HTTPException(status_code=400, detail=f"شرط باید بین {MIN_BET} تا {MAX_BET} باشه")

        balance = await self.db.get_balance(user_id)
        if balance < amount:
            raise HTTPException(status_code=400, detail="موجودی کافی نیست")

        player = [draw_card(), draw_card()]
        dealer = [draw_card(), draw_card()]
        set_session(user_id, {"amount": amount, "player": player, "dealer": dealer})

        player_val = hand_value(player)
        dealer_val = hand_value(dealer)

        # بلک‌جک طبیعی (۲۱ با دو کارت اول)
        if player_val == 21 or dealer_val == 21:
            return await self._resolve(user_id, natural=True)

        return {
            "state": "playing",
            "player": player,
            "player_value": player_val,
            "dealer_visible": dealer[0],
            "balance": balance,
        }

    async def _hit(self, user_id: int) -> dict:
        session = get_session(user_id)
        if not session:
            raise HTTPException(status_code=400, detail="بازی فعالی نداری")

        session["player"].append(draw_card())
        set_session(user_id, session)
        player_val = hand_value(session["player"])

        if player_val > 21:
            return await self._resolve(user_id, bust=True)

        print("HIT:", user_id, session["player"], session["dealer"])
        

        return {
            "state": "playing",
            "player": session["player"],
            "player_value": player_val,
            "dealer_visible": session["dealer"][0],
            "balance": await self.db.get_balance(user_id),
        }

    async def _stand(self, user_id: int) -> dict:
        if not get_session(user_id):
            raise HTTPException(status_code=400, detail="بازی فعالی نداری")
        return await self._resolve(user_id)

    # ---------------------------------------------------------------

    async def _resolve(self, user_id: int, *, natural: bool = False, bust: bool = False) -> dict:
        session = get_session(user_id)
        amount = session["amount"]
        player = session["player"]
        dealer = session["dealer"]

        if not bust:
            while hand_value(dealer) < 17:
                dealer.append(draw_card())

        player_val = hand_value(player)
        dealer_val = hand_value(dealer)

        if bust:
            outcome, delta = "lose", -amount
        elif natural:
            if player_val == 21 and dealer_val == 21:
                outcome, delta = "push", 0
            elif player_val == 21:
                outcome, delta = "blackjack", int(amount * 1.5)
            else:
                outcome, delta = "lose", -amount
        elif dealer_val > 21 or player_val > dealer_val:
            outcome, delta = "win", amount
        elif player_val < dealer_val:
            outcome, delta = "lose", -amount
        else:
            outcome, delta = "push", 0

        clear_session(user_id)

        if delta != 0:
            new_balance = await self.db.add_balance(user_id, delta, reason="blackjack")
        else:
            new_balance = await self.db.get_balance(user_id)

        await self.db.record_minigame(user_id, self.slug, won=(delta > 0), delta=delta)

        return {
            "state": "finished",
            "outcome": outcome,
            "player": player,
            "player_value": player_val,
            "dealer": dealer,
            "dealer_value": dealer_val,
            "delta": delta,
            "balance": new_balance,
        }
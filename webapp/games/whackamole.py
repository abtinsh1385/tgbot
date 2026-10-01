from __future__ import annotations

import random
import time
from fastapi import HTTPException

from .base import BaseGame
from .sessions import set_session, get_session, clear_session

ENTRY_COST = 25
HIT_REWARD = 5
MISS_PENALTY = 2
GAME_DURATION = 30.0  # ثانیه
HOLES = 9             # شبکه‌ی 3x3
HIT_GRACE = 0.15


def _generate_schedule(duration: float = GAME_DURATION) -> list[dict]:
    """
    برنامه‌ی کامل ظاهر شدن موش‌ها رو از قبل می‌سازه. هرچی زمان جلوتر بره،
    فاصله‌ی بین ظاهرشدن‌ها کمتر و مدت دیده‌شدن هر موش کوتاه‌تر می‌شه (سخت‌تر).
    """
    events: list[dict] = []
    t = 0.6
    event_id = 0
    while t < duration:
        ratio = t / duration
        interval = max(0.35, 0.9 - ratio * 0.55)
        visible = max(0.38, 0.8 - ratio * 0.45)

        n_moles = 1
        if ratio > 0.55 and random.random() < 0.3:
            n_moles = 2  # مرحله‌ی سخت‌تر: گاهی دو موش همزمان

        holes = random.sample(range(HOLES), k=n_moles)
        for h in holes:
            events.append({
                "id": event_id,
                "hole": h,
                "spawn": round(t, 3),
                "hide": round(t + visible, 3),
                "hit": False,
            })
            event_id += 1

        t += interval + random.uniform(-0.05, 0.05)
    return events


class WhackAMoleGame(BaseGame):
    slug = "whackamole"
    name = "بزن بزن"
    emoji = "🔨"
    html_file = "whackamole.html"

    def _key(self, user_id: int) -> tuple:
        return (self.slug, user_id)

    async def play(self, user_id: int, payload: dict) -> dict:
        action = payload.get("action")
        if action == "start":
            return await self._start(user_id)
        elif action == "state":
            return self._state(user_id)
        elif action == "hit":
            return await self._hit(user_id, payload)
        elif action == "finish":
            return await self._finish(user_id)
        raise HTTPException(status_code=400, detail="action نامعتبره")

    # ---------------------------------------------------------------

    async def _start(self, user_id: int) -> dict:
        if get_session(self._key(user_id)):
            raise HTTPException(status_code=400, detail="یک بازی نیمه‌کاره داری، اول تمومش کن")

        balance = await self.db.get_balance(user_id)
        if balance < ENTRY_COST:
            raise HTTPException(status_code=400, detail="موجودی کافی نیست")

        new_balance = await self.db.add_balance(user_id, -ENTRY_COST, reason="whackamole_entry")

        session = {
            "schedule": _generate_schedule(),
            "start_time": time.time(),
            "hits": 0,
            "misses": 0,
        }
        set_session(self._key(user_id), session)

        return {
            "state": "playing",
            "holes": HOLES,
            "duration": GAME_DURATION,
            "balance": new_balance,
        }

    def _elapsed(self, session: dict) -> float:
        return time.time() - session["start_time"]

    def _state(self, user_id: int) -> dict:
        session = get_session(self._key(user_id))
        if not session:
            raise HTTPException(status_code=400, detail="بازی فعالی نداری")

        elapsed = self._elapsed(session)
        if elapsed >= GAME_DURATION:
            return {"state": "finished", "elapsed": elapsed}

        active = [
            {"id": e["id"], "hole": e["hole"]}
            for e in session["schedule"]
            if e["spawn"] <= elapsed < e["hide"] and not e["hit"]
        ]
        return {
            "state": "playing",
            "active": active,
            "elapsed": round(elapsed, 2),
            "hits": session["hits"],
            "misses": session["misses"],
        }

    async def _hit(self, user_id: int, payload: dict) -> dict:
        session = get_session(self._key(user_id))
        if not session:
            raise HTTPException(status_code=400, detail="بازی فعالی نداری")

        try:
            hole = int(payload.get("hole"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="سوراخ نامعتبره")

        elapsed = self._elapsed(session)
        if elapsed >= GAME_DURATION:
            return await self._finish(user_id)

        target = next(
            (
                e for e in session["schedule"]
                if e["hole"] == hole
                and e["spawn"] <= elapsed < e["hide"] + HIT_GRACE
                and not e["hit"]
            ),
            None,
        )

        if target is not None:
            target["hit"] = True
            session["hits"] += 1
            new_balance = await self._safe_delta(user_id, HIT_REWARD)
            result = "hit"
        else:
            session["misses"] += 1
            new_balance = await self._safe_delta(user_id, -MISS_PENALTY)
            result = "miss"

        set_session(self._key(user_id), session)
        return {
            "state": "playing",
            "result": result,
            "hits": session["hits"],
            "misses": session["misses"],
            "balance": new_balance,
        }

    async def _safe_delta(self, user_id: int, amount: int) -> int:
        """
        سکه اضافه/کم می‌کنه بدون اینکه موجودی هیچ‌وقت منفی بشه
        (چون جدول wallets اجازه‌ی مقدار منفی نمی‌ده).
        """
        if amount >= 0:
            return await self.db.add_balance(user_id, amount, reason="whackamole_hit")

        balance = await self.db.get_balance(user_id)
        real_delta = -min(abs(amount), balance)
        if real_delta == 0:
            return balance
        return await self.db.add_balance(user_id, real_delta, reason="whackamole_miss")

    async def _finish(self, user_id: int) -> dict:
        session = get_session(self._key(user_id))
        if not session:
            raise HTTPException(status_code=400, detail="بازی فعالی نداری")

        hits = session["hits"]
        misses = session["misses"]
        clear_session(self._key(user_id))

        net = hits * HIT_REWARD - misses * MISS_PENALTY - ENTRY_COST
        won = hits > 0 and (hits * HIT_REWARD) > (misses * MISS_PENALTY)

        await self.db.record_minigame(user_id, self.slug, won=won, delta=net)
        balance = await self.db.get_balance(user_id)

        return {
            "state": "finished",
            "hits": hits,
            "misses": misses,
            "balance": balance,
        }
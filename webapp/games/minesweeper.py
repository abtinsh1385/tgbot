import random
from fastapi import HTTPException
from .base import BaseGame
from .sessions import set_session, get_session, clear_session

ROWS = 10
COLS = 18
MINES = 30
BET_AMOUNT = 25
WIN_REWARD = 500


def neighbors(r: int, c: int):
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nr, nc = r + dr, c + dc
            if 0 <= nr < ROWS and 0 <= nc < COLS:
                yield nr, nc


class MinesweeperGame(BaseGame):
    slug = "minesweeper"
    name = "مین‌یاب"
    emoji = "💣"
    html_file = "minesweeper.html"

    def _key(self, user_id: int) -> tuple:
        return (self.slug, user_id)

    async def play(self, user_id: int, payload: dict) -> dict:
        action = payload.get("action")
        if action == "start":
            return await self._start(user_id)
        elif action == "reveal":
            return await self._reveal(user_id, payload)
        elif action == "flag":
            return await self._flag(user_id, payload)
        raise HTTPException(status_code=400, detail="action نامعتبره")

    # ---------------------------------------------------------------

    async def _start(self, user_id: int) -> dict:
        if get_session(self._key(user_id)):
            raise HTTPException(status_code=400, detail="یک بازی نیمه‌کاره داری، اول تمومش کن")

        balance = await self.db.get_balance(user_id)
        if balance < BET_AMOUNT:
            raise HTTPException(status_code=400, detail="موجودی کافی نیست")

        new_balance = await self.db.add_balance(user_id, -BET_AMOUNT, reason="minesweeper_entry")

        session = {
            "mines": None,        # هنوز مشخص نشده؛ بعد از اولین reveal چیده می‌شه
            "revealed": set(),
            "flagged": set(),
        }
        set_session(self._key(user_id), session)

        return {
            "state": "playing",
            "rows": ROWS,
            "cols": COLS,
            "mines_total": MINES,
            "revealed": {},
            "flagged": [],
            "balance": new_balance,
        }

    def _place_mines(self, safe_cell: tuple) -> set:
        forbidden = {safe_cell} | set(neighbors(*safe_cell))
        candidates = [(r, c) for r in range(ROWS) for c in range(COLS) if (r, c) not in forbidden]
        return set(random.sample(candidates, MINES))

    def _count_adjacent(self, cell: tuple, mines: set) -> int:
        return sum(1 for n in neighbors(*cell) if n in mines)

    def _flood_fill(self, start: tuple, mines: set, revealed: set) -> None:
        stack = [start]
        while stack:
            cell = stack.pop()
            if cell in revealed:
                continue
            revealed.add(cell)
            if self._count_adjacent(cell, mines) == 0:
                for n in neighbors(*cell):
                    if n not in revealed and n not in mines:
                        stack.append(n)

    def _serialize_revealed(self, revealed: set, mines: set) -> dict:
        return {f"{r}_{c}": self._count_adjacent((r, c), mines) for r, c in revealed}

    # ---------------------------------------------------------------

    async def _reveal(self, user_id: int, payload: dict) -> dict:
        session = get_session(self._key(user_id))
        if not session:
            raise HTTPException(status_code=400, detail="بازی فعالی نداری")

        try:
            r, c = int(payload.get("row")), int(payload.get("col"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="مختصات نامعتبره")
        if not (0 <= r < ROWS and 0 <= c < COLS):
            raise HTTPException(status_code=400, detail="خارج از صفحه‌ست")

        cell = (r, c)
        if cell in session["flagged"]:
            raise HTTPException(status_code=400, detail="اول فلگ این خونه رو بردار")
        if cell in session["revealed"]:
            raise HTTPException(status_code=400, detail="این خونه قبلاً باز شده")

        if session["mines"] is None:
            session["mines"] = self._place_mines(cell)
        mines = session["mines"]

        if cell in mines:
            clear_session(self._key(user_id))
            balance = await self.db.get_balance(user_id)
            await self.db.record_minigame(user_id, self.slug, won=False, delta=-BET_AMOUNT)
            return {
                "state": "lost",
                "mines": [{"row": mr, "col": mc} for mr, mc in mines],
                "balance": balance,
            }

        self._flood_fill(cell, mines, session["revealed"])
        set_session(self._key(user_id), session)

        total_safe = ROWS * COLS - MINES
        if len(session["revealed"]) == total_safe:
            clear_session(self._key(user_id))
            new_balance = await self.db.add_balance(user_id, WIN_REWARD, reason="minesweeper_win")
            await self.db.record_minigame(user_id, self.slug, won=True, delta=WIN_REWARD - BET_AMOUNT)
            return {
                "state": "won",
                "revealed": self._serialize_revealed(session["revealed"], mines),
                "delta": WIN_REWARD,
                "balance": new_balance,
            }

        return {
            "state": "playing",
            "revealed": self._serialize_revealed(session["revealed"], mines),
            "flagged": [{"row": fr, "col": fc} for fr, fc in session["flagged"]],
            "balance": await self.db.get_balance(user_id),
        }

    async def _flag(self, user_id: int, payload: dict) -> dict:
        session = get_session(self._key(user_id))
        if not session:
            raise HTTPException(status_code=400, detail="بازی فعالی نداری")

        try:
            r, c = int(payload.get("row")), int(payload.get("col"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="مختصات نامعتبره")

        cell = (r, c)
        if cell in session["revealed"]:
            raise HTTPException(status_code=400, detail="این خونه قبلاً باز شده")

        if cell in session["flagged"]:
            session["flagged"].discard(cell)   # فلگ روشن بود -> خاموش کن
            is_flagged = False
        else:
            session["flagged"].add(cell)       # فلگ نبود -> روشن کن
            is_flagged = True

        set_session(self._key(user_id), session)

        return {
            "state": "playing",
            "flagged": [{"row": fr, "col": fc} for fr, fc in session["flagged"]],
            "revealed": self._serialize_revealed(session["revealed"], session["mines"] or set()),
            "cell_flagged": is_flagged,
        }
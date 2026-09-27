# webapp/games/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from database import Database


class BaseGame(ABC):
    """قرارداد مشترک همه‌ی minigame ها."""

    slug: str          # شناسه‌ی یکتا، مثلاً "coinflip"
    name: str          # اسم نمایشی، مثلاً "شیر یا خط"
    emoji: str = "🎮"
    html_file: str     # اسم فایل HTML در static/games/

    def __init__(self, db: Database):
        self.db = db

    @abstractmethod
    async def play(self, user_id: int, payload: dict) -> dict:
        """
        منطق اصلی بازی. ورودی: داده‌ی خام از کلاینت.
        خروجی: دیکشنری نتیجه که مستقیم به کلاینت JSON برمی‌گرده.
        """
        ...
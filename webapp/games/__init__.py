from .coinflip import CoinflipGame
from .blackjack import BlackjackGame
from .minesweeper import MinesweeperGame

GAME_CLASSES = [
    CoinflipGame,
    BlackjackGame,
    MinesweeperGame
]


def build_registry(db):
    return {cls.slug: cls(db) for cls in GAME_CLASSES}
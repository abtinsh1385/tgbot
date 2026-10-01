from .coinflip import CoinflipGame
from .blackjack import BlackjackGame
from .minesweeper import MinesweeperGame
from .whackamole import WhackAMoleGame

GAME_CLASSES = [
    CoinflipGame,
    BlackjackGame,
    MinesweeperGame,
    WhackAMoleGame
]


def build_registry(db):
    return {cls.slug: cls(db) for cls in GAME_CLASSES}
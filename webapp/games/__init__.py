from .coinflip import CoinflipGame
from .blackjack import BlackjackGame

GAME_CLASSES = [
    CoinflipGame,
    BlackjackGame
]


def build_registry(db):
    return {cls.slug: cls(db) for cls in GAME_CLASSES}
from .coinflip import CoinflipGame

GAME_CLASSES = [
    CoinflipGame,
]


def build_registry(db):
    return {cls.slug: cls(db) for cls in GAME_CLASSES}
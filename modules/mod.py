def is_ad(message: str) -> bool:
    if "تبلیغ" in message:
        return True
    return False
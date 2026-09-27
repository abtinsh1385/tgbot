import time

_sessions: dict[int, dict] = {}
SESSION_TIMEOUT = 600  # 10 دقیقه


def set_session(user_id: int, data: dict) -> None:
    data["_ts"] = time.time()
    _sessions[user_id] = data


def get_session(user_id: int) -> dict | None:
    session = _sessions.get(user_id)
    if session and time.time() - session["_ts"] > SESSION_TIMEOUT:
        del _sessions[user_id]
        return None
    return session


def clear_session(user_id: int) -> None:
    _sessions.pop(user_id, None)
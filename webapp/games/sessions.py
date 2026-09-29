import time

_sessions: dict[tuple, dict] = {}
SESSION_TIMEOUT = 900  # 15 دقیقه


def set_session(key: tuple, data: dict) -> None:
    data["_ts"] = time.time()
    _sessions[key] = data


def get_session(key: tuple) -> dict | None:
    session = _sessions.get(key)
    if session and time.time() - session["_ts"] > SESSION_TIMEOUT:
        del _sessions[key]
        return None
    return session


def clear_session(key: tuple) -> None:
    _sessions.pop(key, None)
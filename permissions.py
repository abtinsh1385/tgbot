"""
permissions.py — shared Telegram permission builders.

Both the bot (modules/mod.py) and the web panel (webapp/server.py) need to
express the same policy, so the logic lives here once and is imported from
both sides. Adding a new content type means editing this file only.

Two different Telegram APIs are involved:

* set_chat_permissions() — chat-wide. Applies to every member at once, and
  has NO time limit: it stays until someone changes it again.
* restrict_chat_member() — one user. Supports until_date, so it can expire
  (that is what /mute uses).

Telegram requires the bot to be an admin with "Manage Chat" rights for the
chat-wide calls, otherwise the API returns Forbidden.
"""

from __future__ import annotations

from aiogram.types import ChatPermissions

# Content that counts as "media": photos, videos, audio, files, stickers,
# GIFs. Polls and link previews are separate switches because they are text
# features many groups still want while media is off.
MEDIA_FIELDS = (
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_audios",
    "can_send_documents",
    "can_send_other_messages",  # stickers, GIFs, dice
)


def full_access() -> ChatPermissions:
    """همه‌چیز آزاد."""
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )


def nothing_allowed() -> ChatPermissions:
    """همه‌چیز بسته — فقط مدیران می‌توانند بنویسند."""
    return ChatPermissions(
        can_send_messages=False,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
    )


def text_only() -> ChatPermissions:
    """فقط متن و نظرسنجی؛ هر نوع رسانه ممنوع."""
    return ChatPermissions(
        can_send_messages=True,
        can_send_audios=False,
        can_send_documents=False,
        can_send_photos=False,
        can_send_videos=False,
        can_send_video_notes=False,
        can_send_voice_notes=False,
        can_send_polls=True,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
    )


def for_chat(chat_locked: bool = False, block_media: bool = False) -> ChatPermissions:
    """
    Chat-wide policy from the two boolean switches.

    Locking implies everything is blocked, so members simply cannot post.
    Media-only blocking keeps text and polls available.
    """
    if chat_locked:
        return nothing_allowed()
    if block_media:
        return text_only()
    return full_access()


def describe(chat_locked: bool, block_media: bool) -> str:
    """Short Persian summary of the current chat-wide policy."""
    if chat_locked:
        return "🔒 گروه قفل است — فقط مدیران می‌توانند پیام بدهند."
    if block_media:
        return "📎 ارسال رسانه بسته است — فقط متن و نظرسنجی مجاز است."
    return "✅ همه‌چیز آزاد است."

# Copyright (c) 2025 AshokShau
# Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
# Part of the TgMusicBot project. All rights reserved where applicable.

from collections import deque
from typing import Any, Optional, TypeAlias, Union
from html import escape
import re

from cachetools import TTLCache
from pytdbot import types

from TgMusic.core._dataclass import CachedTrack

chat_invite_cache = TTLCache(maxsize=1000, ttl=1000)

ChatMemberStatus: TypeAlias = Union[
    types.ChatMemberStatusCreator,
    types.ChatMemberStatusAdministrator,
    types.ChatMemberStatusMember,
    types.ChatMemberStatusRestricted,
    types.ChatMemberStatusLeft,
    types.ChatMemberStatusBanned,
]

ChatMemberStatusResult: TypeAlias = Union[ChatMemberStatus, types.Error]
user_status_cache: TTLCache[str, ChatMemberStatus] = TTLCache(maxsize=5000, ttl=1000)


class ChatCacher:
    def __init__(self):
        self.chat_cache: dict[int, dict[str, Any]] = {}

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """Sanitize text to prevent Telegram entity parsing issues.

        Escapes HTML characters and removes invalid characters.
        """
        if not text:
            return text or ""
        # Escape HTML characters
        text = escape(text)
        # Remove control characters
        text = re.sub(r"[\x00-\x1F\x7F]", "", text)
        # Truncate to Telegram message length limit
        return text[:4096]

    def add_song(self, chat_id: int, song: CachedTrack) -> CachedTrack:
        """Add a song to the chat queue with sanitized fields."""
        # Sanitize text fields and provide defaults for missing fields
        sanitized_song = CachedTrack(
            track_id=song.track_id,
            name=self._sanitize_text(song.name),
            url=self._sanitize_text(song.url),
            user=self._sanitize_text(song.user),
            platform=song.platform,
            duration=song.duration,
            is_video=song.is_video,
            file_path=song.file_path,
            loop=song.loop,
            artist=self._sanitize_text(getattr(song, 'artist', 'Unknown Artist')),
            thumbnail=self._sanitize_text(getattr(song, 'thumbnail', ''))
        )
        data = self.chat_cache.setdefault(chat_id, {"is_active": True, "queue": deque()})
        data["queue"].append(sanitized_song)
        return sanitized_song

    def get_upcoming_track(self, chat_id: int) -> Optional[CachedTrack]:
        queue = self.chat_cache.get(chat_id, {}).get("queue")
        return queue[1] if queue and len(queue) > 1 else None

    def get_playing_track(self, chat_id: int) -> Optional[CachedTrack]:
        queue = self.chat_cache.get(chat_id, {}).get("queue")
        return queue[0] if queue else None

    def remove_current_song(self, chat_id: int) -> Optional[CachedTrack]:
        queue = self.chat_cache.get(chat_id, {}).get("queue")
        return queue.popleft() if queue else None

    def is_active(self, chat_id: int) -> bool:
        return self.chat_cache.get(chat_id, {}).get("is_active", False)

    def set_active(self, chat_id: int, active: bool):
        data = self.chat_cache.setdefault(chat_id, {"is_active": active, "queue": deque()})
        data["is_active"] = active

    def clear_chat(self, chat_id: int):
        self.chat_cache.pop(chat_id, None)

    def get_queue_length(self, chat_id: int) -> int:
        return len(self.chat_cache.get(chat_id, {}).get("queue", deque()))

    def get_loop_count(self, chat_id: int) -> int:
        queue = self.chat_cache.get(chat_id, {}).get("queue", deque())
        return queue[0].loop if queue else 0

    def set_loop_count(self, chat_id: int, loop: int) -> bool:
        if queue := self.chat_cache.get(chat_id, {}).get("queue", deque()):
            queue[0].loop = loop
            return True
        return False

    def remove_track(self, chat_id: int, queue_index: int) -> bool:
        queue = self.chat_cache.get(chat_id, {}).get("queue")
        if queue and 0 <= queue_index < len(queue):
            queue_list = list(queue)
            queue_list.pop(queue_index)
            self.chat_cache[chat_id]["queue"] = deque(queue_list)
            return True
        return False

    def get_queue(self, chat_id: int) -> list[CachedTrack]:
        return list(self.chat_cache.get(chat_id, {}).get("queue", deque()))

    def get_active_chats(self) -> list[int]:
        return [
            chat_id for chat_id, data in self.chat_cache.items() if data["is_active"]
        ]


chat_cache = ChatCacher()

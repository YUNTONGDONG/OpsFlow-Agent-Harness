"""Short-term conversation storage used when routing follow-up requests."""

from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from .task_state import utc_now


class SessionMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: datetime = Field(default_factory=utc_now)


class SessionStore(Protocol):
    def recent(self, session_id: str, limit: int) -> list[SessionMessage]: ...

    def append(self, session_id: str, message: SessionMessage) -> None: ...


class InMemorySessionStore:
    def __init__(self, max_messages: int = 20) -> None:
        self._max_messages = max_messages
        self._sessions: dict[str, list[SessionMessage]] = {}

    def recent(self, session_id: str, limit: int) -> list[SessionMessage]:
        return [
            message.model_copy(deep=True)
            for message in self._sessions.get(session_id, [])[-limit:]
        ]

    def append(self, session_id: str, message: SessionMessage) -> None:
        messages = self._sessions.setdefault(session_id, [])
        messages.append(message.model_copy(deep=True))
        del messages[:-self._max_messages]


class RedisSessionClient(Protocol):
    def rpush(self, name: str, value: str) -> int: ...

    def lrange(self, name: str, start: int, end: int) -> list[str | bytes]: ...

    def ltrim(self, name: str, start: int, end: int) -> bool: ...

    def expire(self, name: str, seconds: int) -> bool: ...


class RedisSessionStore:
    def __init__(
        self,
        client: RedisSessionClient,
        *,
        key_prefix: str = "harness:session",
        max_messages: int = 20,
        ttl_seconds: int = 86_400,
    ) -> None:
        if max_messages <= 0 or ttl_seconds <= 0:
            raise ValueError("Session limits must be positive")
        self._client = client
        self._key_prefix = key_prefix
        self._max_messages = max_messages
        self._ttl_seconds = ttl_seconds

    def recent(self, session_id: str, limit: int) -> list[SessionMessage]:
        if limit <= 0:
            return []
        raw_messages = self._client.lrange(self._key(session_id), -limit, -1)
        messages = []
        for raw_message in raw_messages:
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode("utf-8")
            messages.append(SessionMessage.model_validate_json(raw_message))
        return messages

    def append(self, session_id: str, message: SessionMessage) -> None:
        key = self._key(session_id)
        self._client.rpush(key, message.model_dump_json())
        self._client.ltrim(key, -self._max_messages, -1)
        self._client.expire(key, self._ttl_seconds)

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}:{session_id}"

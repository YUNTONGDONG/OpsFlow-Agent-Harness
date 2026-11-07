"""Cross-session user preferences stored separately from conversation state."""

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .task_state import utc_now


class UserProfile(BaseModel):
    user_id: str = Field(min_length=1, max_length=200)
    time_zone: str = "UTC"
    output_language: str = "zh-CN"
    default_calendar: str | None = None
    default_task_list: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)


class UserProfileStore(Protocol):
    def get(self, user_id: str) -> UserProfile | None: ...

    def upsert(self, profile: UserProfile) -> None: ...


class InMemoryUserProfileStore:
    def __init__(self) -> None:
        self._profiles: dict[str, UserProfile] = {}

    def get(self, user_id: str) -> UserProfile | None:
        profile = self._profiles.get(user_id)
        return profile.model_copy(deep=True) if profile is not None else None

    def upsert(self, profile: UserProfile) -> None:
        profile.updated_at = utc_now()
        self._profiles[profile.user_id] = profile.model_copy(deep=True)


class RedisUserProfileStore:
    def __init__(
        self,
        client: Any,
        *,
        key_prefix: str = "harness:user",
        ttl_seconds: int = 2_592_000,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("User profile TTL must be positive")
        self._client = client
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    def get(self, user_id: str) -> UserProfile | None:
        raw = self._client.get(self._key(user_id))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return UserProfile.model_validate_json(raw)

    def upsert(self, profile: UserProfile) -> None:
        profile.updated_at = utc_now()
        self._client.set(
            self._key(profile.user_id),
            profile.model_dump_json(),
            ex=self._ttl_seconds,
        )

    def _key(self, user_id: str) -> str:
        return f"{self._key_prefix}:{user_id}"

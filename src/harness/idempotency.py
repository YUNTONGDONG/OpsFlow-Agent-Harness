"""Persistent idempotency records for side-effecting Harness steps."""

from enum import Enum
from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .task_state import utc_now


class IdempotencyStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    FAILED = "failed"


class IdempotencyRecord(BaseModel):
    key: str
    status: IdempotencyStatus
    result: dict[str, Any] | None = None
    external_resource_id: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


class IdempotencyStore(Protocol):
    def get(self, key: str) -> IdempotencyRecord | None: ...

    def save(self, record: IdempotencyRecord) -> None: ...


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._records: dict[str, IdempotencyRecord] = {}

    def get(self, key: str) -> IdempotencyRecord | None:
        record = self._records.get(key)
        return record.model_copy(deep=True) if record is not None else None

    def save(self, record: IdempotencyRecord) -> None:
        self._records[record.key] = record.model_copy(deep=True)


class RedisIdempotencyStore:
    def __init__(
        self,
        client: Any,
        *,
        key_prefix: str = "harness:idempotency",
        ttl_seconds: int = 604_800,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Idempotency TTL must be positive")
        self._client = client
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    def get(self, key: str) -> IdempotencyRecord | None:
        raw = self._client.get(self._key(key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return IdempotencyRecord.model_validate_json(raw)

    def save(self, record: IdempotencyRecord) -> None:
        self._client.set(
            self._key(record.key),
            record.model_dump_json(),
            ex=self._ttl_seconds,
        )

    def _key(self, key: str) -> str:
        return f"{self._key_prefix}:{key}"

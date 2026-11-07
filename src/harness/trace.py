"""Request-level trace events emitted by the Harness runtime."""

from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
import json

from pydantic import BaseModel, Field

from .task_state import utc_now


class TraceEventType(str, Enum):
    REQUEST_STARTED = "request_started"
    REQUEST_COMPLETED = "request_completed"
    ROUTE_STARTED = "route_started"
    ROUTE_COMPLETED = "route_completed"
    TASK_CREATED = "task_created"
    TASK_COMPLETED = "task_completed"
    SKILL_STARTED = "skill_started"
    INPUT_REJECTED = "input_rejected"
    SKILL_SUCCEEDED = "skill_succeeded"
    SKILL_FAILED = "skill_failed"
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_COMPLETED = "recovery_completed"


class TraceEvent(BaseModel):
    request_id: str
    event_type: TraceEventType
    session_id: str | None = None
    user_id: str | None = None
    task_id: str | None = None
    step_id: str | None = None
    skill_name: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    attributes: dict[str, Any] = Field(default_factory=dict)


class TraceSink(Protocol):
    def emit(self, event: TraceEvent) -> None: ...


class NullTraceSink:
    def emit(self, event: TraceEvent) -> None:
        return None


class InMemoryTraceSink:
    """Trace collector used by tests and local inspection."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def emit(self, event: TraceEvent) -> None:
        self._events.append(event.model_copy(deep=True))

    def events_for(self, request_id: str) -> list[TraceEvent]:
        return [
            event.model_copy(deep=True)
            for event in self._events
            if event.request_id == request_id
        ]


class JsonlTraceSink:
    """Append request trace events to a local JSONL audit file."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def emit(self, event: TraceEvent) -> None:
        line = event.model_dump_json() + "\n"
        with self._lock, self._path.open("a", encoding="utf-8") as trace_file:
            trace_file.write(line)


def load_trace_events(path: str | Path) -> list[TraceEvent]:
    events = []
    with Path(path).open(encoding="utf-8") as trace_file:
        for line_number, line in enumerate(trace_file, start=1):
            if not line.strip():
                continue
            try:
                events.append(TraceEvent.model_validate(json.loads(line)))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid trace event on line {line_number}") from exc
    return events

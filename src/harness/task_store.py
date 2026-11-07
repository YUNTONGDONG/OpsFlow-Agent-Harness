"""Storage boundary for Harness task state."""

from typing import Any, Protocol

from .task_state import TaskState


class TaskStore(Protocol):
    def create(self, task: TaskState) -> None: ...

    def get(self, task_id: str) -> TaskState: ...

    def save(self, task: TaskState) -> None: ...

    def list_unfinished(self) -> list[TaskState]: ...


class InMemoryTaskStore:
    """Deterministic store used by tests and local Harness execution."""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskState] = {}

    def create(self, task: TaskState) -> None:
        if task.task_id in self._tasks:
            raise ValueError(f"Task already exists: {task.task_id}")
        self._tasks[task.task_id] = task.model_copy(deep=True)

    def get(self, task_id: str) -> TaskState:
        try:
            return self._tasks[task_id].model_copy(deep=True)
        except KeyError as exc:
            raise KeyError(f"Unknown task: {task_id}") from exc

    def save(self, task: TaskState) -> None:
        if task.task_id not in self._tasks:
            raise KeyError(f"Unknown task: {task.task_id}")
        self._tasks[task.task_id] = task.model_copy(deep=True)

    def list_unfinished(self) -> list[TaskState]:
        terminal = {"completed", "failed", "cancelled"}
        return [
            task.model_copy(deep=True)
            for task in self._tasks.values()
            if task.status.value not in terminal
        ]


class RedisClient(Protocol):
    def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> Any: ...

    def get(self, name: str) -> str | bytes | None: ...

    def exists(self, name: str) -> int: ...

    def scan_iter(self, match: str): ...


class RedisTaskStore:
    """Persist task state as JSON using an injected Redis client."""

    def __init__(
        self,
        client: RedisClient,
        *,
        key_prefix: str = "harness:task",
        ttl_seconds: int = 86_400,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("Task TTL must be positive")
        self._client = client
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds

    def create(self, task: TaskState) -> None:
        created = self._client.set(
            self._key(task.task_id),
            task.model_dump_json(),
            ex=self._ttl_seconds,
            nx=True,
        )
        if not created:
            raise ValueError(f"Task already exists: {task.task_id}")

    def get(self, task_id: str) -> TaskState:
        raw_task = self._client.get(self._key(task_id))
        if raw_task is None:
            raise KeyError(f"Unknown task: {task_id}")
        if isinstance(raw_task, bytes):
            raw_task = raw_task.decode("utf-8")
        return TaskState.model_validate_json(raw_task)

    def save(self, task: TaskState) -> None:
        key = self._key(task.task_id)
        if not self._client.exists(key):
            raise KeyError(f"Unknown task: {task.task_id}")
        self._client.set(
            key,
            task.model_dump_json(),
            ex=self._ttl_seconds,
        )

    def list_unfinished(self) -> list[TaskState]:
        terminal = {"completed", "failed", "cancelled"}
        tasks = []
        for raw_key in self._client.scan_iter(match=f"{self._key_prefix}:*"):
            key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
            task_id = key.removeprefix(f"{self._key_prefix}:")
            task = self.get(task_id)
            if task.status.value not in terminal:
                tasks.append(task)
        return tasks

    def _key(self, task_id: str) -> str:
        return f"{self._key_prefix}:{task_id}"

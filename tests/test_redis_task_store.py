import unittest

from src.harness import RedisTaskStore, TaskState, TaskStatus, TaskStep


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.expirations: dict[str, int | None] = {}

    def set(
        self,
        name: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and name in self.data:
            return False
        self.data[name] = value
        self.expirations[name] = ex
        return True

    def get(self, name: str) -> str | None:
        return self.data.get(name)

    def exists(self, name: str) -> int:
        return int(name in self.data)

    def scan_iter(self, match: str):
        prefix = match.removesuffix("*")
        return [key for key in self.data if key.startswith(prefix)]


def build_task() -> TaskState:
    return TaskState(
        task_id="redis-task",
        request_id="redis-request",
        steps=[TaskStep(step_id="one", skill_name="knowledge_search")],
    )


class RedisTaskStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = FakeRedis()
        self.store = RedisTaskStore(self.redis, ttl_seconds=300)

    def test_task_round_trip_and_ttl(self) -> None:
        task = build_task()
        self.store.create(task)

        loaded = self.store.get("redis-task")

        self.assertEqual(loaded, task)
        self.assertEqual(self.redis.expirations["harness:task:redis-task"], 300)

    def test_saved_transition_is_restored(self) -> None:
        task = build_task()
        self.store.create(task)
        task.start_step("one")
        self.store.save(task)

        self.assertEqual(self.store.get("redis-task").status, TaskStatus.RUNNING)

    def test_duplicate_and_missing_tasks_are_rejected(self) -> None:
        task = build_task()
        self.store.create(task)

        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.create(task)
        with self.assertRaisesRegex(KeyError, "Unknown task"):
            self.store.get("missing")

    def test_list_unfinished_excludes_terminal_tasks(self) -> None:
        pending = build_task()
        completed = TaskState(
            task_id="completed-task",
            request_id="completed-request",
            steps=[TaskStep(step_id="one", skill_name="knowledge_search")],
        )
        completed.start_step("one")
        completed.complete_step("one", {"data": "done"})
        self.store.create(pending)
        self.store.create(completed)

        tasks = self.store.list_unfinished()

        self.assertEqual([task.task_id for task in tasks], ["redis-task"])


if __name__ == "__main__":
    unittest.main()

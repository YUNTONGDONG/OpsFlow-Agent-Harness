import unittest

from pydantic import ValidationError

from src.harness import (
    InMemoryTaskStore,
    StepStatus,
    TaskState,
    TaskStatus,
    TaskStep,
)


def build_task() -> TaskState:
    return TaskState(
        task_id="task-1",
        request_id="request-1",
        steps=[
            TaskStep(
                step_id="search_logs",
                skill_name="log_diagnosis",
                arguments={"query": "E102"},
            ),
            TaskStep(
                step_id="search_knowledge",
                skill_name="knowledge_search",
                arguments={"query": "E102"},
                depends_on=["search_logs"],
            ),
        ],
    )


class TaskStateTest(unittest.TestCase):
    def test_task_follows_dependency_order_to_completion(self) -> None:
        task = build_task()

        self.assertEqual(
            [step.step_id for step in task.ready_steps()],
            ["search_logs"],
        )

        task.start_step("search_logs")
        task.complete_step("search_logs", {"matches": 3})
        task.start_step("search_knowledge")
        task.complete_step("search_knowledge", {"answer": "restart"})

        self.assertEqual(task.status, TaskStatus.COMPLETED)
        self.assertTrue(
            all(step.status == StepStatus.COMPLETED for step in task.steps)
        )

    def test_step_cannot_start_before_dependencies_finish(self) -> None:
        task = build_task()

        with self.assertRaisesRegex(ValueError, "waiting for"):
            task.start_step("search_knowledge")

    def test_failed_step_fails_task(self) -> None:
        task = build_task()
        task.start_step("search_logs")
        task.fail_step("search_logs", "LOG_API_TIMEOUT")

        self.assertEqual(task.status, TaskStatus.FAILED)
        self.assertEqual(task.steps[0].error_code, "LOG_API_TIMEOUT")

    def test_invalid_dependency_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "unknown dependencies"):
            TaskState(
                task_id="task-1",
                request_id="request-1",
                steps=[
                    TaskStep(
                        step_id="answer",
                        skill_name="knowledge_search",
                        depends_on=["missing"],
                    )
                ],
            )

    def test_dependency_cycle_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "contain a cycle"):
            TaskState(
                task_id="task-1",
                request_id="request-1",
                steps=[
                    TaskStep(
                        step_id="one",
                        skill_name="one",
                        depends_on=["two"],
                    ),
                    TaskStep(
                        step_id="two",
                        skill_name="two",
                        depends_on=["one"],
                    ),
                ],
            )


class InMemoryTaskStoreTest(unittest.TestCase):
    def test_store_uses_copies_to_protect_saved_state(self) -> None:
        store = InMemoryTaskStore()
        task = build_task()
        store.create(task)

        loaded = store.get("task-1")
        loaded.start_step("search_logs")

        self.assertEqual(store.get("task-1").status, TaskStatus.PENDING)

        store.save(loaded)
        self.assertEqual(store.get("task-1").status, TaskStatus.RUNNING)

    def test_duplicate_task_is_rejected(self) -> None:
        store = InMemoryTaskStore()
        task = build_task()
        store.create(task)

        with self.assertRaisesRegex(ValueError, "already exists"):
            store.create(task)


if __name__ == "__main__":
    unittest.main()

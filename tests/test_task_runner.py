import unittest
from threading import Barrier, Lock
from time import sleep

from pydantic import BaseModel

from src.harness import (
    BaseSkill,
    InMemoryTaskStore,
    InMemoryTraceSink,
    SkillRegistry,
    SkillResult,
    SkillRuntime,
    SkillOperationType,
    VerificationResult,
    VerificationStatus,
    StepStatus,
    TaskRunner,
    TaskState,
    TaskStatus,
    TaskStep,
)


class OrderedInput(BaseModel):
    value: str


class OrderedSkill(BaseSkill[OrderedInput]):
    description = "Record execution order."
    input_schema = OrderedInput

    def __init__(self, name: str, calls: list[str], fail: bool = False) -> None:
        self.name = name
        self._calls = calls
        self._fail = fail

    def execute(self, arguments: OrderedInput) -> SkillResult:
        self._calls.append(self.name)
        if self._fail:
            return SkillResult(success=False, error_code="EXPECTED_FAILURE")
        return SkillResult(success=True, data={"value": arguments.value})


class CoordinatedReadSkill(OrderedSkill):
    operation_type = SkillOperationType.READ
    concurrency_limit = 2
    timeout_seconds = 1

    def __init__(self, name, calls, barrier):
        super().__init__(name, calls)
        self._barrier = barrier

    def execute(self, arguments: OrderedInput) -> SkillResult:
        self._calls.append(self.name)
        self._barrier.wait()
        return SkillResult(success=True, data={"value": arguments.value})


class TimedReadSkill(OrderedSkill):
    operation_type = SkillOperationType.READ
    concurrency_limit = 2

    def __init__(self, name, calls, delay, timeout, fail=False):
        super().__init__(name, calls, fail=fail)
        self._delay = delay
        self.timeout_seconds = timeout

    def execute(self, arguments: OrderedInput) -> SkillResult:
        sleep(self._delay)
        return super().execute(arguments)


class LimitedReadSkill(OrderedSkill):
    operation_type = SkillOperationType.READ
    concurrency_limit = 1
    timeout_seconds = 1

    def __init__(self, name, calls):
        super().__init__(name, calls)
        self._active = 0
        self.max_active = 0
        self._lock = Lock()

    def execute(self, arguments: OrderedInput) -> SkillResult:
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        sleep(0.02)
        with self._lock:
            self._active -= 1
        return super().execute(arguments)


class WriteSkill(OrderedSkill):
    operation_type = SkillOperationType.NON_IDEMPOTENT_WRITE

    def verify(self, arguments, output):
        return VerificationResult(
            status=VerificationStatus.VERIFIED,
            external_resource_id=f"write:{arguments.value}",
        )


def build_task() -> TaskState:
    return TaskState(
        task_id="task-runner",
        request_id="request-runner",
        steps=[
            TaskStep(
                step_id="first",
                skill_name="first_skill",
                arguments={"value": "one"},
            ),
            TaskStep(
                step_id="second",
                skill_name="second_skill",
                arguments={"value": "two"},
                depends_on=["first"],
            ),
        ],
    )


class TaskRunnerTest(unittest.TestCase):
    def test_task_runs_skills_in_dependency_order(self) -> None:
        calls: list[str] = []
        registry = SkillRegistry()
        registry.register(OrderedSkill("first_skill", calls))
        registry.register(OrderedSkill("second_skill", calls))
        trace = InMemoryTraceSink()
        runtime = SkillRuntime(registry, trace_sink=trace)
        store = InMemoryTaskStore()
        store.create(build_task())

        result = TaskRunner(runtime, store).run("task-runner")

        self.assertEqual(calls, ["first_skill", "second_skill"])
        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertTrue(
            all(step.status == StepStatus.COMPLETED for step in result.steps)
        )
        self.assertEqual(len(trace.events_for("request-runner")), 4)
        self.assertEqual(store.get("task-runner").status, TaskStatus.COMPLETED)

    def test_failure_stops_dependent_steps(self) -> None:
        calls: list[str] = []
        registry = SkillRegistry()
        registry.register(OrderedSkill("first_skill", calls, fail=True))
        registry.register(OrderedSkill("second_skill", calls))
        runtime = SkillRuntime(registry)
        store = InMemoryTaskStore()
        store.create(build_task())

        result = TaskRunner(runtime, store).run("task-runner")

        self.assertEqual(calls, ["first_skill"])
        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(result.steps[0].error_code, "EXPECTED_FAILURE")
        self.assertEqual(result.steps[1].status, StepStatus.PENDING)

    def test_independent_reads_execute_concurrently(self) -> None:
        calls = []
        barrier = Barrier(2, timeout=1)
        registry = SkillRegistry()
        registry.register(CoordinatedReadSkill("read_one", calls, barrier))
        registry.register(CoordinatedReadSkill("read_two", calls, barrier))
        store = InMemoryTaskStore()
        task = TaskState(
            task_id="parallel-reads",
            request_id="parallel-request",
            steps=[
                TaskStep(
                    step_id="one",
                    skill_name="read_one",
                    arguments={"value": "one"},
                ),
                TaskStep(
                    step_id="two",
                    skill_name="read_two",
                    arguments={"value": "two"},
                ),
            ],
        )
        store.create(task)

        result = TaskRunner(
            SkillRuntime(registry),
            store,
            max_concurrency=2,
        ).run(task.task_id)

        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertCountEqual(calls, ["read_one", "read_two"])

    def test_parallel_failure_preserves_independent_success(self) -> None:
        calls = []
        registry = SkillRegistry()
        registry.register(TimedReadSkill("success", calls, 0.01, 1))
        registry.register(TimedReadSkill("failure", calls, 0.01, 1, fail=True))
        store = InMemoryTaskStore()
        task = TaskState(
            task_id="partial-failure",
            request_id="partial-request",
            steps=[
                TaskStep(
                    step_id="success",
                    skill_name="success",
                    arguments={"value": "saved"},
                ),
                TaskStep(
                    step_id="failure",
                    skill_name="failure",
                    arguments={"value": "failed"},
                ),
            ],
        )
        store.create(task)

        result = TaskRunner(SkillRuntime(registry), store).run(task.task_id)

        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(result.steps[0].status, StepStatus.COMPLETED)
        self.assertEqual(result.steps[1].status, StepStatus.FAILED)

    def test_read_timeout_is_recorded_without_running_serial_writes(self) -> None:
        calls = []
        registry = SkillRegistry()
        registry.register(TimedReadSkill("slow", calls, 0.05, 0.01))
        registry.register(TimedReadSkill("fast", calls, 0, 1))
        store = InMemoryTaskStore()
        task = TaskState(
            task_id="read-timeout",
            request_id="timeout-request",
            steps=[
                TaskStep(
                    step_id="slow",
                    skill_name="slow",
                    arguments={"value": "slow"},
                ),
                TaskStep(
                    step_id="fast",
                    skill_name="fast",
                    arguments={"value": "fast"},
                ),
            ],
        )
        store.create(task)

        result = TaskRunner(SkillRuntime(registry), store).run(task.task_id)

        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertEqual(result.steps[0].error_code, "SKILL_TIMEOUT")
        self.assertEqual(result.steps[1].status, StepStatus.COMPLETED)
        sleep(0.06)

    def test_per_skill_concurrency_limit_is_enforced(self) -> None:
        calls = []
        skill = LimitedReadSkill("limited", calls)
        registry = SkillRegistry()
        registry.register(skill)
        store = InMemoryTaskStore()
        task = TaskState(
            task_id="limited-reads",
            request_id="limited-request",
            steps=[
                TaskStep(
                    step_id="one",
                    skill_name="limited",
                    arguments={"value": "one"},
                ),
                TaskStep(
                    step_id="two",
                    skill_name="limited",
                    arguments={"value": "two"},
                ),
            ],
        )
        store.create(task)

        result = TaskRunner(SkillRuntime(registry), store).run(task.task_id)

        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertEqual(skill.max_active, 1)

    def test_independent_writes_remain_serial(self) -> None:
        calls = []
        registry = SkillRegistry()
        registry.register(WriteSkill("write_one", calls))
        registry.register(WriteSkill("write_two", calls))
        store = InMemoryTaskStore()
        task = TaskState(
            task_id="serial-writes",
            request_id="serial-write-request",
            steps=[
                TaskStep(
                    step_id="one",
                    skill_name="write_one",
                    arguments={"value": "one"},
                ),
                TaskStep(
                    step_id="two",
                    skill_name="write_two",
                    arguments={"value": "two"},
                ),
            ],
        )
        store.create(task)

        result = TaskRunner(SkillRuntime(registry), store).run(task.task_id)

        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertEqual(calls, ["write_one", "write_two"])


if __name__ == "__main__":
    unittest.main()

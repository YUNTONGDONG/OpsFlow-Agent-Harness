import unittest

from pydantic import BaseModel

from src.harness import (
    BaseSkill,
    IdempotencyRecord,
    IdempotencyStatus,
    InMemoryIdempotencyStore,
    InMemoryTaskStore,
    SkillOperationType,
    SkillRegistry,
    SkillResult,
    SkillRuntime,
    StepStatus,
    TaskRunner,
    TaskState,
    TaskStatus,
    TaskStep,
    VerificationResult,
    VerificationStatus,
)


class WriteInput(BaseModel):
    value: str


class ReliableWriteSkill(BaseSkill[WriteInput]):
    name = "reliable_write"
    description = "Write one external resource."
    input_schema = WriteInput
    operation_type = SkillOperationType.NON_IDEMPOTENT_WRITE

    def __init__(self, verification_statuses):
        self.calls = 0
        self._verification_statuses = iter(verification_statuses)

    def execute(self, arguments: WriteInput) -> SkillResult:
        self.calls += 1
        return SkillResult(
            success=True,
            data={"result": {"id": "external-123", "value": arguments.value}},
        )

    def verify(self, arguments, output):
        return VerificationResult(
            status=next(self._verification_statuses),
            external_resource_id="external-123",
        )


class ReadSkill(BaseSkill[WriteInput]):
    name = "safe_read"
    description = "Read without side effects."
    input_schema = WriteInput
    operation_type = SkillOperationType.READ

    def __init__(self):
        self.calls = 0

    def execute(self, arguments):
        self.calls += 1
        return SkillResult(success=True, data={"value": arguments.value})


def build_task(task_id, skill_name, *, idempotency_key=None):
    return TaskState(
        task_id=task_id,
        request_id=f"request-{task_id}",
        steps=[
            TaskStep(
                step_id="write",
                skill_name=skill_name,
                arguments={"value": "E102"},
                idempotency_key=idempotency_key,
            )
        ],
    )


class WriteReliabilityTest(unittest.TestCase):
    def test_verified_idempotency_record_prevents_duplicate_write(self) -> None:
        skill = ReliableWriteSkill([VerificationStatus.VERIFIED])
        registry = SkillRegistry()
        registry.register(skill)
        store = InMemoryTaskStore()
        idempotency = InMemoryIdempotencyStore()
        runner = TaskRunner(
            SkillRuntime(registry),
            store,
            idempotency_store=idempotency,
        )
        first = build_task("first", skill.name, idempotency_key="shared-key")
        second = build_task("second", skill.name, idempotency_key="shared-key")
        store.create(first)
        store.create(second)

        first_result = runner.run(first.task_id)
        second_result = runner.run(second.task_id)

        self.assertEqual(first_result.status, TaskStatus.COMPLETED)
        self.assertEqual(second_result.status, TaskStatus.COMPLETED)
        self.assertEqual(skill.calls, 1)
        self.assertEqual(
            second_result.steps[0].external_resource_id,
            "external-123",
        )

    def test_unverified_write_becomes_unknown_and_is_not_repeated(self) -> None:
        skill = ReliableWriteSkill([VerificationStatus.UNKNOWN])
        registry = SkillRegistry()
        registry.register(skill)
        store = InMemoryTaskStore()
        task = build_task("unknown", skill.name)
        store.create(task)
        runner = TaskRunner(SkillRuntime(registry), store)

        first_result = runner.run(task.task_id)
        second_result = runner.run(task.task_id)

        self.assertEqual(first_result.status, TaskStatus.UNKNOWN)
        self.assertEqual(first_result.steps[0].status, StepStatus.UNKNOWN)
        self.assertEqual(second_result.status, TaskStatus.UNKNOWN)
        self.assertEqual(skill.calls, 1)

    def test_recovery_reverifies_unknown_write_without_reexecuting(self) -> None:
        skill = ReliableWriteSkill(
            [VerificationStatus.UNKNOWN, VerificationStatus.VERIFIED]
        )
        registry = SkillRegistry()
        registry.register(skill)
        store = InMemoryTaskStore()
        task = build_task("recover-unknown", skill.name)
        store.create(task)
        runner = TaskRunner(SkillRuntime(registry), store)

        unknown_result = runner.run(task.task_id)
        recovered_result = runner.recover(task.task_id)

        self.assertEqual(unknown_result.status, TaskStatus.UNKNOWN)
        self.assertEqual(recovered_result.status, TaskStatus.COMPLETED)
        self.assertEqual(skill.calls, 1)
        self.assertEqual(
            recovered_result.steps[0].external_resource_id,
            "external-123",
        )

    def test_interrupted_read_is_safe_to_restart(self) -> None:
        skill = ReadSkill()
        registry = SkillRegistry()
        registry.register(skill)
        store = InMemoryTaskStore()
        task = build_task("recover-read", skill.name)
        store.create(task)
        task.start_step("write", {"value": "E102"})
        store.save(task)

        result = TaskRunner(SkillRuntime(registry), store).recover(task.task_id)

        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertEqual(skill.calls, 1)

    def test_interrupted_write_without_evidence_becomes_unknown(self) -> None:
        skill = ReliableWriteSkill([])
        registry = SkillRegistry()
        registry.register(skill)
        store = InMemoryTaskStore()
        idempotency = InMemoryIdempotencyStore()
        task = build_task("recover-write", skill.name, idempotency_key="started")
        store.create(task)
        task.start_step("write", {"value": "E102"})
        store.save(task)
        idempotency.save(
            IdempotencyRecord(
                key="started",
                status=IdempotencyStatus.IN_PROGRESS,
            )
        )

        result = TaskRunner(
            SkillRuntime(registry),
            store,
            idempotency_store=idempotency,
        ).recover(task.task_id)

        self.assertEqual(result.status, TaskStatus.UNKNOWN)
        self.assertEqual(result.steps[0].status, StepStatus.UNKNOWN)
        self.assertEqual(skill.calls, 0)


if __name__ == "__main__":
    unittest.main()

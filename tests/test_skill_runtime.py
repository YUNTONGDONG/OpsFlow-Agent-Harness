import unittest

from pydantic import BaseModel

from src.harness import (
    BaseSkill,
    RetryPolicy,
    SkillOperationType,
    SkillRegistry,
    SkillResult,
    SkillRuntime,
)


class RuntimeInput(BaseModel):
    value: int


class RuntimeOutput(BaseModel):
    value: int


class RuntimeSkill(BaseSkill[RuntimeInput]):
    name = "runtime_test"
    description = "Exercise the runtime boundary."
    input_schema = RuntimeInput
    output_schema = RuntimeOutput

    def __init__(self) -> None:
        self.calls = 0

    def execute(self, arguments: RuntimeInput) -> SkillResult:
        self.calls += 1
        return SkillResult(success=True, data={"value": arguments.value})


class ExplodingSkill(RuntimeSkill):
    name = "exploding"

    def execute(self, arguments: RuntimeInput) -> SkillResult:
        raise RuntimeError("sensitive backend detail")


class InvalidOutputSkill(RuntimeSkill):
    name = "invalid_output"

    def execute(self, arguments: RuntimeInput) -> SkillResult:
        return SkillResult(success=True, data={"value": "not-an-integer"})


class RetryReadSkill(RuntimeSkill):
    name = "retry_read"
    retry_policy = RetryPolicy(max_attempts=3)

    def execute(self, arguments: RuntimeInput) -> SkillResult:
        self.calls += 1
        if self.calls < 3:
            return SkillResult(success=False, error_code="TEMPORARY")
        return SkillResult(success=True, data={"value": arguments.value})


class NoRetryWriteSkill(RuntimeSkill):
    name = "no_retry_write"
    operation_type = SkillOperationType.NON_IDEMPOTENT_WRITE
    retry_policy = RetryPolicy(max_attempts=3)

    def execute(self, arguments: RuntimeInput) -> SkillResult:
        self.calls += 1
        return SkillResult(success=False, error_code="WRITE_FAILED")


class SkillRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = SkillRegistry()
        self.skill = RuntimeSkill()
        self.registry.register(self.skill)
        self.runtime = SkillRuntime(self.registry)

    def test_valid_input_executes_registered_skill(self) -> None:
        result = self.runtime.execute(
            "runtime_test",
            {"value": 7},
            request_id="request-1",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.request_id, "request-1")
        self.assertEqual(result.skill_name, "runtime_test")
        self.assertEqual(result.data, {"value": 7})
        self.assertGreaterEqual(result.duration_ms, 0)
        self.assertEqual(self.skill.calls, 1)

    def test_invalid_input_is_rejected_before_execution(self) -> None:
        result = self.runtime.execute("runtime_test", {"value": "invalid"})

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "INVALID_SKILL_INPUT")
        self.assertEqual(self.skill.calls, 0)
        self.assertIn("validation_errors", result.data)

    def test_unknown_skill_returns_normalized_error(self) -> None:
        result = self.runtime.execute("missing", {})

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "SKILL_NOT_FOUND")

    def test_unexpected_exception_does_not_leak_backend_details(self) -> None:
        self.registry.register(ExplodingSkill())

        result = self.runtime.execute("exploding", {"value": 1})

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "SKILL_EXECUTION_FAILED")
        self.assertNotIn("sensitive backend detail", result.message or "")

    def test_invalid_output_is_rejected_after_execution(self) -> None:
        self.registry.register(InvalidOutputSkill())

        result = self.runtime.execute("invalid_output", {"value": 1})

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "INVALID_SKILL_OUTPUT")

    def test_read_uses_bounded_retry_policy(self) -> None:
        skill = RetryReadSkill()
        self.registry.register(skill)

        result = self.runtime.execute("retry_read", {"value": 1})

        self.assertTrue(result.success)
        self.assertEqual(result.attempt_count, 3)
        self.assertEqual(skill.calls, 3)

    def test_non_idempotent_write_is_never_blindly_retried(self) -> None:
        skill = NoRetryWriteSkill()
        self.registry.register(skill)

        result = self.runtime.execute("no_retry_write", {"value": 1})

        self.assertFalse(result.success)
        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(skill.calls, 1)


if __name__ == "__main__":
    unittest.main()

import unittest

from pydantic import BaseModel

from src.harness import (
    BaseSkill,
    InMemoryTraceSink,
    SkillRegistry,
    SkillResult,
    SkillRuntime,
    TraceEventType,
)


class TraceInput(BaseModel):
    query: str


class TraceSkill(BaseSkill[TraceInput]):
    name = "trace_test"
    description = "Exercise request tracing."
    input_schema = TraceInput

    def execute(self, arguments: TraceInput) -> SkillResult:
        return SkillResult(success=True, data={"answer": arguments.query})


class TraceTest(unittest.TestCase):
    def setUp(self) -> None:
        registry = SkillRegistry()
        registry.register(TraceSkill())
        self.sink = InMemoryTraceSink()
        self.runtime = SkillRuntime(registry, trace_sink=self.sink)

    def test_success_trace_is_correlated_by_request_id(self) -> None:
        self.runtime.execute(
            "trace_test",
            {"query": "E102"},
            request_id="request-success",
        )

        events = self.sink.events_for("request-success")
        self.assertEqual(
            [event.event_type for event in events],
            [TraceEventType.SKILL_STARTED, TraceEventType.SKILL_SUCCEEDED],
        )
        self.assertIn("duration_ms", events[-1].attributes)
        self.assertNotIn("query", events[-1].attributes)

    def test_validation_failure_has_explicit_trace_event(self) -> None:
        self.runtime.execute(
            "trace_test",
            {},
            request_id="request-invalid",
        )

        events = self.sink.events_for("request-invalid")
        self.assertEqual(events[-1].event_type, TraceEventType.INPUT_REJECTED)
        self.assertEqual(
            events[-1].attributes["error_code"],
            "INVALID_SKILL_INPUT",
        )

    def test_unknown_skill_failure_is_traced(self) -> None:
        self.runtime.execute("missing", {}, request_id="request-missing")

        events = self.sink.events_for("request-missing")
        self.assertEqual(events[-1].event_type, TraceEventType.SKILL_FAILED)
        self.assertEqual(events[-1].attributes["error_code"], "SKILL_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()

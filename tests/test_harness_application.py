import unittest

from pydantic import BaseModel

from src.harness import (
    BaseSkill,
    CallableRouter,
    HarnessApplication,
    HarnessResponseStatus,
    InMemoryTaskStore,
    InMemoryTraceSink,
    InMemoryWorkspace,
    RouteDecision,
    RouteStep,
    SkillRegistry,
    SkillResult,
    SkillRuntime,
    SkillOperationType,
    TraceEventType,
)


class AppInput(BaseModel):
    value: str


class AppOutput(BaseModel):
    value: str


class AppSkill(BaseSkill[AppInput]):
    description = "Harness application test skill."
    input_schema = AppInput
    output_schema = AppOutput

    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self._calls = calls

    def execute(self, arguments: AppInput) -> SkillResult:
        self._calls.append(self.name)
        return SkillResult(success=True, data={"value": arguments.value})


class WriteAppSkill(AppSkill):
    operation_type = SkillOperationType.NON_IDEMPOTENT_WRITE


def build_application(route_fn, calls):
    registry = SkillRegistry()
    registry.register(AppSkill("first", calls))
    registry.register(AppSkill("second", calls))
    trace = InMemoryTraceSink()
    runtime = SkillRuntime(registry, trace_sink=trace)
    app = HarnessApplication(
        registry=registry,
        router=CallableRouter(route_fn),
        runtime=runtime,
        task_store=InMemoryTaskStore(),
        trace_sink=trace,
    )
    return app, trace


class HarnessApplicationTest(unittest.TestCase):
    def test_single_read_is_recorded_as_a_task(self) -> None:
        calls = []
        app, trace = build_application(
            lambda request, skills, context: RouteDecision(
                steps=[
                    RouteStep(
                        step_id="answer",
                        skill_name="first",
                        arguments={"value": request},
                    )
                ]
            ),
            calls,
        )

        response = app.handle("hello", request_id="direct-request")

        self.assertEqual(response.status, HarnessResponseStatus.COMPLETED)
        self.assertIsNotNone(response.task_id)
        self.assertEqual(calls, ["first"])
        self.assertEqual(
            [event.event_type for event in trace.events_for("direct-request")],
            [
                TraceEventType.REQUEST_STARTED,
                TraceEventType.ROUTE_STARTED,
                TraceEventType.ROUTE_COMPLETED,
                TraceEventType.TASK_CREATED,
                TraceEventType.SKILL_STARTED,
                TraceEventType.SKILL_SUCCEEDED,
                TraceEventType.TASK_COMPLETED,
                TraceEventType.REQUEST_COMPLETED,
            ],
        )

    def test_task_request_plan_and_step_artifacts_are_in_workspace(self) -> None:
        calls = []
        registry = SkillRegistry()
        registry.register(AppSkill("read", calls))
        store = InMemoryTaskStore()
        workspace = InMemoryWorkspace()
        app = HarnessApplication(
            registry=registry,
            router=CallableRouter(
                lambda request, skills, context: {
                    "steps": [
                        {
                            "step_id": "read",
                            "skill_name": "read",
                            "arguments": {"value": request},
                        }
                    ]
                }
            ),
            runtime=SkillRuntime(registry),
            task_store=store,
            workspace=workspace,
        )

        response = app.handle("E102", request_id="workspace-request")
        task = store.get(response.task_id)

        self.assertEqual(
            workspace.read_json(task.request_ref)["request"],
            "E102",
        )
        self.assertEqual(
            workspace.read_json(task.plan_ref)["steps"][0]["skill_name"],
            "read",
        )
        self.assertEqual(
            workspace.read_json(task.steps[0].input_ref),
            {"value": "E102"},
        )
        self.assertEqual(
            workspace.read_json(task.steps[0].output_ref),
            {"value": "E102"},
        )
        self.assertEqual(
            workspace.read_json(task.response_ref)["status"],
            "completed",
        )

    def test_trace_links_request_task_step_and_workspace_evidence(self) -> None:
        calls = []
        app, trace = build_application(
            lambda request, skills, context: {
                "steps": [
                    {
                        "step_id": "answer",
                        "skill_name": "first",
                        "arguments": {"value": request},
                    }
                ]
            },
            calls,
        )

        response = app.handle(
            "E102",
            request_id="observable-request",
            session_id="session-1",
            user_id="user-1",
        )
        events = trace.events_for("observable-request")

        self.assertEqual(events[0].event_type, TraceEventType.REQUEST_STARTED)
        self.assertEqual(events[0].session_id, "session-1")
        self.assertEqual(events[0].user_id, "user-1")
        task_created = next(
            event for event in events if event.event_type == TraceEventType.TASK_CREATED
        )
        self.assertEqual(task_created.task_id, response.task_id)
        self.assertTrue(task_created.attributes["request_ref"]["uri"])
        skill_started = next(
            event for event in events if event.event_type == TraceEventType.SKILL_STARTED
        )
        self.assertEqual(skill_started.task_id, response.task_id)
        self.assertEqual(skill_started.step_id, "answer")
        self.assertTrue(skill_started.attributes["input_ref"]["uri"])
        task_completed = next(
            event for event in events if event.event_type == TraceEventType.TASK_COMPLETED
        )
        self.assertTrue(task_completed.attributes["steps"][0]["output_ref"]["uri"])
        request_completed = events[-1]
        self.assertEqual(request_completed.event_type, TraceEventType.REQUEST_COMPLETED)
        self.assertIn("duration_ms", request_completed.attributes)
        self.assertTrue(request_completed.attributes["response_ref"]["uri"])

    def test_large_upstream_output_binds_from_workspace_reference(self) -> None:
        calls = []
        registry = SkillRegistry()
        registry.register(AppSkill("first", calls))
        registry.register(AppSkill("second", calls))
        store = InMemoryTaskStore()
        app = HarnessApplication(
            registry=registry,
            router=CallableRouter(
                lambda request, skills, context: {
                    "steps": [
                        {
                            "step_id": "one",
                            "skill_name": "first",
                            "arguments": {"value": "large-output"},
                        },
                        {
                            "step_id": "two",
                            "skill_name": "second",
                            "arguments": {
                                "value": {"$from": "one.output.value"}
                            },
                            "depends_on": ["one"],
                        },
                    ]
                }
            ),
            runtime=SkillRuntime(registry),
            task_store=store,
            inline_result_max_bytes=1,
        )

        response = app.handle("bind large output")
        task = store.get(response.task_id)

        self.assertIsNone(task.steps[0].result["data"])
        self.assertIsNotNone(task.steps[0].result["workspace_ref"])
        self.assertEqual(
            task.steps[1].resolved_arguments,
            {"value": "large-output"},
        )

    def test_unverified_single_write_uses_task_and_becomes_unknown(self) -> None:
        calls = []
        registry = SkillRegistry()
        registry.register(WriteAppSkill("write", calls))
        app = HarnessApplication(
            registry=registry,
            router=CallableRouter(
                lambda request, skills, context: {
                    "steps": [
                        {
                            "step_id": "write",
                            "skill_name": "write",
                            "arguments": {"value": "create"},
                        }
                    ]
                }
            ),
            runtime=SkillRuntime(registry),
            task_store=InMemoryTaskStore(),
        )

        response = app.handle("create")

        self.assertEqual(response.status, HarnessResponseStatus.FAILED)
        self.assertEqual(response.error_code, "EXTERNAL_STATE_UNKNOWN")
        self.assertIsNotNone(response.task_id)
        self.assertEqual(calls, ["write"])

    def test_recovery_actions_are_traced(self) -> None:
        calls = []
        registry = SkillRegistry()
        registry.register(WriteAppSkill("write", calls))
        trace = InMemoryTraceSink()
        app = HarnessApplication(
            registry=registry,
            router=CallableRouter(
                lambda request, skills, context: {
                    "steps": [
                        {
                            "step_id": "write",
                            "skill_name": "write",
                            "arguments": {"value": "create"},
                        }
                    ]
                }
            ),
            runtime=SkillRuntime(registry, trace_sink=trace),
            task_store=InMemoryTaskStore(),
            trace_sink=trace,
        )
        response = app.handle("create", request_id="recovery-request")

        recovered = app.recover_task(response.task_id)

        self.assertEqual(recovered.status.value, "unknown")
        event_types = [
            event.event_type for event in trace.events_for("recovery-request")
        ]
        self.assertIn(TraceEventType.RECOVERY_STARTED, event_types)
        self.assertIn(TraceEventType.RECOVERY_COMPLETED, event_types)

    def test_complex_route_creates_dependency_aware_task(self) -> None:
        calls = []
        app, trace = build_application(
            lambda request, skills, context: {
                "steps": [
                    {
                        "step_id": "one",
                        "skill_name": "first",
                        "arguments": {"value": "one"},
                    },
                    {
                        "step_id": "two",
                        "skill_name": "second",
                        "arguments": {
                            "value": {"$from": "one.output.value"}
                        },
                        "depends_on": ["one"],
                    },
                ]
            },
            calls,
        )

        response = app.handle("complex", request_id="complex-request")

        self.assertEqual(response.status, HarnessResponseStatus.COMPLETED)
        self.assertIsNotNone(response.task_id)
        self.assertEqual(calls, ["first", "second"])
        second_step = response.data["steps"][1]
        self.assertEqual(second_step["resolved_arguments"], {"value": "one"})
        event_types = [
            event.event_type for event in trace.events_for("complex-request")
        ]
        self.assertIn(TraceEventType.TASK_CREATED, event_types)
        self.assertIn(TraceEventType.TASK_COMPLETED, event_types)

    def test_unknown_output_field_is_rejected_before_execution(self) -> None:
        calls = []
        app, _ = build_application(
            lambda request, skills, context: {
                "steps": [
                    {
                        "step_id": "one",
                        "skill_name": "first",
                        "arguments": {"value": "one"},
                    },
                    {
                        "step_id": "two",
                        "skill_name": "second",
                        "arguments": {
                            "value": {"$from": "one.output.missing"}
                        },
                        "depends_on": ["one"],
                    },
                ]
            },
            calls,
        )

        response = app.handle("invalid binding")

        self.assertEqual(response.status, HarnessResponseStatus.FAILED)
        self.assertEqual(response.error_code, "INVALID_ROUTE_PLAN")
        self.assertEqual(calls, [])

    def test_output_reference_requires_dependency(self) -> None:
        calls = []
        app, _ = build_application(
            lambda request, skills, context: {
                "steps": [
                    {
                        "step_id": "one",
                        "skill_name": "first",
                        "arguments": {"value": "one"},
                    },
                    {
                        "step_id": "two",
                        "skill_name": "second",
                        "arguments": {
                            "value": {"$from": "one.output.value"}
                        },
                    },
                ]
            },
            calls,
        )

        response = app.handle("missing dependency")

        self.assertEqual(response.status, HarnessResponseStatus.FAILED)
        self.assertEqual(response.error_code, "INVALID_TASK_PLAN")
        self.assertEqual(calls, [])

    def test_route_to_unregistered_skill_is_rejected(self) -> None:
        app, trace = build_application(
            lambda request, skills, context: {
                "steps": [
                    {
                        "step_id": "unknown",
                        "skill_name": "not_registered",
                    }
                ]
            },
            [],
        )

        response = app.handle("unknown", request_id="invalid-route")

        self.assertEqual(response.status, HarnessResponseStatus.FAILED)
        self.assertEqual(response.error_code, "INVALID_ROUTE_PLAN")
        self.assertEqual(
            next(
                event
                for event in trace.events_for("invalid-route")
                if event.event_type == TraceEventType.ROUTE_COMPLETED
            ).attributes["success"],
            False,
        )

    def test_clarification_does_not_execute_a_skill(self) -> None:
        calls = []
        app, trace = build_application(
            lambda request, skills, context: {
                "steps": [],
                "clarification": {
                    "question": "会议安排在什么时间？",
                    "missing_fields": ["start", "end"],
                },
            },
            calls,
        )

        response = app.handle("帮我创建一个会议", request_id="clarify")

        self.assertEqual(response.status, HarnessResponseStatus.NEEDS_INPUT)
        self.assertEqual(response.data["missing_fields"], ["start", "end"])
        self.assertEqual(calls, [])
        self.assertTrue(
            next(
                event
                for event in trace.events_for("clarify")
                if event.event_type == TraceEventType.ROUTE_COMPLETED
            ).attributes[
                "requires_clarification"
            ]
        )


if __name__ == "__main__":
    unittest.main()

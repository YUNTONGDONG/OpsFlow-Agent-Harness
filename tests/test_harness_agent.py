import tempfile
import unittest
from pathlib import Path

from src.HarnessAgent import HarnessAgent
from src.harness import (
    HarnessResponse,
    HarnessResponseStatus,
    JsonlTraceSink,
    InMemorySessionStore,
    InMemoryUserProfileStore,
    SessionMessage,
    TraceEvent,
    TraceEventType,
    UserProfile,
)


class FakeApplication:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def handle(self, request, context=None, session_id=None, user_id=None):
        self.requests.append((request, context, session_id, user_id))
        return self.response


class HarnessAgentTest(unittest.TestCase):
    def test_agent_keeps_legacy_run_agent_response_shape(self) -> None:
        application = FakeApplication(
            HarnessResponse(
                request_id="request-1",
                status=HarnessResponseStatus.COMPLETED,
                data={"answer": "Use the E102 runbook."},
            )
        )
        session_store = InMemorySessionStore()
        session_store.append(
            "session-1",
            SessionMessage(role="user", content="previous question"),
        )
        agent = HarnessAgent(
            application=application,
            session_store=session_store,
        )

        result = agent.run_agent("E102", session_id="session-1")

        self.assertEqual(result["output"], "Use the E102 runbook.")
        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(
            application.requests,
            [
                (
                    "E102",
                    [{"role": "user", "content": "previous question"}],
                    "session-1",
                    None,
                )
            ],
        )
        self.assertEqual(len(session_store.recent("session-1", 10)), 3)

    def test_agent_returns_safe_failure_message(self) -> None:
        agent = HarnessAgent(
            application=FakeApplication(
                HarnessResponse(
                    request_id="request-2",
                    status=HarnessResponseStatus.FAILED,
                    error_code="INVALID_ROUTE_PLAN",
                )
            )
        )

        result = agent.run_agent("bad route")

        self.assertIn("INVALID_ROUTE_PLAN", result["output"])

    def test_agent_renders_and_stores_clarification_question(self) -> None:
        session_store = InMemorySessionStore()
        agent = HarnessAgent(
            application=FakeApplication(
                HarnessResponse(
                    request_id="request-clarify",
                    status=HarnessResponseStatus.NEEDS_INPUT,
                    data={
                        "question": "会议安排在什么时间？",
                        "missing_fields": ["start", "end"],
                    },
                )
            ),
            session_store=session_store,
        )

        result = agent.run_agent("帮我创建一个会议", session_id="session-1")

        self.assertEqual(result["output"], "会议安排在什么时间？")
        messages = session_store.recent("session-1", 10)
        self.assertEqual(messages[-1].content, "会议安排在什么时间？")

    def test_user_profile_is_added_as_system_routing_context(self) -> None:
        profile_store = InMemoryUserProfileStore()
        profile_store.upsert(
            UserProfile(
                user_id="user-1",
                time_zone="America/New_York",
                output_language="zh-CN",
            )
        )
        application = FakeApplication(
            HarnessResponse(
                request_id="profile-request",
                status=HarnessResponseStatus.COMPLETED,
                data={"answer": "done"},
            )
        )
        agent = HarnessAgent(
            application=application,
            session_store=InMemorySessionStore(),
            profile_store=profile_store,
        )

        agent.run_agent("schedule it", "session-1", user_id="user-1")

        context = application.requests[0][1]
        self.assertEqual(context[0]["role"], "system")
        self.assertIn("America/New_York", context[0]["content"])

    def test_jsonl_trace_sink_persists_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            sink = JsonlTraceSink(path)
            sink.emit(
                TraceEvent(
                    request_id="request-3",
                    event_type=TraceEventType.ROUTE_STARTED,
                )
            )

            content = path.read_text(encoding="utf-8")

        self.assertIn('"request_id":"request-3"', content)
        self.assertIn('"event_type":"route_started"', content)


if __name__ == "__main__":
    unittest.main()

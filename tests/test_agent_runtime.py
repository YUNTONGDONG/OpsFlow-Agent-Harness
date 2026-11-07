import os
import unittest
from unittest.mock import patch

from src.agent_runtime import build_message_agent


class FakeAgent:
    def run_agent(self, input, session_id=None, user_id=None):
        return {"output": input, "session_id": session_id}


class AgentRuntimeTest(unittest.TestCase):
    def test_harness_is_the_default_runtime(self) -> None:
        harness = FakeAgent()

        with patch.dict(os.environ, {}, clear=True):
            result = build_message_agent(harness_factory=lambda: harness)

        self.assertIs(result, harness)

    def test_legacy_runtime_requires_explicit_selection(self) -> None:
        legacy = FakeAgent()

        result = build_message_agent(
            "legacy",
            legacy_factory=lambda: legacy,
        )

        self.assertIs(result, legacy)

    def test_unknown_runtime_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_message_agent("unknown")


if __name__ == "__main__":
    unittest.main()

import unittest

from src.cli import run_once


class FakeAgent:
    def __init__(self) -> None:
        self.calls = []

    def run_agent(self, input, session_id=None, user_id=None):
        self.calls.append((input, session_id, user_id))
        return {"output": "done", "session_id": session_id}


class CliAdapterTest(unittest.TestCase):
    def test_cli_adapter_uses_shared_agent_contract(self) -> None:
        agent = FakeAgent()

        result = run_once(
            agent,
            "diagnose E102",
            "cli-session",
            "cli-user",
        )

        self.assertEqual(result["output"], "done")
        self.assertEqual(
            agent.calls,
            [("diagnose E102", "cli-session", "cli-user")],
        )


if __name__ == "__main__":
    unittest.main()

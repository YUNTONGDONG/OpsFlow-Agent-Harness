import unittest

from fastapi.testclient import TestClient

from src.api import app, get_message_agent, get_user_profile_store
from src.harness import InMemoryUserProfileStore


class FakeAgent:
    def run_agent(self, input, session_id=None, user_id=None):
        return {
            "output": f"handled: {input}",
            "request_id": "request-api",
            "task_id": None,
            "session_id": session_id,
            "user_id": user_id,
        }


class ApiBaselineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.profile_store = InMemoryUserProfileStore()
        app.dependency_overrides[get_message_agent] = lambda: FakeAgent()
        app.dependency_overrides[get_user_profile_store] = (
            lambda: self.profile_store
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()

    def test_health_endpoint_requires_no_external_credentials(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "mode": "demo"})

    def test_root_describes_reproducible_entrypoints(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["docs"], "/docs")
        self.assertEqual(response.json()["health"], "/health")

    def test_agent_endpoint_uses_shared_agent_contract(self) -> None:
        response = self.client.post(
            "/v1/agent/run",
            json={
                "message": "diagnose E102",
                "session_id": "api-session",
                "user_id": "user-1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["output"], "handled: diagnose E102")
        self.assertEqual(response.json()["request_id"], "request-api")
        self.assertEqual(response.json()["session_id"], "api-session")
        self.assertEqual(response.json()["user_id"], "user-1")

    def test_profile_can_be_updated_and_read(self) -> None:
        updated = self.client.put(
            "/v1/users/user-1/profile",
            json={
                "user_id": "ignored-body-id",
                "time_zone": "America/New_York",
                "output_language": "zh-CN",
            },
        )
        loaded = self.client.get("/v1/users/user-1/profile")

        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["user_id"], "user-1")
        self.assertEqual(loaded.json()["time_zone"], "America/New_York")

    def test_demo_scenarios_are_discoverable(self) -> None:
        response = self.client.get("/v1/demo/scenarios")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["scenarios"]), 5)

    def test_demo_scenario_endpoint_returns_trace_and_boundary(self) -> None:
        response = self.client.post("/v1/demo/scenarios/knowledge")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scenario"], "knowledge")
        self.assertTrue(response.json()["trace"])
        self.assertTrue(response.json()["evidence_boundary"])

    def test_demo_scenarios_are_discoverable(self) -> None:
        response = self.client.get("/v1/demo/scenarios")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["scenarios"]), 5)


if __name__ == "__main__":
    unittest.main()

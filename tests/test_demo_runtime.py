import tempfile
import unittest
from pathlib import Path

from src.demo_runtime import DEMO_SCENARIOS, DemoEnvironment
from src.harness import TraceEventType


class DemoRuntimeTest(unittest.TestCase):
    def test_all_five_scenarios_return_trace_and_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for scenario in DEMO_SCENARIOS:
                with self.subTest(scenario=scenario):
                    result = DemoEnvironment(root / scenario).run_scenario(scenario)
                    self.assertEqual(result["scenario"], scenario)
                    self.assertTrue(result["trace"])
                    self.assertTrue(result["evidence_boundary"])

    def test_knowledge_demo_records_100_20_3_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            result = DemoEnvironment(directory).run_scenario("knowledge")
        succeeded = next(
            event for event in result["trace"]
            if event["event_type"] == TraceEventType.SKILL_SUCCEEDED.value
        )
        self.assertEqual(
            [item["candidate_count"] for item in succeeded["attributes"]["retrieval_stages"][:3]],
            [100, 20, 3],
        )
        self.assertEqual(result["responses"][0]["status"], "completed")

    def test_log_demo_creates_and_verifies_task(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = DemoEnvironment(directory)
            result = environment.run_scenario("log_to_task")
        self.assertEqual(result["responses"][0]["status"], "completed")
        self.assertEqual(len(environment.created_tasks), 1)
        completed = next(
            event for event in result["trace"]
            if event["event_type"] == TraceEventType.TASK_COMPLETED.value
        )
        create_step = completed["attributes"]["steps"][1]
        self.assertEqual(create_step["verification_status"], "verified")
        self.assertTrue(create_step["external_resource_id"])

    def test_parallel_demo_starts_both_reads_before_either_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            result = DemoEnvironment(directory).run_scenario("parallel_reads")
        event_types = [event["event_type"] for event in result["trace"]]
        started_indices = [
            index for index, event_type in enumerate(event_types)
            if event_type == TraceEventType.SKILL_STARTED.value
        ]
        first_finished = next(
            index for index, event_type in enumerate(event_types)
            if event_type == TraceEventType.SKILL_SUCCEEDED.value
        )
        self.assertEqual(len(started_indices), 2)
        self.assertLess(max(started_indices), first_finished)

    def test_safe_delete_requires_pending_confirmation_and_rejects_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = DemoEnvironment(directory)
            result = environment.run_scenario("safe_delete")
            replay = environment.application.handle("confirm-delete event-project-review")
        self.assertEqual(
            [response["status"] for response in result["responses"]],
            ["completed", "completed"],
        )
        self.assertEqual(replay.status.value, "failed")

    def test_recovery_demo_completes_interrupted_read(self):
        with tempfile.TemporaryDirectory() as directory:
            result = DemoEnvironment(directory).run_scenario("recovery")
        self.assertEqual(result["responses"][0]["status"], "completed")
        event_types = [event["event_type"] for event in result["trace"]]
        self.assertIn(TraceEventType.RECOVERY_STARTED.value, event_types)
        self.assertIn(TraceEventType.RECOVERY_COMPLETED.value, event_types)


if __name__ == "__main__":
    unittest.main()

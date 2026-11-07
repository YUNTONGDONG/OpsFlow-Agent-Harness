import unittest

from src.harness import (
    CallableRouter,
    HarnessApplication,
    HarnessResponseStatus,
    InMemoryTaskStore,
    SkillRegistry,
    SkillRuntime,
    VerificationResult,
    VerificationStatus,
)
from src.harness.skills import LogDiagnosisSkill, TaskManagementSkill


class LogToTaskPlanTest(unittest.TestCase):
    def test_log_output_is_bound_into_task_input(self) -> None:
        created_tasks = []
        registry = SkillRegistry()
        registry.register(
            LogDiagnosisSkill(
                backend=lambda arguments: {
                    "status": "success",
                    "line_count": 2,
                    "level_counts": {"ERROR": 1},
                    "suspicious_lines": ["ERROR E102 database timeout"],
                }
            )
        )
        registry.register(
            TaskManagementSkill(
                create_fn=lambda arguments: created_tasks.append(arguments)
                or {"id": "task-123"},
                verify_fn=lambda arguments, output: VerificationResult(
                    status=VerificationStatus.VERIFIED,
                    external_resource_id="task-123",
                ),
            )
        )
        router = CallableRouter(
            lambda request, skills, context: {
                "steps": [
                    {
                        "step_id": "diagnose",
                        "skill_name": "log_diagnosis",
                        "arguments": {
                            "action": "analyze_text",
                            "log_text": "INFO start\nERROR E102 database timeout",
                        },
                    },
                    {
                        "step_id": "create_task",
                        "skill_name": "task_management",
                        "arguments": {
                            "subject": {
                                "$from": "diagnose.output.issue_title"
                            },
                            "description": {
                                "$from": "diagnose.output.summary"
                            },
                        },
                        "depends_on": ["diagnose"],
                    },
                ]
            }
        )
        app = HarnessApplication(
            registry=registry,
            router=router,
            runtime=SkillRuntime(registry),
            task_store=InMemoryTaskStore(),
        )

        response = app.handle("diagnose logs and create a task")

        self.assertEqual(response.status, HarnessResponseStatus.COMPLETED)
        self.assertEqual(len(created_tasks), 1)
        self.assertEqual(
            created_tasks[0].subject,
            "Investigate E102 in provided logs",
        )
        self.assertIn("Analyzed 2 log lines", created_tasks[0].description)
        create_step = response.data["steps"][1]
        self.assertEqual(
            create_step["resolved_arguments"]["subject"],
            "Investigate E102 in provided logs",
        )


if __name__ == "__main__":
    unittest.main()

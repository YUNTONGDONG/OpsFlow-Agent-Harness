import unittest

from pydantic import ValidationError

from src.harness.skills import (
    CalendarAction,
    CalendarManagementSkill,
    TaskManagementSkill,
)
from src.harness import SkillOperationType


class BusinessSkillsTest(unittest.TestCase):
    def test_task_skill_maps_validated_request_to_backend(self) -> None:
        received = []
        skill = TaskManagementSkill(
            create_fn=lambda arguments: received.append(arguments) or "created"
        )

        arguments = skill.validate_input(
            {
                "subject": "Review E102 incident",
                "due_time": "2026-11-03T18:00:00Z",
            }
        )
        result = skill.execute(arguments)

        self.assertTrue(result.success)
        self.assertEqual(received[0].subject, "Review E102 incident")

    def test_calendar_create_requires_event_fields(self) -> None:
        skill = CalendarManagementSkill(backend=lambda arguments: "created")

        with self.assertRaisesRegex(ValidationError, "requires"):
            skill.validate_input(
                {"action": CalendarAction.CREATE, "summary": "Review"}
            )

    def test_calendar_write_action_uses_validated_backend(self) -> None:
        received = []
        skill = CalendarManagementSkill(
            backend=lambda arguments: received.append(arguments) or "created"
        )
        arguments = skill.validate_input(
            {
                "action": "create",
                "summary": "E102 review",
                "start": {
                    "date_time": "2026-11-03T15:00:00",
                    "time_zone": "America/New_York",
                },
                "end": {
                    "date_time": "2026-11-03T16:00:00",
                    "time_zone": "America/New_York",
                },
            }
        )
        result = skill.execute(arguments)

        self.assertTrue(result.success)
        self.assertEqual(received[0].action, CalendarAction.CREATE)

    def test_provider_failure_is_normalized(self) -> None:
        skill = CalendarManagementSkill(
            backend=lambda arguments: "创建日程失败: timeout"
        )
        arguments = skill.validate_input(
            {
                "action": "confirm_delete",
                "event_id": "event-1",
            }
        )
        result = skill.execute(arguments)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "CALENDAR_OPERATION_FAILED")
        self.assertNotIn("timeout", result.message or "")

    def test_calendar_reads_and_writes_have_different_operation_types(self) -> None:
        skill = CalendarManagementSkill(backend=lambda arguments: "ok")

        self.assertEqual(
            skill.operation_type_for({"action": "search"}),
            SkillOperationType.READ,
        )
        self.assertEqual(
            skill.operation_type_for({"action": "create"}),
            SkillOperationType.NON_IDEMPOTENT_WRITE,
        )
        self.assertEqual(
            skill.operation_type_for({"action": "confirm_delete"}),
            SkillOperationType.HUMAN_CONFIRMATION_REQUIRED,
        )


if __name__ == "__main__":
    unittest.main()

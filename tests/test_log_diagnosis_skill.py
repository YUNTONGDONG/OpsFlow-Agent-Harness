import unittest

from pydantic import ValidationError

from src.harness.skills import LogDiagnosisSkill


class LogDiagnosisSkillTest(unittest.TestCase):
    def test_query_requires_log_source_fields(self) -> None:
        skill = LogDiagnosisSkill(backend=lambda arguments: {})

        with self.assertRaisesRegex(ValidationError, "requires"):
            skill.validate_input({"action": "query", "service": "payments"})

    def test_analyze_text_returns_structured_evidence(self) -> None:
        skill = LogDiagnosisSkill(
            backend=lambda arguments: {
                "status": "success",
                "line_count": 2,
                "level_counts": {"ERROR": 1},
                "suspicious_lines": ["ERROR E102 database timeout"],
            }
        )
        arguments = skill.validate_input(
            {
                "action": "analyze_text",
                "log_text": "INFO start\nERROR E102 database timeout",
            }
        )

        result = skill.execute(arguments)

        self.assertTrue(result.success)
        self.assertEqual(result.data["matched_error_codes"], ["E102"])
        self.assertEqual(
            result.data["issue_title"],
            "Investigate E102 in provided logs",
        )
        self.assertIn("Analyzed 2 log lines", result.data["summary"])

    def test_unavailable_backend_is_a_normalized_failure(self) -> None:
        skill = LogDiagnosisSkill(
            backend=lambda arguments: {
                "status": "unavailable",
                "message": "secret endpoint detail",
            }
        )
        arguments = skill.validate_input(
            {
                "action": "query",
                "service": "payments",
                "environment": "production",
                "start_time": "2026-11-07T10:00:00Z",
                "end_time": "2026-11-07T11:00:00Z",
            }
        )

        result = skill.execute(arguments)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "LOG_DIAGNOSIS_UNAVAILABLE")
        self.assertNotIn("secret endpoint detail", result.message or "")


if __name__ == "__main__":
    unittest.main()

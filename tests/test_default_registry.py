import unittest

from src.harness import build_default_registry
from src.harness.skills import WebSearchSkill


class DefaultRegistryTest(unittest.TestCase):
    def test_default_registry_exposes_canonical_skills(self) -> None:
        registry = build_default_registry()

        self.assertEqual(
            [item["name"] for item in registry.list_skills()],
            [
                "knowledge_search",
                "log_diagnosis",
                "web_search",
                "calendar_management",
                "task_management",
            ],
        )

    def test_web_search_skill_normalizes_backend_failure(self) -> None:
        skill = WebSearchSkill(search_fn=lambda query: (_ for _ in ()).throw(
            RuntimeError("provider key leaked")
        ))
        arguments = skill.validate_input({"query": "latest release"})

        result = skill.execute(arguments)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "WEB_SEARCH_FAILED")
        self.assertNotIn("provider key leaked", result.message or "")


if __name__ == "__main__":
    unittest.main()

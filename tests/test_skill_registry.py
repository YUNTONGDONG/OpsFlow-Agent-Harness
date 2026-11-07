import unittest

from pydantic import BaseModel

from src.harness import BaseSkill, SkillRegistry, SkillResult


class EchoInput(BaseModel):
    text: str


class EchoSkill(BaseSkill[EchoInput]):
    name = "echo"
    description = "Return the provided text."
    input_schema = EchoInput

    def execute(self, arguments: EchoInput) -> SkillResult:
        return SkillResult(success=True, data={"text": arguments.text})


class SkillRegistryTest(unittest.TestCase):
    def test_register_discover_and_execute_skill(self) -> None:
        registry = SkillRegistry()
        registry.register(EchoSkill())

        self.assertEqual(
            registry.list_skills(),
            [{"name": "echo", "description": "Return the provided text."}],
        )

        skill = registry.get("echo")
        arguments = skill.validate_input({"text": "hello"})
        result = skill.execute(arguments)

        self.assertTrue(result.success)
        self.assertEqual(result.data, {"text": "hello"})

    def test_duplicate_registration_is_rejected(self) -> None:
        registry = SkillRegistry()
        registry.register(EchoSkill())

        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(EchoSkill())

    def test_registry_can_expose_input_schema_for_routing(self) -> None:
        registry = SkillRegistry()
        registry.register(EchoSkill())

        metadata = registry.list_skills(include_schema=True)[0]

        self.assertEqual(metadata["name"], "echo")
        self.assertIn("text", metadata["input_schema"]["properties"])
        self.assertEqual(metadata["operation_type"], "read")
        self.assertEqual(metadata["retry_policy"]["max_attempts"], 1)
        self.assertEqual(metadata["timeout_seconds"], 30)

    def test_unknown_skill_is_rejected(self) -> None:
        with self.assertRaisesRegex(KeyError, "Unknown skill"):
            SkillRegistry().get("missing")


if __name__ == "__main__":
    unittest.main()

"""Registration and discovery for pluggable skills."""

from typing import Any

from .skill import BaseSkill


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> BaseSkill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown skill: {name}") from exc

    def list_skills(self, *, include_schema: bool = False) -> list[dict[str, Any]]:
        items = []
        for skill in self._skills.values():
            item: dict[str, Any] = {
                "name": skill.name,
                "description": skill.description,
            }
            if include_schema:
                item["input_schema"] = skill.input_schema.model_json_schema()
                item["output_schema"] = (
                    skill.output_schema.model_json_schema()
                    if skill.output_schema is not None
                    else None
                )
                item["operation_type"] = skill.operation_type.value
                item["retry_policy"] = skill.retry_policy.model_dump(mode="json")
                item["timeout_seconds"] = skill.timeout_seconds
                item["concurrency_limit"] = skill.concurrency_limit
            items.append(item)
        return items

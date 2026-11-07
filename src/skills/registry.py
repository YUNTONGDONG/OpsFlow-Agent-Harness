from collections import OrderedDict
from typing import Iterable

from .base import SkillDefinition


class SkillRegistry:
    """Registers skills and exposes only the tools needed for a request."""

    def __init__(self) -> None:
        self._skills: OrderedDict[str, SkillDefinition] = OrderedDict()

    def register(self, skill: SkillDefinition) -> None:
        if skill.name in self._skills:
            raise ValueError(f"Skill already registered: {skill.name}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillDefinition:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Unknown skill: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(self._skills)

    def descriptions(self) -> list[dict[str, object]]:
        return [
            {
                "name": skill.name,
                "description": skill.description,
                "read_only": skill.read_only,
                "tools": list(skill.tool_names),
            }
            for skill in self._skills.values()
        ]

    def tools_for(self, skill_names: Iterable[str]) -> list[object]:
        tools: list[object] = []
        seen: set[str] = set()
        for skill_name in skill_names:
            for tool in self.get(skill_name).iter_tools():
                tool_name = getattr(tool, "name", repr(tool))
                if tool_name not in seen:
                    seen.add(tool_name)
                    tools.append(tool)
        return tools

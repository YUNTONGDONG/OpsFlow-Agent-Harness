"""Default Harness composition for the capabilities implemented in this repo."""

from .registry import SkillRegistry
from .skills import (
    CalendarManagementSkill,
    KnowledgeSearchSkill,
    LogDiagnosisSkill,
    TaskManagementSkill,
    WebSearchSkill,
)


def build_default_registry() -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(KnowledgeSearchSkill())
    registry.register(LogDiagnosisSkill())
    registry.register(WebSearchSkill())
    registry.register(CalendarManagementSkill())
    registry.register(TaskManagementSkill())
    return registry

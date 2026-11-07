"""Legacy AgentExecutor skill wrappers.

The production Harness uses ``src.harness.skills``. This package remains only
for the explicit ``AGENT_RUNTIME=legacy`` migration fallback.
"""

from .calendar import calendar_skill
from .knowledge import knowledge_search_skill
from .log_diagnosis import log_diagnosis_skill
from .registry import SkillRegistry
from .tasks import task_skill


def build_default_registry() -> SkillRegistry:
    registry = SkillRegistry()
    for skill in (
        knowledge_search_skill,
        log_diagnosis_skill,
        calendar_skill,
        task_skill,
    ):
        registry.register(skill)
    return registry


__all__ = ["SkillRegistry", "build_default_registry"]

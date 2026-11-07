from pydantic import BaseModel, Field

from .skills.registry import SkillRegistry


class TaskStep(BaseModel):
    step_id: str
    skill_name: str
    depends_on: list[str] = Field(default_factory=list)


class RouteDecision(BaseModel):
    skill_names: list[str] = Field(default_factory=list)
    task_plan: list[TaskStep] = Field(default_factory=list)

    @property
    def is_complex(self) -> bool:
        return len(self.task_plan) > 1


class SkillRouter:
    """Small deterministic router used before exposing tools to the LLM."""

    _priority = (
        "log_diagnosis",
        "knowledge_search",
        "calendar_management",
        "task_management",
    )

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def route(self, user_input: str) -> RouteDecision:
        matched = [
            name for name in self._priority
            if name in self.registry.names() and self.registry.get(name).matches(user_input)
        ]

        # General conversation does not need any business tool definitions.
        if not matched:
            return RouteDecision()

        plan: list[TaskStep] = []
        previous_step: str | None = None
        for index, skill_name in enumerate(matched, start=1):
            step_id = f"step_{index}"
            plan.append(
                TaskStep(
                    step_id=step_id,
                    skill_name=skill_name,
                    depends_on=[previous_step] if previous_step else [],
                )
            )
            previous_step = step_id

        self._validate_plan(plan)
        return RouteDecision(skill_names=matched, task_plan=plan)

    @staticmethod
    def _validate_plan(plan: list[TaskStep]) -> None:
        known_steps: set[str] = set()
        for step in plan:
            missing = set(step.depends_on) - known_steps
            if missing:
                raise ValueError(
                    f"Task step {step.step_id} depends on unknown steps: {sorted(missing)}"
                )
            known_steps.add(step.step_id)

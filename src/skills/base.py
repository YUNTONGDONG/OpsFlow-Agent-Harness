from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class SkillDefinition:
    """A reusable business capability exposed to the agent runtime."""

    name: str
    description: str
    tools: Sequence[Any] = field(default_factory=tuple)
    trigger_terms: Sequence[str] = field(default_factory=tuple)
    read_only: bool = True

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(getattr(tool, "name", tool.__class__.__name__) for tool in self.tools)

    def matches(self, text: str) -> bool:
        normalized = text.lower()
        return any(term.lower() in normalized for term in self.trigger_terms)

    def iter_tools(self) -> Iterable[Any]:
        return iter(self.tools)

"""Structured routing contract for direct and multi-step Harness requests."""

from collections.abc import Callable
import json
from typing import Any, Protocol

from pydantic import BaseModel, Field, ValidationError, model_validator


class RouteStep(BaseModel):
    step_id: str = Field(min_length=1)
    skill_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class ClarificationRequest(BaseModel):
    question: str = Field(min_length=1)
    missing_fields: list[str] = Field(min_length=1)


class RouteDecision(BaseModel):
    steps: list[RouteStep] = Field(default_factory=list)
    clarification: ClarificationRequest | None = None

    @model_validator(mode="after")
    def validate_decision_mode(self) -> "RouteDecision":
        has_steps = bool(self.steps)
        has_clarification = self.clarification is not None
        if has_steps == has_clarification:
            raise ValueError(
                "Route decision must contain either executable steps or "
                "one clarification request"
            )
        independent_skills: set[str] = set()
        for step in self.steps:
            if step.depends_on:
                continue
            if step.skill_name in independent_skills:
                raise ValueError(
                    "Independent steps must not call the same skill more than "
                    "once; combine them or declare a dependency"
                )
            independent_skills.add(step.skill_name)
        return self


class RequestRouter(Protocol):
    def route(
        self,
        request: str,
        available_skills: list[dict[str, Any]],
        context: list[dict[str, str]] | None = None,
    ) -> RouteDecision: ...


class CallableRouter:
    """Adapter for an LLM planner or deterministic routing function."""

    def __init__(
        self,
        route_fn: Callable[
            [str, list[dict[str, Any]], list[dict[str, str]] | None],
            RouteDecision | dict[str, Any],
        ],
    ) -> None:
        self._route_fn = route_fn

    def route(
        self,
        request: str,
        available_skills: list[dict[str, Any]],
        context: list[dict[str, str]] | None = None,
    ) -> RouteDecision:
        result = self._route_fn(request, available_skills, context)
        return RouteDecision.model_validate(result)


class LangChainRequestRouter:
    """Use a chat model's structured output support to create a route plan."""

    def __init__(self, model: Any) -> None:
        # LangChain 0.3 defaults to OpenAI strict structured output. RouteStep
        # intentionally has optional/defaulted fields, so function calling is
        # the compatible schema transport for this routing contract.
        self._router_model = model.with_structured_output(
            RouteDecision,
            method="function_calling",
        )

    def route(
        self,
        request: str,
        available_skills: list[dict[str, Any]],
        context: list[dict[str, str]] | None = None,
    ) -> RouteDecision:
        skill_sections = []
        for skill in available_skills:
            section = f"- {skill['name']}: {skill['description']}"
            if skill.get("input_schema"):
                section += "\n  Input schema: " + json.dumps(
                    skill["input_schema"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if skill.get("output_schema"):
                section += "\n  Output schema: " + json.dumps(
                    skill["output_schema"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            skill_sections.append(section)
        skills_text = "\n".join(skill_sections)
        messages: list[tuple[str, str]] = [
            (
                "system",
                "You route requests to registered skills. Use only the skills "
                "listed below. For a simple request, return one step. For a "
                "multi-part request, return the minimum required steps and "
                "declare dependencies with depends_on. Do not invent missing "
                "business parameters. When a later step needs an earlier "
                "step's output, add that earlier step to depends_on and use "
                "an exact reference object such as "
                "{\"$from\":\"step_1.output.summary\"}. Use only fields "
                "declared in the source skill's output schema. Do not copy or "
                "guess a value that should come from an earlier step. "
                "Extract and normalize values already "
                "expressed in the request or conversation; never ask the user "
                "to repeat them. A search phrase can be used directly as query, "
                "and a concise task or meeting title can be derived from the "
                "requested work. Never request optional fields such as "
                "description. If a required semantic value is truly absent, "
                "return no steps and ask one concise clarification question "
                "listing only the missing schema fields. Every executable "
                "step's arguments must match that skill's input schema. Do not "
                "plan a later step that requires "
                "a future user confirmation; stop at the step that requests "
                "confirmation. One user goal should produce one step: a named "
                "source and its search topic belong in the same query, not two "
                "search steps. Call the same skill again only when the later "
                "call depends on the earlier result. The phrase 'search the "
                "knowledge base' without "
                "a topic is missing query. In 'modify the project review "
                "meeting', 'project review meeting' is the existing summary, "
                "so ask only for the new start and end.\n\nAvailable skills:\n"
                + skills_text,
            ),
        ]
        for message in context or []:
            role = message.get("role")
            content = message.get("content")
            if role in {"system", "user", "assistant"} and content:
                messages.append((role, content))
        messages.append(("human", request))
        try:
            result = self._router_model.invoke(messages)
            return RouteDecision.model_validate(result)
        except ValidationError as exc:
            messages.append(
                (
                    "human",
                    "The previous route violated the output contract. Correct "
                    "it once without changing the user's intent. Validation "
                    f"error: {exc}",
                )
            )
            result = self._router_model.invoke(messages)
            return RouteDecision.model_validate(result)

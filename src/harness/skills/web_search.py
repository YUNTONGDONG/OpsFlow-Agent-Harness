"""Real-time web search skill backed by the existing search tool."""

from collections.abc import Callable

from typing import Any

from pydantic import BaseModel, Field

from ..skill import BaseSkill, RetryPolicy, SkillOperationType, SkillResult


class WebSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


class WebSearchOutput(BaseModel):
    result: Any


def _search_existing_web_tool(query: str) -> str:
    from src.Tools import search

    return search.invoke({"query": query})


class WebSearchSkill(BaseSkill[WebSearchInput]):
    name = "web_search"
    description = "Search for current public information not stored internally."
    input_schema = WebSearchInput
    output_schema = WebSearchOutput
    operation_type = SkillOperationType.READ
    retry_policy = RetryPolicy(max_attempts=2, backoff_seconds=0.5)
    timeout_seconds = 15
    concurrency_limit = 4

    def __init__(self, search_fn: Callable[[str], str] | None = None) -> None:
        self._search_fn = search_fn or _search_existing_web_tool

    def execute(self, arguments: WebSearchInput) -> SkillResult:
        try:
            result = self._search_fn(arguments.query)
            return SkillResult(success=True, data={"result": result})
        except Exception:
            return SkillResult(
                success=False,
                error_code="WEB_SEARCH_FAILED",
                message="The web search could not be completed.",
            )

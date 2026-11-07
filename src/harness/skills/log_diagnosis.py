"""Structured log query and evidence extraction for the canonical Harness."""

from collections.abc import Callable
from enum import Enum
import json
import re
from typing import Any

from pydantic import BaseModel, Field, model_validator

from ..skill import BaseSkill, RetryPolicy, SkillOperationType, SkillResult


class LogDiagnosisAction(str, Enum):
    QUERY = "query"
    ANALYZE_TEXT = "analyze_text"


class LogDiagnosisInput(BaseModel):
    action: LogDiagnosisAction
    service: str | None = None
    environment: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    keyword: str | None = None
    log_text: str | None = Field(default=None, max_length=200_000)

    @model_validator(mode="after")
    def validate_action_fields(self) -> "LogDiagnosisInput":
        if self.action == LogDiagnosisAction.QUERY:
            missing = [
                name
                for name in ("service", "environment", "start_time", "end_time")
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError("Log query requires: " + ", ".join(missing))
        elif not self.log_text:
            raise ValueError("Log text analysis requires: log_text")
        return self


class LogDiagnosisOutput(BaseModel):
    status: str
    summary: str
    issue_title: str | None = None
    matched_error_codes: list[str] = Field(default_factory=list)
    evidence: Any


LogBackend = Callable[[LogDiagnosisInput], Any]


def _call_existing_log_tool(arguments: LogDiagnosisInput) -> Any:
    from src.log_tools import query_logs, summarize_log_text

    if arguments.action == LogDiagnosisAction.QUERY:
        return query_logs.invoke(
            {
                "query": {
                    "service": arguments.service,
                    "environment": arguments.environment,
                    "start_time": arguments.start_time,
                    "end_time": arguments.end_time,
                    "keyword": arguments.keyword,
                }
            }
        )
    return summarize_log_text.invoke(
        {"payload": {"log_text": arguments.log_text}}
    )


def _normalize_backend_result(raw_result: Any) -> dict[str, Any]:
    if isinstance(raw_result, str):
        try:
            parsed = json.loads(raw_result)
        except json.JSONDecodeError:
            parsed = {"status": "success", "data": raw_result}
    elif isinstance(raw_result, dict):
        parsed = raw_result
    else:
        parsed = {"status": "success", "data": raw_result}
    return parsed


class LogDiagnosisSkill(BaseSkill[LogDiagnosisInput]):
    name = "log_diagnosis"
    description = (
        "Query a configured log platform or analyze supplied log text. "
        "query requires service, environment, start_time, and end_time; "
        "analyze_text requires log_text."
    )
    input_schema = LogDiagnosisInput
    output_schema = LogDiagnosisOutput
    operation_type = SkillOperationType.READ
    retry_policy = RetryPolicy(max_attempts=2, backoff_seconds=0.5)
    timeout_seconds = 15
    concurrency_limit = 4

    def __init__(self, backend: LogBackend | None = None) -> None:
        self._backend = backend or _call_existing_log_tool

    def execute(self, arguments: LogDiagnosisInput) -> SkillResult:
        try:
            evidence = _normalize_backend_result(self._backend(arguments))
        except Exception:
            return SkillResult(
                success=False,
                error_code="LOG_DIAGNOSIS_FAILED",
                message="The log evidence could not be retrieved or analyzed.",
            )

        status = str(evidence.get("status", "success"))
        if status in {"error", "unavailable"}:
            return SkillResult(
                success=False,
                error_code="LOG_DIAGNOSIS_UNAVAILABLE",
                message="The configured log source is unavailable.",
            )

        serialized = json.dumps(evidence, ensure_ascii=False, default=str)
        error_codes = sorted(set(re.findall(r"\bE\d{3,}\b", serialized)))
        service = arguments.service or "provided logs"
        issue_title = (
            f"Investigate {error_codes[0]} in {service}" if error_codes else None
        )
        if arguments.action == LogDiagnosisAction.ANALYZE_TEXT:
            counts = evidence.get("level_counts", {})
            summary = (
                f"Analyzed {evidence.get('line_count', 0)} log lines; "
                f"levels={counts}."
            )
        else:
            summary = f"Queried {service} logs and collected structured evidence."

        return SkillResult(
            success=True,
            data={
                "status": status,
                "summary": summary,
                "issue_title": issue_title,
                "matched_error_codes": error_codes,
                "evidence": evidence,
            },
        )

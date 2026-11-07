"""Unified execution boundary for registered skills."""

from time import perf_counter, sleep
from typing import Any, Mapping
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from .registry import SkillRegistry
from .skill import (
    SkillOperationType,
    SkillResult,
    VerificationResult,
    VerificationStatus,
)
from .trace import NullTraceSink, TraceEvent, TraceEventType, TraceSink


class RuntimeResult(BaseModel):
    """Result envelope returned by the Harness runtime."""

    request_id: str
    skill_name: str
    success: bool
    data: Any | None = None
    error_code: str | None = None
    message: str | None = None
    duration_ms: float
    verification_status: VerificationStatus = VerificationStatus.VERIFIED
    external_resource_id: str | None = None
    verification_data: Any | None = None
    attempt_count: int = 1
    trace_attributes: dict[str, Any] = Field(default_factory=dict)


class SkillRuntime:
    """Resolve, validate, and execute skills through one controlled boundary."""

    def __init__(
        self,
        registry: SkillRegistry,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self._registry = registry
        self._trace_sink = trace_sink or NullTraceSink()

    def execute(
        self,
        skill_name: str,
        arguments: Mapping[str, Any],
        *,
        request_id: str | None = None,
        task_id: str | None = None,
        step_id: str | None = None,
        trace_attributes: Mapping[str, Any] | None = None,
    ) -> RuntimeResult:
        execution_id = request_id or str(uuid4())
        started_at = perf_counter()
        self._emit(
            execution_id,
            TraceEventType.SKILL_STARTED,
            skill_name,
            task_id=task_id,
            step_id=step_id,
            **dict(trace_attributes or {}),
        )

        try:
            skill = self._registry.get(skill_name)
        except KeyError:
            result = self._result(
                request_id=execution_id,
                skill_name=skill_name,
                started_at=started_at,
                result=SkillResult(
                    success=False,
                    error_code="SKILL_NOT_FOUND",
                    message=f"Unknown skill: {skill_name}",
                ),
            )
            self._emit_result(result, task_id=task_id, step_id=step_id)
            return result

        try:
            validated_arguments = skill.validate_input(arguments)
        except ValidationError as exc:
            result = self._result(
                request_id=execution_id,
                skill_name=skill_name,
                started_at=started_at,
                result=SkillResult(
                    success=False,
                    error_code="INVALID_SKILL_INPUT",
                    data={"validation_errors": exc.errors(include_url=False)},
                    message="The skill input did not match its schema.",
                ),
            )
            self._emit(
                execution_id,
                TraceEventType.INPUT_REJECTED,
                skill_name,
                error_code=result.error_code,
                duration_ms=result.duration_ms,
                task_id=task_id,
                step_id=step_id,
            )
            return result

        operation_type = skill.operation_type_for(
            validated_arguments.model_dump(mode="python")
        )
        max_attempts = (
            skill.retry_policy.max_attempts
            if operation_type == SkillOperationType.READ
            else 1
        )
        attempt_count = 0
        skill_result = SkillResult(success=False)
        for attempt_count in range(1, max_attempts + 1):
            try:
                skill_result = skill.execute(validated_arguments)
            except Exception:
                skill_result = SkillResult(
                    success=False,
                    error_code="SKILL_EXECUTION_FAILED",
                    message="The skill failed unexpectedly.",
                )
            if skill_result.success or attempt_count == max_attempts:
                break
            if skill.retry_policy.backoff_seconds:
                sleep(skill.retry_policy.backoff_seconds)

        if skill_result.success:
            try:
                skill_result.data = skill.validate_output(skill_result.data)
            except ValidationError:
                skill_result = SkillResult(
                    success=False,
                    error_code="INVALID_SKILL_OUTPUT",
                    message="The skill output did not match its schema.",
                )

        verification = VerificationResult(status=VerificationStatus.VERIFIED)
        if skill_result.success:
            try:
                verification = skill.verify(
                    validated_arguments,
                    skill_result.data,
                )
            except Exception:
                verification = VerificationResult(
                    status=VerificationStatus.UNKNOWN
                )

        result = self._result(
            request_id=execution_id,
            skill_name=skill_name,
            started_at=started_at,
            result=skill_result,
            verification=verification,
            attempt_count=attempt_count,
        )
        self._emit_result(result, task_id=task_id, step_id=step_id)
        return result

    def operation_type_for(
        self,
        skill_name: str,
        arguments: Mapping[str, Any],
    ):
        return self._registry.get(skill_name).operation_type_for(arguments)

    def timeout_seconds_for(self, skill_name: str) -> float:
        return self._registry.get(skill_name).timeout_seconds

    def concurrency_limit_for(self, skill_name: str) -> int:
        return self._registry.get(skill_name).concurrency_limit

    def verify(
        self,
        skill_name: str,
        arguments: Mapping[str, Any],
        output: Any,
    ) -> VerificationResult:
        skill = self._registry.get(skill_name)
        validated_arguments = skill.validate_input(arguments)
        return skill.verify(validated_arguments, output)

    def _emit_result(
        self,
        result: RuntimeResult,
        *,
        task_id: str | None = None,
        step_id: str | None = None,
    ) -> None:
        event_type = (
            TraceEventType.SKILL_SUCCEEDED
            if result.success
            else TraceEventType.SKILL_FAILED
        )
        self._emit(
            result.request_id,
            event_type,
            result.skill_name,
            error_code=result.error_code,
            duration_ms=result.duration_ms,
            attempt_count=result.attempt_count,
            verification_status=result.verification_status.value,
            external_resource_id=result.external_resource_id,
            task_id=task_id,
            step_id=step_id,
            **result.trace_attributes,
        )

    def _emit(
        self,
        request_id: str,
        event_type: TraceEventType,
        skill_name: str,
        task_id: str | None = None,
        step_id: str | None = None,
        **attributes: Any,
    ) -> None:
        clean_attributes = {
            key: value for key, value in attributes.items() if value is not None
        }
        self._trace_sink.emit(
            TraceEvent(
                request_id=request_id,
                event_type=event_type,
                task_id=task_id,
                step_id=step_id,
                skill_name=skill_name,
                attributes=clean_attributes,
            )
        )

    @staticmethod
    def _result(
        *,
        request_id: str,
        skill_name: str,
        started_at: float,
        result: SkillResult,
        verification: VerificationResult | None = None,
        attempt_count: int = 1,
    ) -> RuntimeResult:
        verification = verification or VerificationResult(
            status=VerificationStatus.VERIFIED
        )
        return RuntimeResult(
            request_id=request_id,
            skill_name=skill_name,
            success=result.success,
            data=result.data,
            error_code=result.error_code,
            message=result.message,
            duration_ms=(perf_counter() - started_at) * 1000,
            verification_status=verification.status,
            external_resource_id=verification.external_resource_id,
            verification_data=verification.data,
            attempt_count=attempt_count,
            trace_attributes=result.trace_attributes,
        )

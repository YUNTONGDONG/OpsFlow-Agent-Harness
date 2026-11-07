"""Explicit task and step state for multi-skill Harness execution."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from .output_binding import iter_output_references
from .workspace import WorkspaceArtifactRef


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


class TaskStep(BaseModel):
    step_id: str = Field(min_length=1)
    skill_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    resolved_arguments: dict[str, Any] | None = None
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result: dict[str, Any] | None = None
    error_code: str | None = None
    idempotency_key: str | None = None
    external_resource_id: str | None = None
    verification_status: str | None = None
    attempt_count: int = 0
    input_ref: WorkspaceArtifactRef | None = None
    output_ref: WorkspaceArtifactRef | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TaskState(BaseModel):
    task_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    status: TaskStatus = TaskStatus.PENDING
    steps: list[TaskStep] = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    request_ref: WorkspaceArtifactRef | None = None
    plan_ref: WorkspaceArtifactRef | None = None
    response_ref: WorkspaceArtifactRef | None = None

    @model_validator(mode="after")
    def validate_step_graph(self) -> "TaskState":
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Task step IDs must be unique")

        known_steps = set(step_ids)
        for step in self.steps:
            unknown_dependencies = set(step.depends_on) - known_steps
            if unknown_dependencies:
                names = ", ".join(sorted(unknown_dependencies))
                raise ValueError(
                    f"Step {step.step_id} has unknown dependencies: {names}"
                )
            if step.step_id in step.depends_on:
                raise ValueError(f"Step {step.step_id} cannot depend on itself")

        self._validate_acyclic_dependencies()
        self._validate_output_references()
        return self

    def _validate_acyclic_dependencies(self) -> None:
        dependencies = {
            step.step_id: set(step.depends_on) for step in self.steps
        }
        resolved: set[str] = set()

        while len(resolved) < len(dependencies):
            ready = {
                step_id
                for step_id, required in dependencies.items()
                if step_id not in resolved and required <= resolved
            }
            if not ready:
                raise ValueError("Task step dependencies contain a cycle")
            resolved.update(ready)

    def _validate_output_references(self) -> None:
        dependencies = {
            step.step_id: set(step.depends_on) for step in self.steps
        }

        def ancestors(step_id: str) -> set[str]:
            result: set[str] = set()
            pending = list(dependencies[step_id])
            while pending:
                dependency = pending.pop()
                if dependency in result:
                    continue
                result.add(dependency)
                pending.extend(dependencies[dependency])
            return result

        for step in self.steps:
            allowed_sources = ancestors(step.step_id)
            for reference in iter_output_references(step.arguments):
                if reference.step_id not in allowed_sources:
                    raise ValueError(
                        f"Step {step.step_id} references non-dependent output: "
                        f"{reference.step_id}"
                    )

    def start_step(
        self,
        step_id: str,
        resolved_arguments: dict[str, Any] | None = None,
    ) -> None:
        step = self._get_step(step_id)
        if self.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.UNKNOWN,
            TaskStatus.CANCELLED,
        }:
            raise ValueError(f"Cannot start a step in {self.status.value} task")
        if step.status != StepStatus.PENDING:
            raise ValueError(
                f"Step {step_id} must be pending before it can start"
            )

        completed = {
            item.step_id
            for item in self.steps
            if item.status == StepStatus.COMPLETED
        }
        missing = set(step.depends_on) - completed
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"Step {step_id} is waiting for: {names}")

        step.status = StepStatus.RUNNING
        step.resolved_arguments = resolved_arguments
        step.attempt_count += 1
        step.started_at = utc_now()
        self.status = TaskStatus.RUNNING
        self._touch()

    def mark_step_unknown(
        self,
        step_id: str,
        *,
        result: dict[str, Any] | None = None,
        external_resource_id: str | None = None,
        error_code: str = "EXTERNAL_STATE_UNKNOWN",
    ) -> None:
        step = self._get_step(step_id)
        if step.status != StepStatus.RUNNING:
            raise ValueError(
                f"Step {step_id} must be running before becoming unknown"
            )
        step.status = StepStatus.UNKNOWN
        step.result = result
        step.external_resource_id = external_resource_id
        step.verification_status = "unknown"
        step.error_code = error_code
        step.completed_at = utc_now()
        self.status = TaskStatus.UNKNOWN
        self._touch()

    def resolve_unknown_step(
        self,
        step_id: str,
        result: dict[str, Any],
        external_resource_id: str | None = None,
    ) -> None:
        step = self._get_step(step_id)
        if step.status != StepStatus.UNKNOWN:
            raise ValueError(f"Step {step_id} is not unknown")
        step.status = StepStatus.COMPLETED
        step.result = result
        step.external_resource_id = external_resource_id
        step.verification_status = "verified"
        step.error_code = None
        step.completed_at = utc_now()
        self.status = (
            TaskStatus.COMPLETED
            if all(item.status == StepStatus.COMPLETED for item in self.steps)
            else TaskStatus.RUNNING
        )
        self._touch()

    def reject_unknown_step(
        self,
        step_id: str,
        error_code: str = "EXTERNAL_VERIFICATION_FAILED",
    ) -> None:
        step = self._get_step(step_id)
        if step.status != StepStatus.UNKNOWN:
            raise ValueError(f"Step {step_id} is not unknown")
        step.status = StepStatus.FAILED
        step.verification_status = "rejected"
        step.error_code = error_code
        step.completed_at = utc_now()
        self.status = TaskStatus.FAILED
        self._touch()

    def reset_running_step(self, step_id: str) -> None:
        step = self._get_step(step_id)
        if step.status != StepStatus.RUNNING:
            raise ValueError(f"Step {step_id} is not running")
        step.status = StepStatus.PENDING
        step.started_at = None
        self.status = TaskStatus.PENDING
        self._touch()

    def complete_step(
        self,
        step_id: str,
        result: dict[str, Any],
        *,
        external_resource_id: str | None = None,
    ) -> None:
        step = self._get_step(step_id)
        if step.status != StepStatus.RUNNING:
            raise ValueError(
                f"Step {step_id} must be running before completion"
            )

        step.status = StepStatus.COMPLETED
        step.result = result
        step.external_resource_id = external_resource_id
        step.verification_status = "verified"
        step.completed_at = utc_now()
        if all(item.status == StepStatus.COMPLETED for item in self.steps):
            self.status = TaskStatus.COMPLETED
        self._touch()

    def fail_step(self, step_id: str, error_code: str) -> None:
        step = self._get_step(step_id)
        if step.status != StepStatus.RUNNING:
            raise ValueError(f"Step {step_id} must be running before failure")

        step.status = StepStatus.FAILED
        step.error_code = error_code
        step.completed_at = utc_now()
        self.status = TaskStatus.FAILED
        self._touch()

    def ready_steps(self) -> list[TaskStep]:
        completed = {
            step.step_id
            for step in self.steps
            if step.status == StepStatus.COMPLETED
        }
        return [
            step
            for step in self.steps
            if step.status == StepStatus.PENDING
            and set(step.depends_on) <= completed
        ]

    def _get_step(self, step_id: str) -> TaskStep:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise KeyError(f"Unknown task step: {step_id}")

    def _touch(self) -> None:
        self.updated_at = utc_now()

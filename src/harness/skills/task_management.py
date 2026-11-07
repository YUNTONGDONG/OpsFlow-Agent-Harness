"""Google Tasks skill backed by the existing task tool."""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from ..skill import (
    BaseSkill,
    RetryPolicy,
    SkillOperationType,
    SkillResult,
    VerificationResult,
)


class TaskManagementInput(BaseModel):
    subject: str = Field(min_length=1, max_length=1024)
    due_time: str | None = None
    description: str | None = None
    priority: int | None = Field(default=None, ge=0)


class TaskManagementOutput(BaseModel):
    result: Any


def _create_existing_task(arguments: TaskManagementInput) -> Any:
    from src.Tools import GoogleClient

    client = GoogleClient()
    tasklists = client.tasks_service.tasklists().list().execute()
    tasklist_id = tasklists["items"][0]["id"]
    task = {
        "title": arguments.subject,
        "notes": arguments.description or "",
        "status": "needsAction",
    }
    if arguments.due_time:
        task["due"] = arguments.due_time
    created = client.tasks_service.tasks().insert(
        tasklist=tasklist_id,
        body=task,
    ).execute()
    return {
        "task_id": created["id"],
        "tasklist_id": tasklist_id,
        "title": created.get("title", arguments.subject),
    }


def _verify_existing_task(
    arguments: TaskManagementInput,
    output: Any,
) -> VerificationResult:
    from googleapiclient.errors import HttpError
    from src.Tools import GoogleClient

    result = output.get("result") if isinstance(output, dict) else None
    if not isinstance(result, dict):
        return VerificationResult(status="unknown")
    task_id = result.get("task_id")
    tasklist_id = result.get("tasklist_id")
    if not task_id or not tasklist_id:
        return VerificationResult(status="unknown")
    try:
        task = GoogleClient().tasks_service.tasks().get(
            tasklist=tasklist_id,
            task=task_id,
        ).execute()
    except HttpError as exc:
        if exc.resp.status in {404, 410}:
            return VerificationResult(status="rejected")
        return VerificationResult(status="unknown")
    except Exception:
        return VerificationResult(status="unknown")
    if task.get("title") != arguments.subject:
        return VerificationResult(status="rejected")
    return VerificationResult(
        status="verified",
        external_resource_id=task_id,
        data={"tasklist_id": tasklist_id},
    )


class TaskManagementSkill(BaseSkill[TaskManagementInput]):
    name = "task_management"
    description = "Create a Google Tasks item from a validated task request."
    input_schema = TaskManagementInput
    output_schema = TaskManagementOutput
    operation_type = SkillOperationType.NON_IDEMPOTENT_WRITE
    retry_policy = RetryPolicy(max_attempts=1)
    timeout_seconds = 20
    concurrency_limit = 1

    def __init__(
        self,
        create_fn: Callable[[TaskManagementInput], Any] | None = None,
        verify_fn: Callable[
            [TaskManagementInput, Any], VerificationResult
        ]
        | None = None,
    ) -> None:
        self._create_fn = create_fn or _create_existing_task
        self._verify_fn = (
            verify_fn
            if verify_fn is not None
            else (_verify_existing_task if create_fn is None else None)
        )

    def execute(self, arguments: TaskManagementInput) -> SkillResult:
        try:
            result = self._create_fn(arguments)
        except Exception:
            return SkillResult(
                success=False,
                error_code="TASK_CREATE_FAILED",
                message="The task could not be created.",
            )

        if isinstance(result, str) and "失败" in result:
            return SkillResult(
                success=False,
                error_code="TASK_CREATE_FAILED",
                message="The task provider rejected the operation.",
            )
        return SkillResult(success=True, data={"result": result})

    def verify(
        self,
        arguments: TaskManagementInput,
        output: Any,
    ) -> VerificationResult:
        if self._verify_fn is not None:
            return self._verify_fn(arguments, output)
        return super().verify(arguments, output)

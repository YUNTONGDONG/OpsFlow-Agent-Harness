"""Top-level request orchestration for the Agent Harness."""

from enum import Enum
from time import perf_counter
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from .registry import SkillRegistry
from .idempotency import IdempotencyStore
from .output_binding import iter_output_references, schema_contains_path
from .router import RequestRouter, RouteDecision
from .runtime import SkillRuntime
from .task_runner import TaskRunner
from .task_state import TaskState, TaskStatus, TaskStep
from .task_store import TaskStore
from .trace import NullTraceSink, TraceEvent, TraceEventType, TraceSink
from .workspace import InMemoryWorkspace, Workspace


class HarnessResponseStatus(str, Enum):
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"


class HarnessResponse(BaseModel):
    request_id: str
    status: HarnessResponseStatus
    data: object | None = None
    error_code: str | None = None
    task_id: str | None = None


class HarnessApplication:
    def __init__(
        self,
        *,
        registry: SkillRegistry,
        router: RequestRouter,
        runtime: SkillRuntime,
        task_store: TaskStore,
        trace_sink: TraceSink | None = None,
        max_concurrency: int = 4,
        idempotency_store: IdempotencyStore | None = None,
        workspace: Workspace | None = None,
        inline_result_max_bytes: int = 32_768,
        model_name: str | None = None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._runtime = runtime
        self._task_store = task_store
        self._trace_sink = trace_sink or NullTraceSink()
        self._workspace = workspace or InMemoryWorkspace()
        self._model_name = model_name
        self._task_runner = TaskRunner(
            runtime,
            task_store,
            max_concurrency=max_concurrency,
            idempotency_store=idempotency_store,
            workspace=self._workspace,
            inline_result_max_bytes=inline_result_max_bytes,
        )

    def handle(
        self,
        request: str,
        *,
        request_id: str | None = None,
        context: list[dict[str, str]] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> HarnessResponse:
        execution_id = request_id or str(uuid4())
        started_at = perf_counter()
        self._emit(
            execution_id,
            TraceEventType.REQUEST_STARTED,
            {
                "context_message_count": len(context or []),
                "model": self._model_name,
            },
            session_id=session_id,
            user_id=user_id,
        )
        self._emit(
            execution_id,
            TraceEventType.ROUTE_STARTED,
            session_id=session_id,
            user_id=user_id,
        )

        try:
            decision = self._router.route(
                request,
                self._registry.list_skills(include_schema=True),
                context,
            )
            self._validate_registered_skills(decision)
            self._validate_output_bindings(decision)
        except (KeyError, ValidationError, ValueError):
            self._emit(
                execution_id,
                TraceEventType.ROUTE_COMPLETED,
                {"success": False, "error_code": "INVALID_ROUTE_PLAN"},
            )
            return self._finish_response(HarnessResponse(
                request_id=execution_id,
                status=HarnessResponseStatus.FAILED,
                error_code="INVALID_ROUTE_PLAN",
            ), started_at, session_id=session_id, user_id=user_id)

        if decision.clarification is not None:
            self._emit(
                execution_id,
                TraceEventType.ROUTE_COMPLETED,
                {
                    "success": True,
                    "requires_clarification": True,
                    "missing_fields": decision.clarification.missing_fields,
                },
            )
            return self._finish_response(HarnessResponse(
                request_id=execution_id,
                status=HarnessResponseStatus.NEEDS_INPUT,
                data=decision.clarification.model_dump(mode="json"),
            ), started_at, session_id=session_id, user_id=user_id)

        self._emit(
            execution_id,
            TraceEventType.ROUTE_COMPLETED,
            {
                "success": True,
                "step_count": len(decision.steps),
                "steps": [
                    {
                        "step_id": step.step_id,
                        "skill_name": step.skill_name,
                        "depends_on": step.depends_on,
                    }
                    for step in decision.steps
                ],
            },
        )

        task_id = str(uuid4())
        try:
            task = TaskState(
                task_id=task_id,
                request_id=execution_id,
                steps=[
                    TaskStep(
                        step_id=step.step_id,
                        skill_name=step.skill_name,
                        arguments=step.arguments,
                        depends_on=step.depends_on,
                    )
                    for step in decision.steps
                ],
            )
        except ValidationError:
            return self._finish_response(HarnessResponse(
                request_id=execution_id,
                status=HarnessResponseStatus.FAILED,
                error_code="INVALID_TASK_PLAN",
            ), started_at, session_id=session_id, user_id=user_id)

        task.request_ref = self._workspace.write_json(
            task_id,
            "request.json",
            {"request_id": execution_id, "request": request, "context": context or []},
        )
        task.plan_ref = self._workspace.write_json(
            task_id,
            "plan.json",
            decision.model_dump(mode="json"),
        )

        self._task_store.create(task)
        self._emit(
            execution_id,
            TraceEventType.TASK_CREATED,
            {
                "step_count": len(task.steps),
                "request_ref": task.request_ref.model_dump(mode="json"),
                "plan_ref": task.plan_ref.model_dump(mode="json"),
            },
            task_id=task_id,
        )
        completed_task = self._task_runner.run(task_id)
        self._emit(
            execution_id,
            TraceEventType.TASK_COMPLETED,
            {
                "status": completed_task.status.value,
                "steps": [self._step_trace_summary(step) for step in completed_task.steps],
            },
            task_id=task_id,
        )

        response = HarnessResponse(
            request_id=execution_id,
            task_id=task_id,
            status=(
                HarnessResponseStatus.COMPLETED
                if completed_task.status == TaskStatus.COMPLETED
                else HarnessResponseStatus.FAILED
            ),
            data={
                "steps": [step.model_dump(mode="json") for step in completed_task.steps]
            },
            error_code=(
                None
                if completed_task.status == TaskStatus.COMPLETED
                else next(
                    (
                        step.error_code
                        for step in completed_task.steps
                        if step.error_code
                    ),
                    (
                        "EXTERNAL_STATE_UNKNOWN"
                        if completed_task.status == TaskStatus.UNKNOWN
                        else "TASK_FAILED"
                    ),
                )
            ),
        )
        completed_task.response_ref = self._workspace.write_json(
            task_id,
            "response.json",
            response.model_dump(mode="json"),
        )
        self._task_store.save(completed_task)
        return self._finish_response(
            response,
            started_at,
            task=completed_task,
            session_id=session_id,
            user_id=user_id,
        )

    def recover_task(self, task_id: str) -> TaskState:
        task = self._task_store.get(task_id)
        self._emit(
            task.request_id,
            TraceEventType.RECOVERY_STARTED,
            {"status": task.status.value},
            task_id=task_id,
        )
        recovered = self._task_runner.recover(task_id)
        self._emit(
            task.request_id,
            TraceEventType.RECOVERY_COMPLETED,
            {
                "status": recovered.status.value,
                "steps": [self._step_trace_summary(step) for step in recovered.steps],
            },
            task_id=task_id,
        )
        return recovered

    def recover_unfinished_tasks(self) -> list[TaskState]:
        return [
            self.recover_task(task.task_id)
            for task in self._task_store.list_unfinished()
        ]

    def _validate_registered_skills(self, decision: RouteDecision) -> None:
        registered = {
            item["name"] for item in self._registry.list_skills()
        }
        unknown = {
            step.skill_name
            for step in decision.steps
            if step.skill_name not in registered
        }
        if unknown:
            raise KeyError(f"Unknown routed skills: {sorted(unknown)}")

    def _validate_output_bindings(self, decision: RouteDecision) -> None:
        steps_by_id = {step.step_id: step for step in decision.steps}
        for step in decision.steps:
            for reference in iter_output_references(step.arguments):
                source_step = steps_by_id.get(reference.step_id)
                if source_step is None:
                    raise ValueError(
                        f"Unknown output reference step: {reference.step_id}"
                    )
                source_skill = self._registry.get(source_step.skill_name)
                if source_skill.output_schema is None:
                    continue
                schema = source_skill.output_schema.model_json_schema()
                if not schema_contains_path(schema, reference.path):
                    raise ValueError(
                        f"Unknown output field: {reference.expression}"
                    )

    def _emit(
        self,
        request_id: str,
        event_type: TraceEventType,
        attributes: dict[str, object] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        task_id: str | None = None,
    ) -> None:
        self._trace_sink.emit(
            TraceEvent(
                request_id=request_id,
                event_type=event_type,
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                attributes=attributes or {},
            )
        )

    def _finish_response(
        self,
        response: HarnessResponse,
        started_at: float,
        *,
        task: TaskState | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> HarnessResponse:
        self._emit(
            response.request_id,
            TraceEventType.REQUEST_COMPLETED,
            {
                "status": response.status.value,
                "error_code": response.error_code,
                "duration_ms": (perf_counter() - started_at) * 1000,
                "response_ref": (
                    task.response_ref.model_dump(mode="json")
                    if task is not None and task.response_ref is not None
                    else None
                ),
            },
            session_id=session_id,
            user_id=user_id,
            task_id=response.task_id,
        )
        return response

    @staticmethod
    def _step_trace_summary(step: TaskStep) -> dict[str, object]:
        return {
            "step_id": step.step_id,
            "skill_name": step.skill_name,
            "status": step.status.value,
            "started_at": step.started_at.isoformat() if step.started_at else None,
            "completed_at": (
                step.completed_at.isoformat() if step.completed_at else None
            ),
            "attempt_count": step.attempt_count,
            "error_code": step.error_code,
            "verification_status": step.verification_status,
            "external_resource_id": step.external_resource_id,
            "input_ref": (
                step.input_ref.model_dump(mode="json") if step.input_ref else None
            ),
            "output_ref": (
                step.output_ref.model_dump(mode="json") if step.output_ref else None
            ),
        }

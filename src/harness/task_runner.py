"""Dependency-aware task execution backed by SkillRuntime and TaskStore."""

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from hashlib import sha256
import json
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import Any

from .runtime import SkillRuntime
from .idempotency import (
    IdempotencyRecord,
    IdempotencyStatus,
    IdempotencyStore,
    InMemoryIdempotencyStore,
)
from .output_binding import OutputBindingError, resolve_output_references
from .runtime import RuntimeResult
from .skill import SkillOperationType, VerificationStatus
from .task_state import StepStatus, TaskState, TaskStatus
from .task_store import TaskStore
from .workspace import (
    InMemoryWorkspace,
    Workspace,
    WorkspaceArtifactRef,
)


class TaskRunner:
    def __init__(
        self,
        runtime: SkillRuntime,
        task_store: TaskStore,
        *,
        max_concurrency: int = 4,
        idempotency_store: IdempotencyStore | None = None,
        workspace: Workspace | None = None,
        inline_result_max_bytes: int = 32_768,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        self._runtime = runtime
        self._task_store = task_store
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="harness-skill",
        )
        self._skill_semaphores: dict[str, BoundedSemaphore] = {}
        self._semaphore_lock = Lock()
        self._idempotency_store = (
            idempotency_store or InMemoryIdempotencyStore()
        )
        self._workspace = workspace or InMemoryWorkspace()
        self._inline_result_max_bytes = inline_result_max_bytes

    def run(self, task_id: str) -> TaskState:
        task = self._task_store.get(task_id)
        if task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.UNKNOWN,
            TaskStatus.CANCELLED,
        }:
            return task

        while task.status not in {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.UNKNOWN,
            TaskStatus.CANCELLED,
        }:
            ready_steps = task.ready_steps()
            if not ready_steps:
                raise RuntimeError(
                    "Task has no executable steps; it may contain an interrupted "
                    "running step"
                )

            prepared_steps = []
            for step in ready_steps:
                completed_outputs = {
                    item.step_id: self._load_step_output(item)
                    for item in task.steps
                    if item.status.value == "completed" and item.result is not None
                }
                try:
                    resolved_arguments = resolve_output_references(
                        step.arguments,
                        completed_outputs,
                    )
                except OutputBindingError:
                    task.start_step(step.step_id)
                    task.fail_step(step.step_id, "OUTPUT_BINDING_FAILED")
                    self._task_store.save(task)
                    return task

                prepared_steps.append((step, resolved_arguments))

            parallel_reads = [
                item
                for item in prepared_steps
                if self._runtime.operation_type_for(
                    item[0].skill_name,
                    item[1],
                )
                == SkillOperationType.READ
            ]
            serial_steps = [
                item for item in prepared_steps if item not in parallel_reads
            ]

            if len(parallel_reads) > 1:
                self._run_parallel_reads(task, parallel_reads)
                if task.status in {TaskStatus.FAILED, TaskStatus.UNKNOWN}:
                    return task
            else:
                serial_steps = parallel_reads + serial_steps

            for step, resolved_arguments in serial_steps:
                self._run_serial_step(task, step.step_id, step.skill_name, resolved_arguments)
                if task.status in {TaskStatus.FAILED, TaskStatus.UNKNOWN}:
                    return task

        return task

    def recover(self, task_id: str) -> TaskState:
        """Reconcile interrupted or unknown steps without repeating writes."""

        task = self._task_store.get(task_id)
        for step in task.steps:
            arguments = step.resolved_arguments or step.arguments
            if step.status == StepStatus.RUNNING:
                operation_type = self._runtime.operation_type_for(
                    step.skill_name,
                    arguments,
                )
                if operation_type == SkillOperationType.READ:
                    task.reset_running_step(step.step_id)
                    continue
                record = (
                    self._idempotency_store.get(step.idempotency_key)
                    if step.idempotency_key
                    else None
                )
                if record is not None and record.status == IdempotencyStatus.VERIFIED:
                    task.complete_step(
                        step.step_id,
                        record.result or {"data": None, "duration_ms": 0},
                        external_resource_id=record.external_resource_id,
                    )
                else:
                    task.mark_step_unknown(
                        step.step_id,
                        result=record.result if record else None,
                        external_resource_id=(
                            record.external_resource_id if record else None
                        ),
                    )

        for step in task.steps:
            if step.status != StepStatus.UNKNOWN or step.result is None:
                continue
            arguments = step.resolved_arguments or step.arguments
            verification = self._runtime.verify(
                step.skill_name,
                arguments,
                self._load_step_output(step),
            )
            if verification.status == VerificationStatus.VERIFIED:
                task.resolve_unknown_step(
                    step.step_id,
                    step.result,
                    verification.external_resource_id,
                )
                if step.idempotency_key:
                    self._idempotency_store.save(
                        IdempotencyRecord(
                            key=step.idempotency_key,
                            status=IdempotencyStatus.VERIFIED,
                            result=step.result,
                            external_resource_id=verification.external_resource_id,
                        )
                    )
            elif verification.status == VerificationStatus.REJECTED:
                task.reject_unknown_step(step.step_id)

        self._task_store.save(task)
        if task.status in {TaskStatus.PENDING, TaskStatus.RUNNING}:
            return self.run(task_id)
        return task

    def recover_all(self) -> list[TaskState]:
        return [
            self.recover(task.task_id)
            for task in self._task_store.list_unfinished()
        ]

    def _run_serial_step(
        self,
        task: TaskState,
        step_id: str,
        skill_name: str,
        resolved_arguments: dict[str, Any],
    ) -> None:
        operation_type = self._runtime.operation_type_for(
            skill_name,
            resolved_arguments,
        )
        if operation_type != SkillOperationType.READ:
            self._run_idempotent_write_step(
                task,
                step_id,
                skill_name,
                resolved_arguments,
            )
            return

        task.start_step(step_id, resolved_arguments)
        step = next(item for item in task.steps if item.step_id == step_id)
        step.input_ref = self._workspace.write_json(
            task.task_id,
            f"steps/{step_id}/input.json",
            resolved_arguments,
        )
        self._task_store.save(task)

        result = self._runtime.execute(
            skill_name,
            resolved_arguments,
            request_id=task.request_id,
            task_id=task.task_id,
            step_id=step_id,
            trace_attributes={
                "input_ref": step.input_ref.model_dump(mode="json"),
            },
        )
        self._apply_result(task, step_id, result)
        self._task_store.save(task)

    def _run_idempotent_write_step(
        self,
        task: TaskState,
        step_id: str,
        skill_name: str,
        resolved_arguments: dict[str, Any],
    ) -> None:
        step = next(item for item in task.steps if item.step_id == step_id)
        if step.idempotency_key is None:
            step.idempotency_key = self._idempotency_key(
                task.task_id,
                step_id,
                skill_name,
                resolved_arguments,
            )
        existing = self._idempotency_store.get(step.idempotency_key)
        task.start_step(step_id, resolved_arguments)
        step.input_ref = self._workspace.write_json(
            task.task_id,
            f"steps/{step_id}/input.json",
            resolved_arguments,
        )
        self._task_store.save(task)

        if existing is not None and existing.status == IdempotencyStatus.VERIFIED:
            task.complete_step(
                step_id,
                existing.result or {"data": None, "duration_ms": 0},
                external_resource_id=existing.external_resource_id,
            )
            self._task_store.save(task)
            return
        if existing is not None and existing.status in {
            IdempotencyStatus.IN_PROGRESS,
            IdempotencyStatus.UNKNOWN,
        }:
            task.mark_step_unknown(
                step_id,
                result=existing.result,
                external_resource_id=existing.external_resource_id,
            )
            self._task_store.save(task)
            return

        self._idempotency_store.save(
            IdempotencyRecord(
                key=step.idempotency_key,
                status=IdempotencyStatus.IN_PROGRESS,
            )
        )
        result = self._runtime.execute(
            skill_name,
            resolved_arguments,
            request_id=task.request_id,
            task_id=task.task_id,
            step_id=step_id,
            trace_attributes={
                "input_ref": step.input_ref.model_dump(mode="json"),
                "idempotency_key": step.idempotency_key,
            },
        )
        result_payload = self._result_payload(task, step_id, result)

        if result.success and result.verification_status == VerificationStatus.VERIFIED:
            self._idempotency_store.save(
                IdempotencyRecord(
                    key=step.idempotency_key,
                    status=IdempotencyStatus.VERIFIED,
                    result=result_payload,
                    external_resource_id=result.external_resource_id,
                )
            )
            task.complete_step(
                step_id,
                result_payload,
                external_resource_id=result.external_resource_id,
            )
        elif result.success and result.verification_status == VerificationStatus.UNKNOWN:
            self._idempotency_store.save(
                IdempotencyRecord(
                    key=step.idempotency_key,
                    status=IdempotencyStatus.UNKNOWN,
                    result=result_payload,
                    external_resource_id=result.external_resource_id,
                )
            )
            task.mark_step_unknown(
                step_id,
                result=result_payload,
                external_resource_id=result.external_resource_id,
            )
        else:
            self._idempotency_store.save(
                IdempotencyRecord(
                    key=step.idempotency_key,
                    status=IdempotencyStatus.FAILED,
                    result=result_payload,
                    external_resource_id=result.external_resource_id,
                )
            )
            task.fail_step(
                step_id,
                result.error_code or "EXTERNAL_VERIFICATION_FAILED",
            )
        self._task_store.save(task)

    def _run_parallel_reads(
        self,
        task: TaskState,
        prepared_steps: list[tuple[Any, dict[str, Any]]],
    ) -> None:
        futures: dict[Future[RuntimeResult], tuple[Any, float]] = {}
        for step, resolved_arguments in prepared_steps:
            task.start_step(step.step_id, resolved_arguments)
            step.input_ref = self._workspace.write_json(
                task.task_id,
                f"steps/{step.step_id}/input.json",
                resolved_arguments,
            )
            timeout = self._runtime.timeout_seconds_for(step.skill_name)
            future = self._executor.submit(
                self._execute_with_skill_limit,
                step.skill_name,
                resolved_arguments,
                task.request_id,
                task.task_id,
                step.step_id,
                step.input_ref.model_dump(mode="json"),
            )
            futures[future] = (step, monotonic() + timeout)
        self._task_store.save(task)

        results: dict[str, RuntimeResult | None] = {}
        pending = set(futures)
        while pending:
            now = monotonic()
            expired = [future for future in pending if futures[future][1] <= now]
            for future in expired:
                step, _ = futures[future]
                future.cancel()
                results[step.step_id] = None
                pending.remove(future)
            if not pending:
                break

            next_deadline = min(futures[future][1] for future in pending)
            completed, _ = wait(
                pending,
                timeout=max(0, next_deadline - monotonic()),
                return_when=FIRST_COMPLETED,
            )
            for future in completed:
                step, _ = futures[future]
                results[step.step_id] = future.result()
                pending.remove(future)

        for step, _ in prepared_steps:
            result = results[step.step_id]
            if result is not None and result.success:
                self._apply_result(task, step.step_id, result)
        for step, _ in prepared_steps:
            result = results[step.step_id]
            if result is None:
                task.fail_step(step.step_id, "SKILL_TIMEOUT")
            elif not result.success:
                self._apply_result(task, step.step_id, result)
        self._task_store.save(task)

    def _execute_with_skill_limit(
        self,
        skill_name: str,
        arguments: dict[str, Any],
        request_id: str,
        task_id: str,
        step_id: str,
        input_ref: dict[str, Any],
    ) -> RuntimeResult:
        semaphore = self._semaphore_for(skill_name)
        with semaphore:
            return self._runtime.execute(
                skill_name,
                arguments,
                request_id=request_id,
                task_id=task_id,
                step_id=step_id,
                trace_attributes={"input_ref": input_ref},
            )

    def _semaphore_for(self, skill_name: str) -> BoundedSemaphore:
        with self._semaphore_lock:
            semaphore = self._skill_semaphores.get(skill_name)
            if semaphore is None:
                semaphore = BoundedSemaphore(
                    self._runtime.concurrency_limit_for(skill_name)
                )
                self._skill_semaphores[skill_name] = semaphore
            return semaphore

    def _apply_result(
        self,
        task: TaskState,
        step_id: str,
        result: RuntimeResult,
    ) -> None:
        if result.success and result.verification_status == VerificationStatus.VERIFIED:
            task.complete_step(
                step_id,
                self._result_payload(
                    task,
                    step_id,
                    result,
                ),
                external_resource_id=result.external_resource_id,
            )
            return
        if result.success and result.verification_status == VerificationStatus.UNKNOWN:
            task.mark_step_unknown(
                step_id,
                result=self._result_payload(
                    task,
                    step_id,
                    result,
                ),
                external_resource_id=result.external_resource_id,
            )
            return
        task.fail_step(
            step_id,
            result.error_code or "SKILL_EXECUTION_FAILED",
        )

    @staticmethod
    def _idempotency_key(
        task_id: str,
        step_id: str,
        skill_name: str,
        arguments: dict[str, Any],
    ) -> str:
        payload = json.dumps(
            {
                "task_id": task_id,
                "step_id": step_id,
                "skill_name": skill_name,
                "arguments": arguments,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def _result_payload(
        self,
        task: TaskState,
        step_id: str,
        result: RuntimeResult,
    ) -> dict[str, Any]:
        reference = self._workspace.write_json(
            task.task_id,
            f"steps/{step_id}/output.json",
            result.data,
        )
        step = next(item for item in task.steps if item.step_id == step_id)
        step.output_ref = reference
        inline_data = (
            result.data
            if reference.size_bytes <= self._inline_result_max_bytes
            else None
        )
        return {
            "data": inline_data,
            "workspace_ref": reference.model_dump(mode="json"),
            "duration_ms": result.duration_ms,
            "attempt_count": result.attempt_count,
        }

    def _load_step_output(self, step: Any) -> Any:
        if step.result is not None and step.result.get("data") is not None:
            return step.result["data"]
        raw_reference = (
            step.result.get("workspace_ref") if step.result is not None else None
        )
        reference = step.output_ref or (
            WorkspaceArtifactRef.model_validate(raw_reference)
            if raw_reference is not None
            else None
        )
        if reference is None:
            raise OutputBindingError(
                f"Completed step has no readable output: {step.step_id}"
            )
        return self._workspace.read_json(reference)


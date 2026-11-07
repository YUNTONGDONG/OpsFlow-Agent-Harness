"""Credential-free deterministic environment for the five Harness demos."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from time import monotonic, sleep
from typing import Any, Sequence
from uuid import uuid4

from .HarnessAgent import HarnessAgent
from .harness import (
    CallableRouter,
    HarnessApplication,
    InMemoryIdempotencyStore,
    InMemorySessionStore,
    InMemoryTaskStore,
    InMemoryTraceSink,
    LocalWorkspace,
    RouteDecision,
    RouteStep,
    SkillRegistry,
    SkillRuntime,
    TaskState,
    TaskStep,
    VerificationResult,
)
from .harness.rag import (
    CallableEvidenceAnswerer,
    RetrievalDocument,
    ThreeStageRAGPipeline,
)
from .harness.skills import (
    CalendarAction,
    CalendarManagementSkill,
    KnowledgeSearchSkill,
    LogDiagnosisSkill,
    TaskManagementSkill,
)


class DemoRetriever:
    def retrieve(self, query: str, limit: int) -> list[RetrievalDocument]:
        documents = [
            RetrievalDocument(
                chunk_id="runbook-e102-1",
                source="runbooks/payment-worker.md",
                content="E102 means the payment worker lease expired. Restart the worker.",
                score=1.0,
            ),
            RetrievalDocument(
                chunk_id="runbook-e102-2",
                source="runbooks/payment-worker.md",
                content="After restart, verify the queue lag returns below 100 messages.",
                score=0.9,
            ),
            RetrievalDocument(
                chunk_id="incident-e102-1",
                source="incidents/2026-08-e102.md",
                content="Escalate if E102 repeats three times within ten minutes.",
                score=0.8,
            ),
        ]
        documents.extend(
            RetrievalDocument(
                chunk_id=f"background-{index:03d}",
                source="wiki/background.md",
                content=f"Background engineering note {index}.",
                score=max(0.0, 0.7 - index / 100),
            )
            for index in range(97)
        )
        return documents[:limit]


class DemoRanker:
    def __init__(self, delay_seconds: float = 0.01) -> None:
        self._delay_seconds = delay_seconds

    def rank(
        self,
        query: str,
        documents: Sequence[RetrievalDocument],
        limit: int,
    ) -> list[RetrievalDocument]:
        sleep(self._delay_seconds)
        return sorted(documents, key=lambda item: item.score, reverse=True)[:limit]


class DemoCalendarBackend:
    def __init__(self) -> None:
        self._events: dict[str, dict[str, Any]] = {}
        self._pending_deletes: dict[str, float] = {}
        self._lock = Lock()
        self.reset()

    def reset(self) -> None:
        self._events = {
            "event-project-review": {
                "event_id": "event-project-review",
                "summary": "项目评审会议",
                "start": "2026-11-08T14:00:00-05:00",
                "end": "2026-11-08T15:00:00-05:00",
            }
        }
        self._pending_deletes.clear()

    def __call__(self, arguments) -> Any:
        sleep(0.05)
        with self._lock:
            if arguments.action == CalendarAction.CHECK_AVAILABILITY:
                return {"available": True, "events": list(self._events.values())}
            if arguments.action == CalendarAction.REQUEST_DELETE:
                candidates = [
                    event
                    for event in self._events.values()
                    if arguments.summary in event["summary"]
                    or event["summary"] in arguments.summary
                ]
                for event in candidates:
                    self._pending_deletes[event["event_id"]] = monotonic() + 300
                return {
                    "confirmation_required": True,
                    "candidates": candidates,
                    "expires_in_seconds": 300,
                }
            if arguments.action == CalendarAction.CONFIRM_DELETE:
                deadline = self._pending_deletes.pop(arguments.event_id, None)
                if deadline is None or deadline < monotonic():
                    raise ValueError("Delete confirmation is missing or expired")
                removed = self._events.pop(arguments.event_id, None)
                return {"deleted": removed is not None, "event_id": arguments.event_id}
            return {"status": "demo", "action": arguments.action.value}

    def verify(self, arguments, output) -> VerificationResult:
        if arguments.action == CalendarAction.CONFIRM_DELETE:
            status = "verified" if arguments.event_id not in self._events else "rejected"
            return VerificationResult(
                status=status,
                external_resource_id=arguments.event_id,
            )
        return VerificationResult(status="verified")


class DemoRouter:
    def route(self, request, available_skills, context=None) -> RouteDecision:
        normalized = request.casefold()
        if "confirm-delete" in normalized:
            event_id = request.split()[-1]
            return RouteDecision(
                steps=[
                    RouteStep(
                        step_id="delete",
                        skill_name="calendar_management",
                        arguments={"action": "confirm_delete", "event_id": event_id},
                    )
                ]
            )
        if "删除" in request or "delete" in normalized:
            return RouteDecision(
                steps=[
                    RouteStep(
                        step_id="find_event",
                        skill_name="calendar_management",
                        arguments={"action": "request_delete", "summary": "项目评审会议"},
                    )
                ]
            )
        if ("日志" in request or "log" in normalized) and (
            "任务" in request or "task" in normalized
        ):
            return RouteDecision(
                steps=[
                    RouteStep(
                        step_id="diagnose",
                        skill_name="log_diagnosis",
                        arguments={
                            "action": "query",
                            "service": "payment-service",
                            "environment": "demo",
                            "start_time": "2026-11-07T11:00:00Z",
                            "end_time": "2026-11-07T12:00:00Z",
                            "keyword": "E102",
                        },
                    ),
                    RouteStep(
                        step_id="create_task",
                        skill_name="task_management",
                        arguments={
                            "subject": {"$from": "diagnose.output.issue_title"},
                            "description": {"$from": "diagnose.output.summary"},
                        },
                        depends_on=["diagnose"],
                    ),
                ]
            )
        if "同时" in request or ("日历" in request and "知识" in request):
            return RouteDecision(
                steps=[
                    RouteStep(
                        step_id="knowledge",
                        skill_name="knowledge_search",
                        arguments={"query": "E102"},
                    ),
                    RouteStep(
                        step_id="calendar",
                        skill_name="calendar_management",
                        arguments={
                            "action": "check_availability",
                            "time_min": "2026-11-08T13:00:00-05:00",
                            "time_max": "2026-11-08T17:00:00-05:00",
                        },
                    ),
                ]
            )
        return RouteDecision(
            steps=[
                RouteStep(
                    step_id="knowledge",
                    skill_name="knowledge_search",
                    arguments={"query": request},
                )
            ]
        )


class DemoEnvironment:
    def __init__(self, root: str | Path = "workspaces/demo") -> None:
        self.trace = InMemoryTraceSink()
        self.task_store = InMemoryTaskStore()
        self.workspace = LocalWorkspace(root)
        self.calendar = DemoCalendarBackend()
        self.created_tasks: dict[str, dict[str, Any]] = {}
        registry = SkillRegistry()
        registry.register(
            KnowledgeSearchSkill(
                pipeline=ThreeStageRAGPipeline(
                    retriever=DemoRetriever(),
                    svm_ranker=DemoRanker(),
                    cross_encoder_ranker=DemoRanker(),
                    answerer=CallableEvidenceAnswerer(
                        lambda query, evidence: (
                            "E102 处理方式：重启 payment worker，确认队列积压低于 "
                            "100；十分钟内重复三次则升级处理 "
                            "[runbook-e102-1] [runbook-e102-2] [incident-e102-1]"
                        )
                    ),
                )
            )
        )
        registry.register(
            LogDiagnosisSkill(
                backend=lambda arguments: {
                    "status": "success",
                    "service": arguments.service,
                    "events": [
                        {
                            "timestamp": "2026-11-07T11:32:00Z",
                            "level": "ERROR",
                            "code": "E102",
                            "message": "worker lease expired",
                        }
                    ],
                }
            )
        )

        def create_task(arguments):
            task_id = f"demo-task-{len(self.created_tasks) + 1}"
            task = {"task_id": task_id, "tasklist_id": "demo-list", "title": arguments.subject}
            self.created_tasks[task_id] = task
            return task

        def verify_task(arguments, output):
            task_id = output["result"]["task_id"]
            return VerificationResult(
                status="verified" if task_id in self.created_tasks else "rejected",
                external_resource_id=task_id,
            )

        registry.register(TaskManagementSkill(create_fn=create_task, verify_fn=verify_task))
        registry.register(
            CalendarManagementSkill(
                backend=self.calendar,
                verify_fn=self.calendar.verify,
            )
        )
        runtime = SkillRuntime(registry, trace_sink=self.trace)
        self.application = HarnessApplication(
            registry=registry,
            router=DemoRouter(),
            runtime=runtime,
            task_store=self.task_store,
            trace_sink=self.trace,
            workspace=self.workspace,
            idempotency_store=InMemoryIdempotencyStore(),
            model_name="deterministic-demo-router",
        )
        self.agent = HarnessAgent(
            application=self.application,
            session_store=InMemorySessionStore(),
        )

    def run_scenario(self, scenario: str) -> dict[str, Any]:
        if scenario == "knowledge":
            responses = [self.application.handle("从内部知识库查一下 E102 错误怎么处理？")]
            boundary = "Deterministic local RAG fixture; no external model call."
        elif scenario == "log_to_task":
            responses = [self.application.handle("检查日志，如果发现 E102，创建修复任务")]
            boundary = "Mock log and task providers with real output binding and verification."
        elif scenario == "parallel_reads":
            responses = [self.application.handle("同时查询知识库和日历")]
            boundary = "Fixed delays prove Harness read-step overlap in local execution."
        elif scenario == "safe_delete":
            self.calendar.reset()
            request = self.application.handle("删除明天下午的项目评审会议")
            confirm = self.application.handle("confirm-delete event-project-review")
            responses = [request, confirm]
            boundary = "Two-phase Mock Calendar flow; not Google Calendar live evidence."
        elif scenario == "recovery":
            task = TaskState(
                task_id=f"interrupted-{uuid4()}",
                request_id=f"recovery-{uuid4()}",
                steps=[
                    TaskStep(
                        step_id="knowledge",
                        skill_name="knowledge_search",
                        arguments={"query": "E102"},
                    )
                ],
            )
            self.task_store.create(task)
            task.start_step("knowledge", {"query": "E102"})
            self.task_store.save(task)
            recovered = self.application.recover_task(task.task_id)
            responses = []
            boundary = "In-memory interrupted read recovery; Redis restart is separate integration evidence."
            return self._scenario_payload(
                scenario,
                [task.request_id],
                [recovered.model_dump(mode="json")],
                boundary,
            )
        else:
            raise KeyError(f"Unknown demo scenario: {scenario}")
        return self._scenario_payload(
            scenario,
            [response.request_id for response in responses],
            [response.model_dump(mode="json") for response in responses],
            boundary,
        )

    def _scenario_payload(
        self,
        scenario: str,
        request_ids: list[str],
        responses: list[dict[str, Any]],
        boundary: str,
    ) -> dict[str, Any]:
        events = [
            event.model_dump(mode="json")
            for request_id in request_ids
            for event in self.trace.events_for(request_id)
        ]
        return {
            "scenario": scenario,
            "request_ids": request_ids,
            "responses": responses,
            "trace": events,
            "evidence_boundary": boundary,
        }


DEMO_SCENARIOS = (
    "knowledge",
    "log_to_task",
    "parallel_reads",
    "safe_delete",
    "recovery",
)

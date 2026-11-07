"""Composition root for a production Harness application."""

import os

from typing import Any

from .application import HarnessApplication
from .idempotency import RedisIdempotencyStore
from .defaults import build_default_registry
from .router import LangChainRequestRouter
from .runtime import SkillRuntime
from .task_store import InMemoryTaskStore, RedisTaskStore
from .trace import NullTraceSink, TraceSink
from .workspace import LocalWorkspace


def build_harness_application(
    *,
    model: Any,
    redis_client: Any | None = None,
    trace_sink: TraceSink | None = None,
    max_concurrency: int | None = None,
) -> HarnessApplication:
    registry = build_default_registry()
    sink = trace_sink or NullTraceSink()
    runtime = SkillRuntime(registry, trace_sink=sink)
    task_store = (
        RedisTaskStore(redis_client)
        if redis_client is not None
        else InMemoryTaskStore()
    )
    return HarnessApplication(
        registry=registry,
        router=LangChainRequestRouter(model),
        runtime=runtime,
        task_store=task_store,
        trace_sink=sink,
        max_concurrency=(
            max_concurrency
            if max_concurrency is not None
            else int(os.getenv("HARNESS_MAX_CONCURRENCY", "4"))
        ),
        idempotency_store=(
            RedisIdempotencyStore(
                redis_client,
                ttl_seconds=int(
                    os.getenv("IDEMPOTENCY_TTL_SECONDS", "604800")
                ),
            )
            if redis_client is not None
            else None
        ),
        workspace=LocalWorkspace(os.getenv("WORKSPACE_ROOT", "workspaces")),
        inline_result_max_bytes=int(
            os.getenv("WORKSPACE_INLINE_MAX_BYTES", "32768")
        ),
        model_name=os.getenv("BASE_MODEL"),
    )

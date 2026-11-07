"""Composition boundary shared by every user-facing Agent adapter."""

import os
from collections.abc import Callable
from typing import Any, Protocol

from dotenv import load_dotenv


class MessageAgent(Protocol):
    def run_agent(
        self,
        input: str,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]: ...


AgentFactory = Callable[[], MessageAgent]


def build_message_agent(
    runtime: str | None = None,
    *,
    harness_factory: AgentFactory | None = None,
    legacy_factory: AgentFactory | None = None,
) -> MessageAgent:
    """Build the configured runtime, defaulting every adapter to the Harness."""

    load_dotenv()
    selected_runtime = (runtime or os.getenv("AGENT_RUNTIME", "harness")).lower()
    if selected_runtime == "harness":
        if harness_factory is None:
            from src.HarnessAgent import HarnessAgent

            harness_factory = HarnessAgent
        return harness_factory()

    if selected_runtime == "legacy":
        if legacy_factory is None:
            from src.Agents import AgentClass

            legacy_factory = AgentClass
        return legacy_factory()

    raise ValueError(
        "AGENT_RUNTIME must be either 'harness' or the temporary 'legacy' fallback"
    )

"""Credential-free HTTP entrypoint for repository and container smoke checks."""

import os
from functools import lru_cache
from typing import Any

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field

from .agent_runtime import MessageAgent, build_message_agent
from .harness import (
    InMemoryUserProfileStore,
    RedisUserProfileStore,
    UserProfile,
    UserProfileStore,
)


app = FastAPI(
    title="OpsFlow Agent Harness",
    description="Reliable agent execution for knowledge retrieval and operational workflows.",
    version="0.1.0",
)


class AgentRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)
    session_id: str | None = Field(default=None, min_length=1, max_length=200)
    user_id: str | None = Field(default=None, min_length=1, max_length=200)


class AgentResponse(BaseModel):
    output: str
    request_id: str | None = None
    task_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None


@lru_cache(maxsize=1)
def get_message_agent() -> MessageAgent:
    if os.getenv("APP_MODE", "demo") == "demo":
        return get_demo_environment().agent
    return build_message_agent()


@lru_cache(maxsize=1)
def get_demo_environment():
    from .demo_runtime import DemoEnvironment

    return DemoEnvironment(os.getenv("WORKSPACE_ROOT", "workspaces") + "/demo")


@lru_cache(maxsize=1)
def get_user_profile_store() -> UserProfileStore:
    if os.getenv("APP_MODE", "demo") == "demo":
        return InMemoryUserProfileStore()
    from redis import Redis

    return RedisUserProfileStore(
        Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0")),
        ttl_seconds=int(os.getenv("USER_PROFILE_TTL_SECONDS", "2592000")),
    )


@app.get("/")
def project_info() -> dict[str, str]:
    return {
        "name": "OpsFlow Agent Harness",
        "mode": os.getenv("APP_MODE", "demo"),
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mode": os.getenv("APP_MODE", "demo")}


@app.post("/v1/agent/run", response_model=AgentResponse)
def run_agent(
    request: AgentRequest,
    agent: MessageAgent = Depends(get_message_agent),
) -> dict[str, Any]:
    return agent.run_agent(
        request.message,
        request.session_id,
        user_id=request.user_id,
    )


@app.get("/v1/users/{user_id}/profile", response_model=UserProfile)
def get_user_profile(
    user_id: str,
    store: UserProfileStore = Depends(get_user_profile_store),
) -> UserProfile:
    return store.get(user_id) or UserProfile(user_id=user_id)


@app.put("/v1/users/{user_id}/profile", response_model=UserProfile)
def put_user_profile(
    user_id: str,
    profile: UserProfile,
    store: UserProfileStore = Depends(get_user_profile_store),
) -> UserProfile:
    stored_profile = profile.model_copy(update={"user_id": user_id})
    store.upsert(stored_profile)
    return stored_profile


@app.get("/v1/demo/scenarios")
def list_demo_scenarios() -> dict[str, list[str]]:
    from .demo_runtime import DEMO_SCENARIOS

    return {"scenarios": list(DEMO_SCENARIOS)}


@app.post("/v1/demo/scenarios/{scenario}")
def run_demo_scenario(scenario: str) -> dict[str, Any]:
    from fastapi import HTTPException

    try:
        return get_demo_environment().run_scenario(scenario)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

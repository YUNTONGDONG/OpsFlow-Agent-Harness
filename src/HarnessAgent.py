"""Shared user-facing adapter for the Agent Harness application."""

import os
from typing import Any

from src.harness import (
    HarnessApplication,
    HarnessResponse,
    HarnessResponseStatus,
    JsonlTraceSink,
    RedisSessionStore,
    RedisUserProfileStore,
    SessionMessage,
    SessionStore,
    UserProfileStore,
    build_harness_application,
)


class HarnessAgent:
    def __init__(
        self,
        application: HarnessApplication | None = None,
        session_store: SessionStore | None = None,
        profile_store: UserProfileStore | None = None,
    ) -> None:
        if application is None:
            (
                application,
                default_session_store,
                default_profile_store,
            ) = self._build_default_components()
            self._application = application
            self._session_store = session_store or default_session_store
            self._profile_store = profile_store or default_profile_store
        else:
            self._application = application
            self._session_store = session_store
            self._profile_store = profile_store

    def run_agent(
        self,
        input: str,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        context = None
        if session_id and self._session_store:
            context = [
                {"role": message.role, "content": message.content}
                for message in self._session_store.recent(session_id, limit=10)
            ]
        if user_id and self._profile_store:
            profile = self._profile_store.get(user_id)
            if profile is not None:
                profile_context = {
                    "role": "system",
                    "content": (
                        "User profile: "
                        + profile.model_dump_json(
                            exclude={"user_id", "updated_at"}
                        )
                    ),
                }
                context = [profile_context, *(context or [])]
        response = self._application.handle(
            input,
            context=context,
            session_id=session_id,
            user_id=user_id,
        )
        output = self._render_response(response)
        if session_id and self._session_store:
            self._session_store.append(
                session_id,
                SessionMessage(role="user", content=input),
            )
            self._session_store.append(
                session_id,
                SessionMessage(role="assistant", content=output),
            )
        return {
            "output": output,
            "request_id": response.request_id,
            "task_id": response.task_id,
            "session_id": session_id,
            "user_id": user_id,
        }

    @staticmethod
    def _build_default_components() -> tuple[
        HarnessApplication,
        SessionStore,
        UserProfileStore,
    ]:
        from langchain_openai import ChatOpenAI
        from redis import Redis

        model = ChatOpenAI(model=os.getenv("BASE_MODEL"))
        redis_client = Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0")
        )
        application = build_harness_application(
            model=model,
            redis_client=redis_client,
            trace_sink=JsonlTraceSink(
                os.getenv("HARNESS_TRACE_PATH", "logs/harness_trace.jsonl")
            ),
        )
        application.recover_unfinished_tasks()
        return (
            application,
            RedisSessionStore(redis_client),
            RedisUserProfileStore(
                redis_client,
                ttl_seconds=int(
                    os.getenv("USER_PROFILE_TTL_SECONDS", "2592000")
                ),
            ),
        )

    @classmethod
    def _render_response(cls, response: HarnessResponse) -> str:
        if response.status == HarnessResponseStatus.FAILED:
            return (
                "抱歉，本次请求未能完成。"
                f"错误代码：{response.error_code or 'HARNESS_REQUEST_FAILED'}"
            )
        if response.status == HarnessResponseStatus.NEEDS_INPUT:
            if isinstance(response.data, dict):
                question = response.data.get("question")
                if isinstance(question, str) and question:
                    return question
            return "还需要补充一些信息后才能继续。"

        value = cls._find_user_value(response.data)
        return value if value is not None else "请求已完成。"

    @classmethod
    def _find_user_value(cls, value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("answer", "result", "data"):
                if key in value:
                    found = cls._find_user_value(value[key])
                    if found is not None:
                        return found
            steps = value.get("steps")
            if isinstance(steps, list):
                for step in reversed(steps):
                    found = cls._find_user_value(step)
                    if found is not None:
                        return found
        if isinstance(value, list):
            for item in reversed(value):
                found = cls._find_user_value(item)
                if found is not None:
                    return found
        return None

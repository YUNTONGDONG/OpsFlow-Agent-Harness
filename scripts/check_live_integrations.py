"""Run credential-aware live probes without exposing secret values."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import pickle
import subprocess
from typing import Any, Callable
from uuid import uuid4

from dotenv import load_dotenv


def result(service: str, status: str, evidence: dict[str, Any], boundary: str):
    return {
        "service": service,
        "status": status,
        "evidence": evidence,
        "boundary": boundary,
    }


def safe_probe(
    service: str,
    boundary: str,
    probe: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    try:
        return result(service, "PASS", probe(), boundary)
    except SkipProbe as exc:
        return result(service, "SKIP", {"reason": str(exc)}, boundary)
    except Exception as exc:
        return result(
            service,
            "FAIL",
            {"error_type": type(exc).__name__},
            boundary,
        )


class SkipProbe(RuntimeError):
    pass


def probe_redis(allow_writes: bool) -> dict[str, Any]:
    from redis import Redis

    url = os.getenv("REDIS_URL")
    if not url:
        raise SkipProbe("REDIS_URL is not configured")
    client = Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
    evidence = {"ping": bool(client.ping()), "round_trip": False}
    if allow_writes:
        key = f"harness:live-probe:{uuid4()}"
        try:
            client.set(key, "ok", ex=60)
            evidence["round_trip"] = client.get(key) == b"ok"
        finally:
            client.delete(key)
    return evidence


def probe_openai_router_evidence() -> dict[str, Any]:
    results_path = Path("logs/router_evaluation.jsonl")
    dataset_path = Path("evals/router_cases.jsonl")
    if not results_path.is_file():
        raise SkipProbe("run `make eval` first")
    rows = [json.loads(line) for line in results_path.read_text().splitlines() if line]
    if not rows:
        raise RuntimeError("router evaluation result is empty")
    passed = sum(bool(row.get("passed")) for row in rows)
    errors = sorted({row["error"] for row in rows if row.get("error")})
    if passed != len(rows):
        raise RuntimeError("router live evaluation contains failures")
    return {
        "passed": passed,
        "total": len(rows),
        "model": os.getenv("BASE_MODEL"),
        "dataset_sha256": sha256(dataset_path.read_bytes()).hexdigest(),
        "errors": errors,
    }


def probe_log_api() -> dict[str, Any]:
    import requests

    url = os.getenv("LOG_API_URL")
    required = {
        "service": os.getenv("LIVE_LOG_SERVICE"),
        "environment": os.getenv("LIVE_LOG_ENVIRONMENT"),
        "start_time": os.getenv("LIVE_LOG_START_TIME"),
        "end_time": os.getenv("LIVE_LOG_END_TIME"),
    }
    if not url:
        raise SkipProbe("LOG_API_URL is not configured")
    if any(value is None for value in required.values()):
        raise SkipProbe("LIVE_LOG_SERVICE/ENVIRONMENT/START_TIME/END_TIME are required")
    headers = {}
    if os.getenv("LOG_API_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["LOG_API_TOKEN"]
    response = requests.get(
        url,
        params={**required, "keyword": os.getenv("LIVE_LOG_KEYWORD")},
        headers=headers,
        timeout=float(os.getenv("LOG_API_TIMEOUT_SECONDS", "8")),
    )
    response.raise_for_status()
    payload = response.json()
    return {
        "http_status": response.status_code,
        "response_type": type(payload).__name__,
    }


def probe_rag_live() -> dict[str, Any]:
    from src.harness.rag import RetrievalMode

    mode = RetrievalMode(os.getenv("RAG_RETRIEVAL_MODE", "faiss_cross_encoder"))
    required = {
        "FAISS_INDEX_PATH": os.getenv("FAISS_INDEX_PATH"),
        "RAG_DOCUMENTS_PATH": os.getenv("RAG_DOCUMENTS_PATH"),
    }
    if mode in {RetrievalMode.FAISS_SVM, RetrievalMode.FULL}:
        required["SVMRANK_MODEL_PATH"] = os.getenv("SVMRANK_MODEL_PATH")
    missing = [
        name for name, value in required.items()
        if not value or not Path(value).is_file()
    ]
    if missing:
        raise SkipProbe("missing RAG artifacts: " + ", ".join(missing))
    query = os.getenv("LIVE_RAG_QUERY")
    if not query:
        raise SkipProbe("LIVE_RAG_QUERY is required")
    from src.harness.skills.knowledge_pipeline import build_live_knowledge_pipeline

    documents, trace = build_live_knowledge_pipeline().retrieve(
        query,
        mode=mode,
    )
    return {
        "selected_chunk_ids": [document.chunk_id for document in documents],
        "stages": [item.model_dump(mode="json") for item in trace],
        "answer_generation_called": False,
    }


def _google_services():
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_path = Path("token.pickle")
    if not token_path.is_file():
        raise SkipProbe("token.pickle is not present; interactive OAuth is not run by probes")
    with token_path.open("rb") as token_file:
        credentials = pickle.load(token_file)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials.valid:
        raise RuntimeError("Google OAuth token is invalid")
    return (
        build("calendar", "v3", credentials=credentials),
        build("tasks", "v1", credentials=credentials),
    )


def probe_google_read() -> dict[str, Any]:
    calendar, tasks = _google_services()
    calendars = calendar.calendarList().list(maxResults=1).execute()
    tasklists = tasks.tasklists().list(maxResults=1).execute()
    return {
        "calendar_read": "items" in calendars,
        "tasks_read": "items" in tasklists,
        "calendar_count_sampled": len(calendars.get("items", [])),
        "tasklist_count_sampled": len(tasklists.get("items", [])),
    }


def probe_google_write(allow_writes: bool) -> dict[str, Any]:
    if not allow_writes:
        raise SkipProbe("external writes require --allow-writes")
    calendar_id = os.getenv("LIVE_GOOGLE_CALENDAR_ID")
    tasklist_id = os.getenv("LIVE_GOOGLE_TASKLIST_ID")
    if not calendar_id or not tasklist_id:
        raise SkipProbe("sandbox calendar and tasklist IDs are required")
    calendar, tasks = _google_services()
    start = datetime.now(timezone.utc) + timedelta(days=30)
    end = start + timedelta(minutes=5)
    event_id = None
    task_id = None
    try:
        event = calendar.events().insert(
            calendarId=calendar_id,
            body={
                "summary": f"OpsFlow live probe {uuid4()}",
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": end.isoformat()},
            },
        ).execute()
        event_id = event["id"]
        fetched_event = calendar.events().get(
            calendarId=calendar_id,
            eventId=event_id,
        ).execute()
        title = f"OpsFlow live probe {uuid4()}"
        created = tasks.tasks().insert(
            tasklist=tasklist_id,
            body={"title": title, "notes": "Temporary integration probe"},
        ).execute()
        task_id = created["id"]
        fetched = tasks.tasks().get(tasklist=tasklist_id, task=task_id).execute()
        evidence = {
            "task_created_and_verified": fetched.get("title") == title,
            "calendar_created_and_verified": fetched_event.get("id") == event_id,
            "external_resource_ids": {"task_id": task_id, "event_id": event_id},
        }
    finally:
        if task_id is not None:
            tasks.tasks().delete(tasklist=tasklist_id, task=task_id).execute()
        if event_id is not None:
            calendar.events().delete(
                calendarId=calendar_id,
                eventId=event_id,
            ).execute()
    evidence["cleanup"] = "task_and_event_deleted"
    return {
        **evidence,
    }


def probe_slack_read() -> dict[str, Any]:
    from slack_sdk import WebClient

    token = os.getenv("SLACK_BOT_TOKEN")
    if not token:
        raise SkipProbe("SLACK_BOT_TOKEN is not configured")
    response = WebClient(token=token).auth_test()
    return {
        "authenticated": bool(response.get("ok")),
        "team_id": response.get("team_id"),
        "bot_id": response.get("bot_id"),
    }


def probe_slack_write(allow_writes: bool) -> dict[str, Any]:
    from slack_sdk import WebClient

    if not allow_writes:
        raise SkipProbe("external writes require --allow-writes")
    token = os.getenv("SLACK_BOT_TOKEN")
    channel = os.getenv("LIVE_SLACK_CHANNEL_ID")
    if not token or not channel:
        raise SkipProbe("SLACK_BOT_TOKEN and LIVE_SLACK_CHANNEL_ID are required")
    response = WebClient(token=token).chat_postMessage(
        channel=channel,
        text="OpsFlow temporary live integration probe",
    )
    return {
        "message_sent": bool(response.get("ok")),
        "channel": response.get("channel"),
        "external_resource_id": response.get("ts"),
    }


def _git_commit() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/live_integrations.json"),
    )
    args = parser.parse_args()
    load_dotenv(".env")
    probes = [
        safe_probe(
            "openai_router",
            "Summarizes the latest real-model router evaluation; executes no Skill.",
            probe_openai_router_evidence,
        ),
        safe_probe(
            "redis",
            "Ping is read-only; optional scoped key is deleted after round-trip.",
            lambda: probe_redis(args.allow_writes),
        ),
        safe_probe(
            "log_api_read",
            "Configured time-bounded GET query; response content is not persisted.",
            probe_log_api,
        ),
        safe_probe(
            "rag_retrieval",
            "Runs embedding, FAISS, SVMRank and Cross-Encoder; does not generate an answer.",
            probe_rag_live,
        ),
        safe_probe(
            "google_read",
            "Lists at most one Calendar and Tasks list; creates nothing.",
            probe_google_read,
        ),
        safe_probe(
            "google_calendar_tasks_write",
            "Requires explicit sandbox IDs; temporary task is verified and deleted.",
            lambda: probe_google_write(args.allow_writes),
        ),
        safe_probe(
            "slack_auth",
            "Calls auth.test only and records non-secret account IDs.",
            probe_slack_read,
        ),
        safe_probe(
            "slack_message_write",
            "Requires explicit channel ID; message is an externally visible side effect.",
            lambda: probe_slack_write(args.allow_writes),
        ),
    ]
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "allow_writes": args.allow_writes,
        "results": probes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if any(item["status"] == "FAIL" for item in probes) else 0


if __name__ == "__main__":
    raise SystemExit(main())

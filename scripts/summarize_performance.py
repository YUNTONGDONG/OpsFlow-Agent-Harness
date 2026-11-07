"""Create an evidence-bounded performance report from request trace events."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess

from src.harness import TraceEventType, load_trace_events


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("Cannot calculate a percentile without values")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def build_report(
    *,
    trace_path: Path,
    dataset_path: Path,
    concurrency: int,
    warm_state: str,
    model: str,
    hardware: str | None = None,
) -> dict:
    completed = [
        event
        for event in load_trace_events(trace_path)
        if event.event_type == TraceEventType.REQUEST_COMPLETED
    ]
    if not completed:
        raise ValueError("Trace contains no completed requests")
    durations = [float(event.attributes["duration_ms"]) for event in completed]
    failures = sum(
        event.attributes.get("status") != "completed" for event in completed
    )
    dataset_bytes = dataset_path.read_bytes()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "dataset": str(dataset_path),
        "dataset_sha256": sha256(dataset_bytes).hexdigest(),
        "request_count": len(completed),
        "concurrency": concurrency,
        "warm_state": warm_state,
        "model": model,
        "hardware": hardware or _hardware_summary(),
        "latency_ms": {
            "p50": percentile(durations, 0.50),
            "p95": percentile(durations, 0.95),
            "min": min(durations),
            "max": max(durations),
        },
        "failure_count": failures,
        "failure_rate": failures / len(completed),
        "trace": str(trace_path),
    }


def _git_commit() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _hardware_summary() -> str:
    return (
        f"{platform.system()} {platform.machine()}, "
        f"cpu_count={os.cpu_count() or 'unknown'}, python={platform.python_version()}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--concurrency", required=True, type=int)
    parser.add_argument("--warm-state", required=True, choices=("cold", "prewarmed"))
    parser.add_argument("--model", required=True)
    parser.add_argument("--hardware")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.concurrency <= 0:
        raise ValueError("concurrency must be positive")
    report = build_report(
        trace_path=args.trace,
        dataset_path=args.dataset,
        concurrency=args.concurrency,
        warm_state=args.warm_state,
        model=args.model,
        hardware=args.hardware,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

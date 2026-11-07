"""Evaluate real-model Skill routing without executing any Skill."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from src.harness import LangChainRequestRouter, build_default_registry


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON on line {line_number}") from exc
        has_expected_route = "expected_skills" in case
        has_expected_clarification = "required_missing_fields" in case
        if (
            not case.get("id")
            or not case.get("input")
            or has_expected_route == has_expected_clarification
        ):
            raise ValueError(f"Incomplete router case on line {line_number}")
        cases.append(case)
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evals/router_cases.jsonl"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/router_evaluation.jsonl"),
    )
    args = parser.parse_args()

    load_dotenv(".env")
    model_name = os.getenv("BASE_MODEL")
    if not model_name or not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("BASE_MODEL and OPENAI_API_KEY must be configured")

    cases = load_cases(args.cases)
    registry = build_default_registry()
    router = LangChainRequestRouter(ChatOpenAI(model=model_name, temperature=0))
    available_skills = registry.list_skills(include_schema=True)
    results = []
    correct_by_expected: Counter[str] = Counter()
    total_by_expected: Counter[str] = Counter()

    for case in cases:
        expected = case.get("expected_skills", [])
        required_missing = set(case.get("required_missing_fields", []))
        error = None
        predicted: list[str] = []
        steps: list[dict[str, Any]] = []
        arguments_valid = False
        clarification_fields: list[str] = []
        try:
            decision = router.route(
                case["input"],
                available_skills,
                case.get("context"),
            )
            steps = [step.model_dump(mode="json") for step in decision.steps]
            predicted = [step["skill_name"] for step in steps]
            if decision.clarification is not None:
                clarification_fields = decision.clarification.missing_fields
            else:
                arguments_valid = all(
                    registry.get(step["skill_name"]).validate_input(
                        step["arguments"]
                    )
                    is not None
                    for step in steps
                )
        except Exception as exc:
            error = type(exc).__name__

        if required_missing:
            passed = (
                not predicted
                and required_missing == set(clarification_fields)
            )
            expected_label = "clarification"
        else:
            passed = predicted == expected and arguments_valid
            expected_arguments = case.get("expected_arguments", [])
            if passed and expected_arguments:
                passed = len(expected_arguments) == len(steps) and all(
                    all(
                        step["arguments"].get(key) == value
                        for key, value in expected_subset.items()
                    )
                    for step, expected_subset in zip(
                        steps,
                        expected_arguments,
                        strict=True,
                    )
                )
            expected_label = ",".join(expected)
        total_by_expected[expected_label] += 1
        if passed:
            correct_by_expected[expected_label] += 1
        results.append(
            {
                "id": case["id"],
                "input": case["input"],
                "expected_skills": expected,
                "expected_arguments": case.get("expected_arguments", []),
                "predicted_skills": predicted,
                "arguments_valid": arguments_valid,
                "required_missing_fields": sorted(required_missing),
                "clarification_fields": clarification_fields,
                "passed": passed,
                "steps": steps,
                "error": error,
            }
        )
        print(
            f"{case['id']}: {'PASS' if passed else 'FAIL'} "
            f"expected={expected} predicted={predicted} "
            f"arguments_valid={arguments_valid} "
            f"clarification_fields={clarification_fields}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in results),
        encoding="utf-8",
    )

    passed_count = sum(item["passed"] for item in results)
    print(f"\noverall={passed_count}/{len(results)} ({passed_count / len(results):.1%})")
    for label in sorted(total_by_expected):
        correct = correct_by_expected[label]
        total = total_by_expected[label]
        print(f"{label}={correct}/{total} ({correct / total:.1%})")
    print(f"details={args.output}")
    return 0 if passed_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

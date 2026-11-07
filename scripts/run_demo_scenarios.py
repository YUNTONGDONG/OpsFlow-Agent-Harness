"""Run all credential-free final-state scenarios and save raw evidence."""

import argparse
import json
from pathlib import Path

from src.demo_runtime import DEMO_SCENARIOS, DemoEnvironment


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("logs/demo_scenarios.json"),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("workspaces/demo-cli"),
    )
    args = parser.parse_args()
    results = []
    for scenario in DEMO_SCENARIOS:
        environment = DemoEnvironment(args.workspace / scenario)
        result = environment.run_scenario(scenario)
        results.append(result)
        statuses = [item.get("status") for item in result["responses"]]
        print(f"{scenario}: statuses={statuses} trace_events={len(result['trace'])}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"evidence={args.output}")


if __name__ == "__main__":
    main()

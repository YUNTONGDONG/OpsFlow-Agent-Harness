"""Local command-line adapter for the same Harness used by Slack and HTTP."""

import argparse
from typing import Any

from .agent_runtime import MessageAgent, build_message_agent


def run_once(
    agent: MessageAgent,
    message: str,
    session_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    return agent.run_agent(message, session_id, user_id=user_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Send one request to Agent Harness")
    parser.add_argument("message", help="User request to execute")
    parser.add_argument("--session-id", default="local-cli")
    parser.add_argument("--user-id", default="local-user")
    args = parser.parse_args()

    result = run_once(
        build_message_agent(),
        args.message,
        session_id=args.session_id,
        user_id=args.user_id,
    )
    print(result["output"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

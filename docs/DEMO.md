# Credential-free final-state demo

## Run

```bash
cp .env.example .env
make demo-scenarios
```

This command requires no external credentials. It runs the production Harness
control path with deterministic routing and injected Mock providers. The raw
result is saved to `logs/demo_scenarios.json`; request, plan, input, output, and
response artifacts are saved below `workspaces/demo-cli/`.

## What each scenario proves

| Scenario | Evidence produced | Explicit boundary |
| --- | --- | --- |
| `knowledge` | 100/20/3 retrieval counts, grounded answer, chunk sources | Fixed local documents and answerer; no external embedding or LLM call |
| `log_to_task` | DAG, output binding, Mock Task ID and verified write | Mock log and task providers |
| `parallel_reads` | Both Skill start events occur before either completion | Fixed local delays, not a production load test |
| `safe_delete` | Candidate lookup, expiring pending confirmation, verified delete, replay rejection | In-process Mock Calendar; not session-bound Google evidence |
| `recovery` | Interrupted read reset, re-execution and recovery events | In-memory state; Redis process restart is a separate integration test |

## Inspect evidence

Each scenario result contains `request_ids`, `responses`, `trace`, and an
`evidence_boundary`. Workspace references in the Trace point to the exact JSON
files used by the run and include SHA-256 checksums.

To use the HTTP presentation instead:

```bash
make demo
curl -X POST http://localhost:8000/v1/demo/scenarios/knowledge
```

Docker Compose starts the same credential-free API by default:

```bash
docker compose up --build
```

Do not describe these runs as OpenAI, Google Calendar, Google Tasks, Redis
restart, Slack, or log-platform live integration. Those require separately
recorded runs with the corresponding services configured.

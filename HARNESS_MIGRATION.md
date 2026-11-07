# Agent Harness migration

## Why this is a Harness

The legacy application attaches every LangChain tool directly to one
`AgentExecutor`. The new runtime places an explicit execution layer between the
model and business tools:

1. `SkillRegistry` discovers reusable capabilities without initializing them.
2. `LangChainRequestRouter` emits a schema-validated route or task plan.
3. `HarnessApplication` validates that planned skills are registered.
4. `SkillRuntime` validates inputs, isolates errors, records timing, and emits
   trace events.
5. `TaskRunner` executes multi-step plans only when dependencies are complete.
6. `RedisTaskStore` persists task and step state with a bounded TTL.
7. `RedisSessionStore` supplies a bounded recent-message window for follow-ups.
8. `JsonlTraceSink` preserves request-correlated routing and execution events.

Trace events now carry the request/session/user/task/step correlation IDs. Raw
requests, plans, step inputs/outputs, and final responses remain in Workspace;
the trace contains their checksum-bearing references plus status, timing,
attempt count, verification state, and external resource IDs. Recovery emits
separate start/completion events. `scripts/summarize_performance.py` derives
request-level P50/P95/failure rate while recording dataset hash, concurrency,
warm state, model, hardware, timestamp, and Git commit.

Task control state and durable execution evidence are intentionally separated.
Redis stores the compact task graph, current status, verification metadata, and
bounded session context. `LocalWorkspace` stores `request.json`, `plan.json`,
and each step's input/output JSON with a checksum-bearing reference. Results
larger than `WORKSPACE_INLINE_MAX_BYTES` are omitted from the Redis task payload
and loaded from Workspace when a downstream `$from` binding needs them.

Cross-session preferences use a third namespace, `RedisUserProfileStore`, keyed
by `user_id`. The REST profile endpoints explicitly manage time zone, output
language, default Calendar/Tasks targets, and user preferences. Profiles are
passed to the Router as system context; the Harness does not infer or mutate
them from ordinary conversation.

Multi-step plans can bind a downstream argument to a declared upstream output:

```json
{"subject": {"$from": "diagnose.output.issue_title"}}
```

The application validates the source output schema before execution, and the
TaskRunner stores the resolved arguments with the step state.

Independent `read` steps in the same ready batch run concurrently. Global
concurrency defaults to four workers through `HARNESS_MAX_CONCURRENCY`; each
Skill also declares its own concurrency limit and timeout. Write operations
remain serial. A failed or timed-out independent read does not erase successful
results from the same batch.

Side-effecting steps always run through TaskState, including single-step writes.
Before execution the runner persists an idempotency record. A write becomes
complete only after its Skill verifier confirms external state and returns an
external resource ID. An indeterminate result becomes `UNKNOWN` and is never
blindly replayed. `recover(task_id)` safely reruns interrupted reads, reuses
verified idempotency records, and re-verifies unknown writes. Only read Skills
use automatic bounded retries; non-idempotent writes execute once.

## Migration boundary

The Harness is now the default runtime for Slack, the REST API, and the local
CLI. All three adapters build their runtime through `src.agent_runtime` and use
the same `run_agent(input, session_id)` contract. The old `AgentClass` remains
available only as a temporary, explicit `AGENT_RUNTIME=legacy` fallback.

Unit tests prove the Harness control flow, but live checks for Qdrant, Redis,
the configured LLM, Slack, Google Calendar, and Google Tasks remain separate
integration evidence.

The checked-in Workspace backend is a local filesystem implementation suitable
for direct reproduction and one-node deployment. Shared object storage,
retention jobs, encryption policy, and live Redis durability remain deployment
integration work rather than unit-test claims.

## Implemented skills

| Skill | Current backend | Operation type |
| --- | --- | --- |
| `knowledge_search` | FAISS → SVMRank → Cross-Encoder → grounded LLM | Read |
| `log_diagnosis` | Configured log API or supplied log text | Read |
| `web_search` | Existing SerpAPI tool | Read |
| `calendar_management` | Existing Google Calendar tools | Read and write |
| `task_management` | Existing Google Tasks create tool | Write |

`src/harness/skills` is the canonical Skill system used by the Harness. The
older `src/skills` package is retained only by the explicit legacy runtime.
`log_diagnosis` is registered and covered with injected-backend tests; a live
log-platform result must still be reported separately from those tests.

## Verification

Run:

```bash
python -m unittest discover -s tests -v
```

The focused suite covers Skill registration, schema validation, Runtime error
normalization, dependency validation, state transitions, Redis serialization,
request routing, task execution, session isolation, and trace persistence.

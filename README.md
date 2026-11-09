# OpsFlow Agent Harness

[![unit tests](https://github.com/YUNTONGDONG/opsflow-agent-harness/actions/workflows/test.yml/badge.svg)](https://github.com/YUNTONGDONG/opsflow-agent-harness/actions/workflows/test.yml)

A reliable agent harness for knowledge retrieval and operational workflow
automation. It separates model-driven request understanding from deterministic
execution, state management, recovery, and evidence collection.

OpsFlow is not primarily a LangChain chatbot. LangChain is used at the model
boundary for prompt and structured-output integration. The OpsFlow Harness
owns the execution contract: skill discovery, validated plans, dependency-aware
scheduling, bounded retries, write safety, durable state, workspace evidence,
and request-level traces.

> The model understands and plans. The Harness controls state and execution.
> Skills perform business operations.

## Why an Agent Harness?

A model can choose a tool or produce a plan, but a production agent still needs
deterministic answers to operational questions:

- Is the requested skill registered and is its input valid?
- Which steps can run concurrently, and which depend on earlier output?
- Is a failed operation safe to retry?
- Did an external write complete when the response timed out?
- How does an interrupted task resume without repeating side effects?
- Where are the request, plan, step evidence, and correlated trace stored?

The Harness implements these controls outside the model loop so that they can be
tested, inspected, and recovered independently.

## Architecture

All entry points use the same runtime factory and the same Harness execution
path:

```text
Slack / REST API / Local CLI
              |
              v
         HarnessAgent
              |
              v
      HarnessApplication
              |
              v
   Router / Planner (structured output)
              |
              v
          TaskRunner
              |
              v
         SkillRuntime
              |
              v
Knowledge / Log / Web / Calendar / Tasks Skills

Harness <------> Redis task, session, and profile state
Runtime/Skills <-> Workspace request and step evidence
All stages ------> Correlated Trace events
```

The default runtime is selected explicitly:

```env
AGENT_RUNTIME=harness
```

The legacy `AgentClass` remains available only as an explicit migration fallback
with `AGENT_RUNTIME=legacy`; it is not the default path for REST, Slack, or CLI
requests.

## Harness Responsibilities

### Registry and validated routing

The Skill Registry exposes canonical names, operation types, and input schemas.
The Router produces a structured route or plan constrained by that registry.
Pydantic validation and runtime lookup reject unknown skills and invalid inputs
before business code executes.

### Dependency-aware execution

`TaskRunner` validates step identifiers, dependencies, cycles, and `$from`
output bindings. Independent read steps can run concurrently within configured
global and per-skill limits. Writes remain serial, and a failed dependency
blocks downstream work.

### Read and write safety

Only declared read operations receive bounded automatic retries. Writes use
idempotency records and external-state verification. If the external result
cannot be determined, the step becomes `UNKNOWN`; recovery verifies the
external system instead of blindly repeating the write.

### Recovery and evidence

Tasks persist step state and external resource identifiers. Interrupted reads
can resume, while uncertain writes are reverified. Request inputs, plans, step
inputs and outputs, and final responses are saved to the Workspace. Trace events
correlate the full request with `request_id`, `task_id`, and `step_id`.

## Included Skills

The Harness registers the following production skill contracts:

- `knowledge_search`: FAISS Top-100 recall, SVMRank Top-20 filtering, and
  Cross-Encoder Top-3 reranking before evidence-grounded answer generation.
- `log_diagnosis`: analyzes supplied log text or queries a configured log API.
- `web_search`: delegates live search to the configured backend.
- `calendar_management`: searches, creates, updates, and safely deletes Google
  Calendar events.
- `task_management`: creates and verifies Google Tasks items.

The external backends are optional. The credential-free Demo uses deterministic
providers while exercising the real Harness control flow.

## Quick Start with Docker

Requirements:

- Docker Desktop with Docker Compose
- Git

Start the Demo API and Redis:

```bash
cp .env.example .env
docker compose up --build
```

The default `APP_MODE=demo` does not call OpenAI, Slack, Google, a log platform,
or a live knowledge backend.

Verify the service:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"ok","mode":"demo"}
```

Run an Agent request:

```bash
curl -X POST http://localhost:8000/v1/agent/run \
  -H 'Content-Type: application/json' \
  -d '{"message":"Find the E102 incident procedure","session_id":"demo-user"}'
```

Interactive API documentation is available at
[`http://localhost:8000/docs`](http://localhost:8000/docs).

Stop the containers with `Ctrl+C`, or run:

```bash
docker compose stop
```

## Credential-Free Demo Scenarios

Five deterministic scenarios demonstrate the Harness behavior:

1. `knowledge`: runs the 100 -> 20 -> 3 retrieval flow and returns citations.
2. `log_to_task`: binds diagnosis output into a dependent task-creation step and
   verifies the external identifier.
3. `parallel_reads`: starts independent knowledge and calendar reads before
   either finishes.
4. `safe_delete`: requires confirmation, deletes once, and rejects confirmation
   replay.
5. `recovery`: resumes an interrupted `RUNNING` read step.

With Docker running, execute all scenarios inside the application container:

```bash
docker compose exec app python -m scripts.run_demo_scenarios \
  --output logs/demo_scenarios.json
```

The report is written to `logs/demo_scenarios.json`, and Workspace evidence is
written under `workspaces/demo-cli/`.

Individual scenarios are also available over HTTP:

```bash
curl http://localhost:8000/v1/demo/scenarios
curl -X POST http://localhost:8000/v1/demo/scenarios/knowledge
curl -X POST http://localhost:8000/v1/demo/scenarios/log_to_task
curl -X POST http://localhost:8000/v1/demo/scenarios/parallel_reads
curl -X POST http://localhost:8000/v1/demo/scenarios/safe_delete
curl -X POST http://localhost:8000/v1/demo/scenarios/recovery
```

Each result includes the response, trace, and an `evidence_boundary` describing
what the deterministic scenario does and does not prove. See
[`docs/DEMO.md`](docs/DEMO.md) for details.

## Local Development

The repository targets Python 3.12 and uses Poetry:

```bash
poetry env use 3.12
poetry install
cp .env.example .env
make test
make demo
```

Useful commands:

```bash
make test                    # Unit tests with no external services
make test-integration        # Local infrastructure integration tests
make demo                    # Deterministic credential-free API
make demo-scenarios          # All five deterministic Harness scenarios
make cli MESSAGE="Find E102" # Local CLI through the same Harness
make eval                    # Real-model routing evaluation
make test-live-read          # Read-only external integration probes
make test-live-write         # Explicit sandbox write probes and cleanup
make slack                   # Real Slack Socket Mode entry point
```

GitHub Actions runs the unit suite with Python 3.12 on every push and pull
request.

## State and Storage Model

The Harness separates operational state from evidence:

| Layer | Contents | Default retention |
| --- | --- | --- |
| Redis task/session | Step dependencies, statuses, external IDs, recent conversation | Task: 7 days; session: 24 hours |
| Workspace | Request, plan, step inputs and outputs, response evidence | Deployment-managed |
| Redis user profile | Explicit timezone, language, and default resource preferences | 30 days, renewable |

Workspace JSON is written atomically and referenced with SHA-256 and byte count.
Results larger than `WORKSPACE_INLINE_MAX_BYTES` remain in the Workspace and are
represented in Redis by `workspace://` references. Downstream `$from` bindings
can resolve those references transparently.

User preferences are explicit rather than inferred silently from conversation:

```bash
curl -X PUT http://localhost:8000/v1/users/demo-user/profile \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"demo-user","time_zone":"America/New_York","output_language":"en"}'

curl http://localhost:8000/v1/users/demo-user/profile
```

## RAG Retrieval and Optional Candidate Compression

The evaluated default knowledge pipeline in `src/harness/rag.py` uses:

```text
FAISS Top-5 -> multilingual Cross-Encoder Top-3 -> Answer
```

Set `RAG_RETRIEVAL_MODE=faiss` for the fast fallback:

```text
FAISS Top-3 -> Answer
```

Set `RAG_RETRIEVAL_MODE=full` to enable the experimental compression path:

```text
FAISS Top-100 -> SVMRank Top-20 -> Cross-Encoder Top-3 -> Answer
```

- FAISS performs normalized-vector candidate recall.
- The default multilingual mMARCO Cross-Encoder and Top-5 window improved the
  checked-in holdout evaluation over FAISS alone. Its model revision is pinned
  in `.env.example` for reproducibility.
- SVMRank optionally reduces Cross-Encoder work using an offline-trained linear
  ranker. Keep it disabled until its candidate recall is validated on a
  representative holdout set.
- FAISS-only mode does not load Cross-Encoder or SVMRank artifacts, so it remains
  available as a low-latency and offline fallback.
- The Cross-Encoder scores all query-document pairs in one batch.
- The answer includes source references and per-stage retrieval metrics.
- If no candidate reaches `RAG_RELEVANCE_THRESHOLD`, the pipeline refuses to
  answer without calling the generator.

Install optional local retrieval and reranking dependencies:

```bash
poetry install -E rag
```

Build an index from JSONL documents containing `chunk_id`, `content`, `source`,
and optional `metadata`:

```bash
make knowledge-chunks
make rag-index \
  INPUT=data/knowledge_chunks.jsonl \
  FAISS_INDEX_PATH=rag_data/knowledge.faiss \
  RAG_DOCUMENTS_PATH=rag_data/documents.jsonl
make rag-training-data
make rag-train
```

Evaluate retrieval ablations with a fixed dataset:

```bash
make rag-eval DATASET=evals/rag_cases.jsonl
```

Run one end-to-end query with the configured retrieval mode and generator:

```bash
python scripts/run_live_rag_query.py \
  "What are the three retrieval stages in the OpsFlow knowledge pipeline?" \
  --output logs/live_rag_query.json
```

`RAG_RELEVANCE_THRESHOLD` is a Cross-Encoder logit threshold, not a
probability. Keep the default refusal behavior until the threshold has been
calibrated on a representative labeled dataset. For an explicit local
calibration experiment, pass `--threshold` to the live-query script.

Real embeddings, model downloads, trained artifacts, and generator calls are
Live Integration evidence; injected test doubles do not prove those external
systems are available.

## Live Integrations

Copy `.env.example` to `.env` and configure only the services you intend to
exercise:

- OpenAI-compatible routing and answer generation
- Embeddings and RAG artifacts
- Redis
- Slack Socket Mode
- Google Calendar and Tasks OAuth
- Log API

Run read-only checks separately from the deterministic suite:

```bash
make eval
make test-live-read
```

Every probe records `PASS`, `SKIP`, or `FAIL`. Missing credentials are reported
as `SKIP`, never as success. External writes remain disabled unless explicit
sandbox Calendar, Tasklist, or Slack Channel identifiers are configured and
`make test-live-write` is invoked. See
[`docs/LIVE_INTEGRATION.md`](docs/LIVE_INTEGRATION.md).

Never commit `.env`, `credentials.json`, `token.pickle`, real provider
responses, user content, runtime logs, or local databases.

## Observability and Performance Reports

Trace events correlate routing, planning, execution, retries, verification,
latency, and errors for each request. Large or sensitive payloads stay in the
Workspace; Trace records their checksummed references.

Generate a scoped performance report after running a fixed dataset:

```bash
make perf-report \
  TRACE=logs/harness_trace.jsonl \
  DATASET=evals/router_cases.jsonl \
  CONCURRENCY=1 \
  WARM_STATE=prewarmed \
  MODEL=gpt-4o-mini \
  OUTPUT=logs/performance_report.json
```

The report records the dataset hash, request count, concurrency, warm state,
model, hardware, timestamp, Git commit, P50, P95, and failure rate. Performance
claims should be made only from a saved report with its stated test boundary.

## Project Structure

```text
src/api.py                    REST entry point
src/SlackWebHook.py           Slack Socket Mode entry point
src/cli.py                    Local CLI entry point
src/agent_runtime.py          Shared runtime factory
src/HarnessAgent.py           Entry-point adapter
src/harness/application.py    Routing, planning, and task creation
src/harness/task_runner.py    Dependency scheduling and recovery
src/harness/runtime.py        Validation, retry, and write controls
src/harness/registry.py       Skill discovery and schemas
src/harness/rag.py            Three-stage retrieval pipeline
src/harness/workspace.py      Durable request and step evidence
src/harness/trace.py          Correlated execution events
src/harness/skills/           Canonical business skill contracts
scripts/                      Demo, evaluation, indexing, and reports
tests/                        Unit and focused integration tests
```

The complete acceptance criteria and evidence boundaries are documented in
[`docs/FINAL_STATE_ACCEPTANCE.md`](docs/FINAL_STATE_ACCEPTANCE.md).

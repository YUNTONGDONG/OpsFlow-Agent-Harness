# Live integration evidence

Live checks are intentionally separate from unit tests and the deterministic
Demo. Never commit `.env`, OAuth tokens, API keys, raw provider responses, or
real user content.

## Commands

```bash
make eval
make test-live-read
```

`make eval` calls the configured model but executes no Skill. The current fixed
dataset contains five cases each for Knowledge, Log Diagnosis, Calendar, and
Tasks. Detailed per-case results are written to
`logs/router_evaluation.jsonl`.

`make test-live-read` writes `logs/live_integrations.json`. Every probe is one
of:

- `PASS`: the configured external system responded and the stated assertion
  was verified;
- `SKIP`: credentials, artifacts, sandbox identifiers, or a local service are
  absent;
- `FAIL`: the service was configured but the probe failed.

The runner never prints secret values or provider response bodies. Failures
record only the exception type.

## Read probes

| Probe | What it verifies | Required configuration |
| --- | --- | --- |
| OpenAI Router | Latest real-model fixed-dataset results all passed | `OPENAI_API_KEY`, `BASE_MODEL`, then `make eval` |
| Redis | `PING`; optional scoped key round-trip only in write mode | running `REDIS_URL` |
| Log API | Time-bounded HTTP GET and JSON response | `LOG_API_URL`, `LIVE_LOG_*` query fields |
| RAG retrieval | Embedding → FAISS → SVMRank → Cross-Encoder | three RAG artifacts and `LIVE_RAG_QUERY` |
| Google read | Lists at most one Calendar and one Tasklist | local valid `token.pickle` |
| Slack auth | Slack `auth.test` | `SLACK_BOT_TOKEN` |

## External write probes

Writes require both the `--allow-writes` flag used by `make test-live-write`
and explicit sandbox target IDs:

```env
LIVE_GOOGLE_CALENDAR_ID=
LIVE_GOOGLE_TASKLIST_ID=
LIVE_SLACK_CHANNEL_ID=
```

Google creates a temporary Event and Task, reads both back, records their
external IDs, and deletes them in a `finally` cleanup. Slack posts one clearly
labelled probe message; it is externally visible and is not run by default.

## Current verified snapshot — 2026-11-07

- OpenAI Router: `PASS`, 20/20 on the final four core Skill classes.
- Redis: `FAIL`, URL configured but no Redis server is running locally.
- Log API: `SKIP`, endpoint not configured.
- RAG retrieval: `SKIP`, FAISS/documents/SVMRank artifacts absent.
- Google Calendar/Tasks: `SKIP`, OAuth token absent.
- Slack: `SKIP`, tokens absent.

The raw reports are under ignored `logs/`. This snapshot proves only model
routing. It does not prove Skill execution, provider writes, Slack Socket Mode,
Redis restart recovery, or end-to-end production behavior.

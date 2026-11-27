---
name: offline-resilience
description: Use when working on model calls, circuit breaking, timeouts, retries, graceful degradation, request handling, or production stability in crm-sales-agent. Covers LLM fallback semantics, tool timeouts without thread killing, request timeout vs ghost memory, rate limiting, resource cleanup, and the guarantee that core functions run with no network or external services.
---

# Offline resilience

The system must answer core questions with **no API Key, no network, and no external service**. The model is an optional enhancement, never a dependency. See design-notes §9–14, §19, §26 for rationale.

## LLM degradation contract (`llm.py`)

- `Settings.llm_enabled` is False when `llm_mode=offline` (regardless of Key) or when mode is `llm` with no Key. `mode` reports `llm`/`offline`.
- `LLM.chat()` returns `None` on any failure (init failure, timeout, circuit open, no Key). Callers must treat `None` as "use the rule/template path", never as an error to propagate.
- Circuit breaker: after `llm_cb_threshold` consecutive failures, open for `llm_cb_cooldown` seconds; half-open on expiry. While open, `chat()` returns `None` **without any network call**. `available` reflects state for health/metrics.
- Metrics record calls/failures/latency only; log `type(exc).__name__`, never the Key or response body.
- Every node has a template fallback: `profile.py:183`, `opportunity.py:253`, `analysis.py:247`, `knowledge.py:71`. Adding a node without a fallback breaks the offline guarantee.

## Request timeout vs ghost memory (`api/server.py`, `agent.py`)

`POST /api/chat` wraps `agent.ask` in `asyncio.wait_for(..., settings.request_timeout)`. On timeout the client gets 504 — but `wait_for` cannot stop the worker thread already running in `to_thread`. Without care, that hidden answer gets written to conversation memory, and the next follow-up resolves references against something the user never saw ("ghost memory").

`SalesAgent.ask()` accepts an optional `cancel_event: threading.Event`. The API sets it on timeout; `ask()` checks it before `memory.append_turn` and skips the write. Old callers (CLI, eval, tests) omit it and are unaffected. This covers the dominant slow-LLM case; extreme races are acceptable. Do not write a failed/degraded (`intent="error"`) answer into memory either.

## Tool timeout without thread killing (`tools/registry.py:358-373`)

`Future.result(timeout)` returns `TOOL_TIMEOUT` promptly, but the worker is not force-terminated — Python cannot safely kill threads, and a killed thread could leave half-written state. Keep handlers read-only/idempotent and bound the worker pool (8). SQLite connections are thread-local, so connection count is bounded by worker count.

## Other degradation paths (all must hold)

- `context.py`: knowledge-base load failure → `_EmptyKnowledgeBase` (retrieve returns `[]`), service still starts. Advertiser-prefix map build failure → `{}`, router falls back to the full-table query.
- `memory.py`: SQLite persistence failure → in-memory only, warning logged; `close()`/`clear()` still work.
- `store.py`: `close()` resets thread-local connections so the same thread reopens a fresh `mode=ro` connection.
- `graph.py`: session eviction failure is debug-logged and never affects an answer.
- `config.py`/`scoring.py`: out-of-range config/thresholds fall back per item; the service must always start.
- `orchestrate.py`: tool orchestration exception → pure node execution; a sub-task failure is isolated into a safe error result without dropping the other sub-answers.
- `integration.py`: failed tools become warnings + reduced confidence, never fabricated data.

## Containment rules

- Classify errors by code and `retryable`, never by parsing messages. `message` is safe fixed text; `detail` is internal only.
- Catching limits propagation, not mutations: only the owner of the affected state may decide it is safe to continue. Otherwise stop dependent work and visibly report untrusted/unknown state.
- Retries require safe repetition, cancellation, and an owner; bound finite operations, supervise ongoing ones.
- Never claim success, rollback, or safe retry without a guarantee.

## Verification

Tests in `tests/test_enhancements.py` and `tests/test_daily_optimizations.py` cover: circuit-open returns None with no network call, fallback inside tool calls, offline composite orchestration, ghost-memory suppression on cancel, cross-day date handling. To check offline end-to-end:

```sh
env -u OPENAI_API_KEY LLM_MODE=offline USE_CHECKPOINTER=false python -m unittest discover -s tests -v
```

Then run the full [eval-regression](../eval-regression/SKILL.md) gate. A change that makes any test require a Key has broken the guarantee.

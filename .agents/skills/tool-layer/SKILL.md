---
name: tool-layer
description: Use when adding, modifying, or reviewing a tool in crm-sales-agent's unified registry — covering ToolSpec fields, parameter schema validation, retry and fallback policy, $ref dependency orchestration, ctx injection by explicit signature, tool metrics, and result reuse in composite tasks.
---

# Tool layer

All capabilities are registered once in `tools/bootstrap.py` and consumed by name. A caller never imports a store or knowledge-base function directly for a new capability — it declares a tool. See design-notes §18–21, §29–30 for the rationale.

## Register a tool

Add a `ToolSpec` inside `register_builtin_tools(registry, ctx)` using the local `add()` helper (`bootstrap.py:26-140`). `add()` is idempotent: it skips names already registered. `ToolSpec` fields (`tools/registry.py:177-189`):

| Field | Meaning |
|---|---|
| `name` | stable identifier, dot-namespaced (`advertiser.get`, `kb.retrieve`, `sql.generate`) |
| `description` | human/planner-readable purpose |
| `handler` | callable; declare `ctx` explicitly only if it needs the runtime container |
| `parameters` | dict of param rules via `P(kind, description, **rules)` |
| `source` | origin tag, default `local` |
| `timeout_s` | `0` = no extra timeout wrapper |
| `retries` | extra attempts beyond the first |
| `fallback` | callable used when all attempts fail |
| `allow_extra` | default False — unexpected params are rejected |
| `enabled` | toggle |
| `tags` | grouping |

A new tool needs **no** change to `Context`, nodes, or docs to become callable; the planner discovers it through `registry.describe()` and `registry.names()`. Keep the tool count and names in the API `/api/tools` response current.

## Parameter schema (`tools/registry.py:69-140`)

Supported rule keys: `type`, `required`, `default`, `enum`, `min`, `max`, `min_len`, `max_len`, `pattern`. Types: `str/int/float/bool/list/dict`; `int` excludes bool; `float` accepts int. Validation runs before the handler and returns **all** errors at once (never raises to the caller). A malformed regex logs a warning and does not reject the call.

- External identifiers must be constrained at the entrance: `advertiser_id` uses `^A\d{3}$` (`bootstrap.py:22`).
- Validation failure yields `INVALID_PARAMETERS` with `attempts=0` — no retry, because the input will not change.
- `ValueError/TypeError/KeyError/AttributeError` from a handler are also classified `INVALID_PARAMETERS` and stop immediately (`registry.py:322-330`). Do not retry logic/config errors.

## Result contract

Every call returns `ToolResult` (`registry.py:144-173`): `tool / ok / data / error (safe text) / error_code / detail (internal only) / source / latency_ms / attempts / degraded / warnings / step_id`. `to_dict()` omits `detail`. On fallback success: `ok=True, degraded=True, source="<source>:fallback"`, with a warning naming the primary failure code. On total failure: `ToolResult.failure()` from an `AgentError`. Never put internal detail into `error`.

## Retry, timeout, fallback (`registry.py:292-373`)

- Order: registry lookup → `validate_params` → up to `1+retries` attempts → `fallback` → final `ToolResult.failure()`.
- Retry only errors whose `retryable=True` (see `errors.py:_RETRYABLE`: `TOOL_TIMEOUT/TOOL_FAILED/EXTERNAL_SERVICE_DOWN/DATA_UNAVAILABLE/INTERNAL_ERROR`).
- Timeout uses a shared 8-worker `ThreadPoolExecutor` + `Future.result(timeout)`. On timeout the call returns `TOOL_TIMEOUT` immediately but the worker is **not** force-killed (Python cannot safely kill threads). Keep handlers read-only and idempotent.
- `configure()` applies timeout/retry/fallback policy in bulk from `Settings`; tests can disable fallback globally.
- Fallbacks in `bootstrap.py` return safe empty values: `kb.retrieve→[]`, `kb.stats→zero`, `sql.generate→(None,"none")`, `sql.execute→[]`.

## ctx injection — signature only

`registry.py:56-66` uses `inspect.signature(handler)` (cached with `lru_cache`) to decide whether to pass `ctx=`. **Only an explicit `ctx` parameter counts; `**kwargs` does not.** The old approach — catching a TypeError whose text mentions "unexpected keyword argument 'ctx'" — could re-execute a handler that itself raised that message, causing duplicate side effects. Do not reintroduce it.

## Dependency orchestration (`registry.py:386-477`)

Steps: `{"id":"s1","tool":"...","params":{...},"depends_on":[],"optional":False}`. In `params`, `{"$ref":"s1.rows.0"}` resolves recursively against an upstream result's `data`. Upstream failure marks the downstream `DEPENDENCY_FAILED` and skips its execution; cycles are detected and failed. `$ref` is for wiring real data dependencies, not for copying large blobs.

## Result reuse in composite tasks (`nodes/orchestrate.py`)

`orchestrate_node` pre-seeds successful `sql.generate` / `sql.execute` / `kb.retrieve` results into sub-task state so nodes reuse them instead of re-querying. Two guards are mandatory:

1. **Degraded values are never reused** — an empty fallback must not overwrite a node's real data.
2. **Follow-ups are never reused** — `_sql_question()` rewrites the question from history, so it no longer matches the tool's input.

Violating either reintroduces duplicate LLM/SQL/BM25 work and inconsistent results (see design-notes §29).

## Verification

Add a regression test in `tests/test_enhancements.py` or `tests/test_reuse_and_robustness.py` covering: param validation rejects bad input without calling the handler; a retryable failure is retried, a non-retryable one is not; fallback marks `degraded`; dependency failure propagates; ctx injection happens once. Then run the full [eval-regression](../eval-regression/SKILL.md) gate — tool changes must leave all 37 answers unchanged.

---
name: tool-layer
description: Use when adding, modifying, or reviewing a tool in crm-sales-agent's unified registry — covering ToolSpec fields, parameter schema validation, retry and fallback policy, $ref dependency orchestration, ctx injection by explicit signature, tool metrics, and result reuse in composite tasks. | 在 crm-sales-agent 统一工具层中新增、修改或审查工具时使用，覆盖 ToolSpec 字段、参数 schema 校验、重试与回退策略、$ref 依赖编排、显式签名 ctx 注入、工具指标与复合任务结果复用。
---

# Tool layer / 工具层

<!-- EN -->
All capabilities are registered once in `tools/bootstrap.py` and consumed by name. A caller never imports a store or knowledge-base function directly for a new capability — it declares a tool. See design-notes §18–21, §29–30 for the rationale.

<!-- 中文 -->
所有能力在 `tools/bootstrap.py` 中一次性注册，按名字取用。新增能力时调用方不得直接导入 store 或知识库函数——应声明为一个工具。理由见 design-notes §18–21、§29–30。

> Both languages are authoritative; English comes first (`<!-- EN -->`), Chinese follows (`<!-- 中文 -->`). / 两种语言同等有效；英文在前，中文在后。

## Register a tool / 注册工具

<!-- EN -->
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

<!-- 中文 -->
在 `register_builtin_tools(registry, ctx)` 中用局部 `add()` 辅助函数添加 `ToolSpec`（`bootstrap.py:26-140`）。`add()` 是幂等的：已注册的名字会被跳过。`ToolSpec` 字段（`tools/registry.py:177-189`）：

| 字段 | 含义 |
|---|---|
| `name` | 稳定标识，点号命名空间（`advertiser.get`、`kb.retrieve`、`sql.generate`） |
| `description` | 人/规划器可读的用途说明 |
| `handler` | 可调用对象；仅当需要运行时容器时才显式声明 `ctx` |
| `parameters` | 通过 `P(kind, description, **rules)` 定义的参数规则字典 |
| `source` | 来源标签，默认 `local` |
| `timeout_s` | `0` = 不套额外超时包装 |
| `retries` | 首次之外的额外尝试次数 |
| `fallback` | 所有尝试失败后使用的备用函数 |
| `allow_extra` | 默认 False——多余参数会被拒绝 |
| `enabled` | 开关 |
| `tags` | 分组标签 |

新工具**无需**修改 `Context`、节点或文档即可被调用；规划器通过 `registry.describe()` 与 `registry.names()` 自动发现。请同步更新 API `/api/tools` 响应中的工具数量与名字。

## Parameter schema / 参数 schema（`tools/registry.py:69-140`）

<!-- EN -->
Supported rule keys: `type`, `required`, `default`, `enum`, `min`, `max`, `min_len`, `max_len`, `pattern`. Types: `str/int/float/bool/list/dict`; `int` excludes bool; `float` accepts int. Validation runs before the handler and returns **all** errors at once (never raises to the caller). A malformed regex logs a warning and does not reject the call.

- External identifiers must be constrained at the entrance: `advertiser_id` uses `^A\d{3}$` (`bootstrap.py:22`).
- Validation failure yields `INVALID_PARAMETERS` with `attempts=0` — no retry, because the input will not change.
- `ValueError/TypeError/KeyError/AttributeError` from a handler are also classified `INVALID_PARAMETERS` and stop immediately (`registry.py:322-330`). Do not retry logic/config errors.

<!-- 中文 -->
支持的规则键：`type`、`required`、`default`、`enum`、`min`、`max`、`min_len`、`max_len`、`pattern`。类型：`str/int/float/bool/list/dict`；`int` 排除 bool；`float` 接受 int。校验在 handler 之前执行，一次性返回**全部**错误（绝不向调用方抛异常）。正则写错只记 warning，不拒绝调用。

- 外部标识必须在入口处约束：`advertiser_id` 使用 `^A\d{3}$`（`bootstrap.py:22`）。
- 校验失败返回 `INVALID_PARAMETERS` 且 `attempts=0`——不重试，因为输入不会改变。
- handler 抛出的 `ValueError/TypeError/KeyError/AttributeError` 也归为 `INVALID_PARAMETERS` 并立即中止（`registry.py:322-330`）。不要对逻辑/配置错误重试。

## Result contract / 结果契约

<!-- EN -->
Every call returns `ToolResult` (`registry.py:144-173`): `tool / ok / data / error (safe text) / error_code / detail (internal only) / source / latency_ms / attempts / degraded / warnings / step_id`. `to_dict()` omits `detail`. On fallback success: `ok=True, degraded=True, source="<source>:fallback"`, with a warning naming the primary failure code. On total failure: `ToolResult.failure()` from an `AgentError`. Never put internal detail into `error`.

<!-- 中文 -->
每次调用都返回 `ToolResult`（`registry.py:144-173`）：`tool / ok / data / error（安全文案） / error_code / detail（仅内部） / source / latency_ms / attempts / degraded / warnings / step_id`。`to_dict()` 会省略 `detail`。回退成功时：`ok=True, degraded=True, source="<source>:fallback"`，并附一条注明主失败码的 warning。完全失败时由 `AgentError` 生成 `ToolResult.failure()`。绝不可把内部细节放进 `error`。

## Retry, timeout, fallback / 重试、超时、回退（`registry.py:292-373`）

<!-- EN -->
- Order: registry lookup → `validate_params` → up to `1+retries` attempts → `fallback` → final `ToolResult.failure()`.
- Retry only errors whose `retryable=True` (see `errors.py:_RETRYABLE`: `TOOL_TIMEOUT/TOOL_FAILED/EXTERNAL_SERVICE_DOWN/DATA_UNAVAILABLE/INTERNAL_ERROR`).
- Timeout uses a shared 8-worker `ThreadPoolExecutor` + `Future.result(timeout)`. On timeout the call returns `TOOL_TIMEOUT` immediately but the worker is **not** force-killed (Python cannot safely kill threads). Keep handlers read-only and idempotent.
- `configure()` applies timeout/retry/fallback policy in bulk from `Settings`; tests can disable fallback globally.
- Fallbacks in `bootstrap.py` return safe empty values: `kb.retrieve→[]`, `kb.stats→zero`, `sql.generate→(None,"none")`, `sql.execute→[]`.

<!-- 中文 -->
- 顺序：注册中心查找 → `validate_params` → 最多 `1+retries` 次尝试 → `fallback` → 最终 `ToolResult.failure()`。
- 仅重试 `retryable=True` 的错误（见 `errors.py:_RETRYABLE`：`TOOL_TIMEOUT/TOOL_FAILED/EXTERNAL_SERVICE_DOWN/DATA_UNAVAILABLE/INTERNAL_ERROR`）。
- 超时使用共享的 8 worker `ThreadPoolExecutor` + `Future.result(timeout)`。超时立即返回 `TOOL_TIMEOUT`，但**不会**强杀工作线程（Python 无法安全杀线程）。handler 须保持只读且幂等。
- `configure()` 从 `Settings` 批量套用超时/重试/回退策略；测试可全局关闭回退。
- `bootstrap.py` 中的回退返回安全的空值：`kb.retrieve→[]`、`kb.stats→zero`、`sql.generate→(None,"none")`、`sql.execute→[]`。

## ctx injection — signature only / ctx 注入——仅看签名

<!-- EN -->
`registry.py:56-66` uses `inspect.signature(handler)` (cached with `lru_cache`) to decide whether to pass `ctx=`. **Only an explicit `ctx` parameter counts; `**kwargs` does not.** The old approach — catching a TypeError whose text mentions "unexpected keyword argument 'ctx'" — could re-execute a handler that itself raised that message, causing duplicate side effects. Do not reintroduce it.

<!-- 中文 -->
`registry.py:56-66` 用 `inspect.signature(handler)`（由 `lru_cache` 缓存）判断是否传入 `ctx=`。**只有显式 `ctx` 参数算数，`**kwargs` 不算。** 旧做法靠捕获文案含 "unexpected keyword argument 'ctx'" 的 TypeError 来猜测；若 handler 自身抛出同样文案就会重复执行，造成副作用。不要重新引入这种做法。

## Dependency orchestration / 依赖编排（`registry.py:386-477`）

<!-- EN -->
Steps: `{"id":"s1","tool":"...","params":{...},"depends_on":[],"optional":False}`. In `params`, `{"$ref":"s1.rows.0"}` resolves recursively against an upstream result's `data`. Upstream failure marks the downstream `DEPENDENCY_FAILED` and skips its execution; cycles are detected and failed. `$ref` is for wiring real data dependencies, not for copying large blobs.

<!-- 中文 -->
步骤格式：`{"id":"s1","tool":"...","params":{...},"depends_on":[],"optional":False}`。`params` 中的 `{"$ref":"s1.rows.0"}` 会针对上游结果的 `data` 递归解析。上游失败会把下游标记为 `DEPENDENCY_FAILED` 并跳过执行；检测到环会判失败。`$ref` 用于连接真实数据依赖，不用于复制大块数据。

## Result reuse in composite tasks / 复合任务中的结果复用（`nodes/orchestrate.py`）

<!-- EN -->
`orchestrate_node` pre-seeds successful `sql.generate` / `sql.execute` / `kb.retrieve` results into sub-task state so nodes reuse them instead of re-querying. Two guards are mandatory:

1. **Degraded values are never reused** — an empty fallback must not overwrite a node's real data.
2. **Follow-ups are never reused** — `_sql_question()` rewrites the question from history, so it no longer matches the tool's input.

Violating either reintroduces duplicate LLM/SQL/BM25 work and inconsistent results (see design-notes §29).

<!-- 中文 -->
`orchestrate_node` 把成功的 `sql.generate` / `sql.execute` / `kb.retrieve` 结果预置进子任务状态，让节点直接复用而非重复取数。两条保护必须遵守：

1. **降级值绝不复用**——空的回退结果不得覆盖节点的真实数据。
2. **追问绝不复用**——`_sql_question()` 会依据历史改写问题，改写后的文本与工具入参不再一致。

违反任一条都会重新引入重复的 LLM/SQL/BM25 工作与不一致结果（见 design-notes §29）。

## Verification / 验证

<!-- EN -->
Add a regression test in `tests/test_enhancements.py` or `tests/test_reuse_and_robustness.py` covering: param validation rejects bad input without calling the handler; a retryable failure is retried, a non-retryable one is not; fallback marks `degraded`; dependency failure propagates; ctx injection happens once. Then run the full [eval-regression](../eval-regression/SKILL.md) gate — tool changes must leave all 37 answers unchanged.

<!-- 中文 -->
在 `tests/test_enhancements.py` 或 `tests/test_reuse_and_robustness.py` 添加回归测试，覆盖：参数校验拒绝非法输入且不调用 handler；可重试错误被重试、不可重试错误不被重试；回退标记 `degraded`；依赖失败会传播；ctx 注入只发生一次。随后跑完整的 [eval-regression](../eval-regression/SKILL.md) 门禁——工具改动必须让 37 条答案保持不变。

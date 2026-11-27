---
name: offline-resilience
description: Use when working on model calls, circuit breaking, timeouts, retries, graceful degradation, request handling, or production stability in crm-sales-agent. Covers LLM fallback semantics, tool timeouts without thread killing, request timeout vs ghost memory, rate limiting, resource cleanup, and the guarantee that core functions run with no network or external services. | 在 crm-sales-agent 中改动模型调用、熔断、超时、重试、优雅降级、请求处理或生产稳定性时使用，覆盖 LLM 降级语义、不强杀线程的工具超时、请求超时与幽灵记忆、限流、资源清理，以及无网络/无外部服务时核心功能仍可运行的保证。
---

# Offline resilience / 离线韧性

<!-- EN -->
The system must answer core questions with **no API Key, no network, and no external service**. The model is an optional enhancement, never a dependency. See design-notes §9–14, §19, §26 for rationale.

<!-- 中文 -->
系统必须在**没有 API Key、没有网络、没有外部服务**的情况下回答核心问题。模型是可选增强，绝不是依赖。理由见 design-notes §9–14、§19、§26。

> Both languages are authoritative; English comes first (`<!-- EN -->`), Chinese follows (`<!-- 中文 -->`). / 两种语言同等有效；英文在前，中文在后。

## LLM degradation contract / LLM 降级契约（`llm.py`）

<!-- EN -->
- `Settings.llm_enabled` is False when `llm_mode=offline` (regardless of Key) or when mode is `llm` with no Key. `mode` reports `llm`/`offline`.
- `LLM.chat()` returns `None` on any failure (init failure, timeout, circuit open, no Key). Callers must treat `None` as "use the rule/template path", never as an error to propagate.
- Circuit breaker: after `llm_cb_threshold` consecutive failures, open for `llm_cb_cooldown` seconds; half-open on expiry. While open, `chat()` returns `None` **without any network call**. `available` reflects state for health/metrics.
- Metrics record calls/failures/latency only; log `type(exc).__name__`, never the Key or response body.
- Every node has a template fallback: `profile.py:183`, `opportunity.py:253`, `analysis.py:247`, `knowledge.py:71`. Adding a node without a fallback breaks the offline guarantee.

<!-- 中文 -->
- 当 `llm_mode=offline`（无论是否有 Key），或模式为 `llm` 但无 Key 时，`Settings.llm_enabled` 为 False。`mode` 报告 `llm`/`offline`。
- `LLM.chat()` 在任何失败（初始化失败、超时、熔断打开、无 Key）时返回 `None`。调用方必须把 `None` 当作"走规则/模板路径"，绝不可当成要向上抛的错误。
- 熔断器：连续 `llm_cb_threshold` 次失败后打开 `llm_cb_cooldown` 秒；到期后半开。熔断期间 `chat()` 返回 `None`，**不发任何网络调用**。`available` 反映状态供 health/metrics 使用。
- 指标只记录调用数/失败数/延迟；日志只记 `type(exc).__name__`，绝不记 Key 或响应体。
- 每个节点都有模板兜底：`profile.py:183`、`opportunity.py:253`、`analysis.py:247`、`knowledge.py:71`。新增没有兜底的节点会破坏离线保证。

## Request timeout vs ghost memory / 请求超时与幽灵记忆（`api/server.py`、`agent.py`）

<!-- EN -->
`POST /api/chat` wraps `agent.ask` in `asyncio.wait_for(..., settings.request_timeout)`. On timeout the client gets 504 — but `wait_for` cannot stop the worker thread already running in `to_thread`. Without care, that hidden answer gets written to conversation memory, and the next follow-up resolves references against something the user never saw ("ghost memory").

`SalesAgent.ask()` accepts an optional `cancel_event: threading.Event`. The API sets it on timeout; `ask()` checks it before `memory.append_turn` and skips the write. Old callers (CLI, eval, tests) omit it and are unaffected. This covers the dominant slow-LLM case; extreme races are acceptable. Do not write a failed/degraded (`intent="error"`) answer into memory either.

<!-- 中文 -->
`POST /api/chat` 用 `asyncio.wait_for(..., settings.request_timeout)` 包裹 `agent.ask`。超时后客户端收到 504——但 `wait_for` 无法停止已经在 `to_thread` 中运行的工作线程。若不处理，这个用户看不到的答案会被写进会话记忆，下一轮追问会基于用户从未见过的内容做指代消解（"幽灵记忆"）。

`SalesAgent.ask()` 接受可选的 `cancel_event: threading.Event`。API 超时时置位；`ask()` 在 `memory.append_turn` 前检查该事件并跳过写入。旧调用方（CLI、评估、测试）不传此参数，行为不受影响。这覆盖了占主导的慢 LLM 场景；极端竞态可接受。同样不要把失败/降级（`intent="error"`）的答案写进记忆。

## Tool timeout without thread killing / 工具超时但不强杀线程（`tools/registry.py:358-373`）

<!-- EN -->
`Future.result(timeout)` returns `TOOL_TIMEOUT` promptly, but the worker is not force-terminated — Python cannot safely kill threads, and a killed thread could leave half-written state. Keep handlers read-only/idempotent and bound the worker pool (8). SQLite connections are thread-local, so connection count is bounded by worker count.

<!-- 中文 -->
`Future.result(timeout)` 会立即返回 `TOOL_TIMEOUT`，但工作线程不会被强制终止——Python 无法安全杀线程，被杀的线程可能留下半写状态。handler 须保持只读/幂等，worker 池固定为 8。SQLite 连接是线程本地的，因此连接数上界就是 worker 数。

## Other degradation paths / 其他降级路径（全部必须成立）

<!-- EN -->
- `context.py`: knowledge-base load failure → `_EmptyKnowledgeBase` (retrieve returns `[]`), service still starts. Advertiser-prefix map build failure → `{}`, router falls back to the full-table query.
- `memory.py`: SQLite persistence failure → in-memory only, warning logged; `close()`/`clear()` still work.
- `store.py`: `close()` resets thread-local connections so the same thread reopens a fresh `mode=ro` connection.
- `graph.py`: session eviction failure is debug-logged and never affects an answer.
- `config.py`/`scoring.py`: out-of-range config/thresholds fall back per item; the service must always start.
- `orchestrate.py`: tool orchestration exception → pure node execution; a sub-task failure is isolated into a safe error result without dropping the other sub-answers.
- `integration.py`: failed tools become warnings + reduced confidence, never fabricated data.

<!-- 中文 -->
- `context.py`：知识库加载失败 → `_EmptyKnowledgeBase`（retrieve 返回 `[]`），服务仍启动。客户名前缀映射构建失败 → `{}`，router 回退到全表查询。
- `memory.py`：SQLite 持久化失败 → 退化为纯内存并记 warning；`close()`/`clear()` 仍可用。
- `store.py`：`close()` 重置线程本地连接，使同一线程后续重新打开新的 `mode=ro` 连接。
- `graph.py`：会话淘汰失败只记 debug 日志，绝不影响答案。
- `config.py`/`scoring.py`：越界配置/阈值逐项回落；服务必须始终能启动。
- `orchestrate.py`：工具编排异常 → 退化为纯节点执行；子任务失败被隔离为安全的错误结果，不会丢掉其他子答案。
- `integration.py`：工具失败变成 warnings + 降低置信度，绝不编造数据。

## Containment rules / 隔离规则

<!-- EN -->
- Classify errors by code and `retryable`, never by parsing messages. `message` is safe fixed text; `detail` is internal only.
- Catching limits propagation, not mutations: only the owner of the affected state may decide it is safe to continue. Otherwise stop dependent work and visibly report untrusted/unknown state.
- Retries require safe repetition, cancellation, and an owner; bound finite operations, supervise ongoing ones.
- Never claim success, rollback, or safe retry without a guarantee.

<!-- 中文 -->
- 按错误码与 `retryable` 分类，绝不解析异常文案。`message` 是固定安全文案；`detail` 仅供内部使用。
- 捕获只是限制传播，不等于撤销已发生的变更：只有受影响状态的所有者才能决定是否安全继续；否则停止依赖工作，并显式报告不可信/未知状态。
- 重试要求可安全重复、可取消且有责任方；有限操作要有上界，持续操作要有监督。
- 没有保证就不要声称成功、已回滚或可安全重试。

## Verification / 验证

<!-- EN -->
Tests in `tests/test_enhancements.py` and `tests/test_daily_optimizations.py` cover: circuit-open returns None with no network call, fallback inside tool calls, offline composite orchestration, ghost-memory suppression on cancel, cross-day date handling. To check offline end-to-end:

```sh
env -u OPENAI_API_KEY LLM_MODE=offline USE_CHECKPOINTER=false python -m unittest discover -s tests -v
```

Then run the full [eval-regression](../eval-regression/SKILL.md) gate. A change that makes any test require a Key has broken the guarantee.

<!-- 中文 -->
`tests/test_enhancements.py` 与 `tests/test_daily_optimizations.py` 中的测试覆盖：熔断打开时返回 None 且无网络调用、工具调用内的回退、离线复合编排、取消时抑制幽灵记忆、跨天日期处理。端到端检查离线：

```sh
env -u OPENAI_API_KEY LLM_MODE=offline USE_CHECKPOINTER=false python -m unittest discover -s tests -v
```

随后跑完整的 [eval-regression](../eval-regression/SKILL.md) 门禁。任何使测试需要 Key 的改动都破坏了这条保证。

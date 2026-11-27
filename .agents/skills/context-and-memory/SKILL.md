---
name: context-and-memory
description: Use when changing conversation memory, multi-turn follow-ups, pronoun/intent resolution, context slots, planning, or composite task orchestration in crm-sales-agent. Covers ConversationMemory persistence, history prompt assembly, rule-based reference resolution, slot extraction, the single-intent-vs-composite boundary, and ghost-memory suppression. | 在 crm-sales-agent 中改动对话记忆、多轮追问、指代/意图消解、上下文槽位、规划或复合任务编排时使用，覆盖 ConversationMemory 持久化、历史提示词组装、规则式指代消解、槽位抽取、单意图与复合任务边界及幽灵记忆抑制。
---

# Context & memory / 上下文与记忆

<!-- EN -->
Multi-turn context makes follow-ups meaningful ("他现在有什么商机", "那转化率呢") without letting the model overwrite facts. See design-notes §6, §12–14, §20, §31 for rationale.

<!-- 中文 -->
多轮上下文让追问（"他现在有什么商机"、"那转化率呢"）有意义，同时不让模型改写事实。理由见 design-notes §6、§12–14、§20、§31。

> Both languages are authoritative; English comes first (`<!-- EN -->`), Chinese follows (`<!-- 中文 -->`). / 两种语言同等有效；英文在前，中文在后。

## State rule / 状态规则（`state.py`）

<!-- EN -->
`AgentState` holds only structured facts (intent, advertiser_id, evidence, citations, trace, plan, retry, history summary) — **not** raw dialogue. `SalesAgent.ask()` resets `advertiser_id` and `trace` each turn (`agent.py:52-58`) to prevent cross-turn leakage.

<!-- 中文 -->
`AgentState` 只保存结构化事实（intent、advertiser_id、evidence、citations、trace、plan、retry、历史摘要）——**不**放对话原文。`SalesAgent.ask()` 每轮重置 `advertiser_id` 与 `trace`（`agent.py:52-58`），防止跨轮泄漏。

## ConversationMemory / 对话记忆（`memory.py`）

<!-- EN -->
- `Turn`: `role` (user/assistant), `content`, `intent`, `advertiser_id`, `ts`. One Q&A round writes **two** turns (user + assistant) — do not assume one.
- Public API: `get(session_id)` (returns turns and LRU `move_to_end`), `append_turn(session_id, question, answer, intent, advertiser_id)`, `clear`, `clear_all`, `stats`, `close`.
- `append_turn` truncates the question to 500 chars and the answer summary to `answer_chars` (240). Persistence writes the **whole session** — DELETE all rows for the session then re-INSERT (`memory.py:140-163`). Incremental deletion breaks when turn trimming shifts `seq` forward and misses/over-deletes rows. Keep this strategy.
- Persistence DB: `data/conversations.db`, table `conversation_turns`, key `(session_id, seq)`. Failure degrades to in-memory with a warning. Session count over `max_sessions` evicts LRU and deletes its rows.
- Only successful answers (`intent != "error"`) and non-cancelled requests are recorded. See [offline-resilience](../offline-resilience/SKILL.md) for ghost-memory suppression.

<!-- 中文 -->
- `Turn`：`role`（user/assistant）、`content`、`intent`、`advertiser_id`、`ts`。一轮问答写入**两条** Turn（用户 + 助手）——不要假设只有一条。
- 公开 API：`get(session_id)`（返回轮次并执行 LRU `move_to_end`）、`append_turn(session_id, question, answer, intent, advertiser_id)`、`clear`、`clear_all`、`stats`、`close`。
- `append_turn` 把问题截断到 500 字、回答摘要截断到 `answer_chars`（240）。持久化写入**整个会话**——先 DELETE 该 session 全部行，再逐条 INSERT（`memory.py:140-163`）。轮次裁剪会让 `seq` 前移，增量删除会漏删/错删旧行。保持这一策略。
- 持久化 DB：`data/conversations.db`，表 `conversation_turns`，主键 `(session_id, seq)`。失败时退化为纯内存并记 warning。会话数超过 `max_sessions` 时按 LRU 淘汰并删除其行。
- 只记录成功答案（`intent != "error"`）且未被取消的请求。幽灵记忆抑制见 [offline-resilience](../offline-resilience/SKILL.md)。

## History prompt / 历史提示词（`build_history_prompt`，`memory.py:205-230`）

<!-- EN -->
Walks newest→oldest, includes each user question + answer summary, capped by `max_chars`. An oversized turn is **skipped with `continue`, not `break`** — a `break` would drop all older turns, including the short one that often carries the reference target ("A003 呢"). Numbers in history are context only; the current turn's tool results remain authoritative.

<!-- 中文 -->
从新到旧遍历，包含每条用户问题 + 回答摘要，受 `max_chars` 上限约束。超长轮次用 **`continue` 跳过，而非 `break`**——`break` 会丢掉所有更早轮次，包括常承载指代对象的短问句（"A003 呢"）。历史中的数字只是上下文；本轮工具结果仍是权威。

## Rule-based reference & intent resolution / 规则式指代与意图消解（`nodes/router.py`）

<!-- EN -->
Pronouns and follow-ups ("他/这家/继续/还有呢/那转化率呢") are resolved by rules, not by the model: inherit `advertiser_id` and intent from the last turn only when the current text has no explicit target. A batch question ("分别看下 A003 和 A005") must not bind to the previous customer. `advertiser_prefixes` — a startup-built ordered map of name-prefix(3 chars)→ID — resolves colloquial names without a per-request full-table scan; build failure falls back to the original query. Do not reorder it: `setdefault` preserves the consumption-descending first match.

<!-- 中文 -->
代词与追问（"他/这家/继续/还有呢/那转化率呢"）由规则而非模型消解：仅当当前文本没有显式对象时才继承上一轮的 `advertiser_id` 与意图。批量问题（"分别看下 A003 和 A005"）不得绑定到上一轮客户。`advertiser_prefixes` 是启动期构建的"名字前 3 字 → ID"有序映射，无需每次请求全表扫描即可解析口语化名称；构建失败时回退到原查询。不要改变顺序：`setdefault` 保留了按消耗降序的第一个匹配。

## Context slots & planning / 上下文槽位与规划（`planning.py`）

<!-- EN -->
`extract_slots()` produces `turns / advertiser_id / intent / metrics (≤8) / window / last_advice (≤120 chars)`; `slots_prompt()` formats them for **online** prompts only (offline templates do not assemble them). `Planner.plan()`:

- Splits compounds on `同时/并且/另外/此外/顺便/以及/再帮我/还要/分别/；;`, filters sub-clauses by intent signal, then classifies each.
- Sets `mode="composite"` only when there are ≥2 **different** intents and a sub-task count in `[2, max_steps]` (default 4); otherwise it returns single-intent.
- Recommends tools by `registry.names()` — unregistered tools never enter a plan. Missing customer → `clarify`.
- Confidence starts at 0.8 (smalltalk 0.9), −0.12 for a customer-level missing object, +0.1 with context, −0.05 for composite, clamped [0.3, 0.99].

**Hard boundary**: `graph._dispatch()` switches to the orchestrate node **only** for `plan.mode=="composite"` and ≥2 sub-tasks. Every single-intent change must leave answers byte-identical (see [eval-regression](../eval-regression/SKILL.md)). This is why "smart" planning never rewrites the 37 stable answers.

<!-- 中文 -->
`extract_slots()` 产出 `turns / advertiser_id / intent / metrics（≤8） / window / last_advice（≤120 字）`；`slots_prompt()` 仅为**在线**提示词格式化它们（离线模板不组装槽位）。`Planner.plan()`：

- 按 `同时/并且/另外/此外/顺便/以及/再帮我/还要/分别/；;` 拆分复合句，按意图信号过滤子句，然后逐条分类。
- 仅当存在 ≥2 个**不同**意图且子任务数在 `[2, max_steps]`（默认 4）内时才设 `mode="composite"`，否则返回单意图。
- 通过 `registry.names()` 推荐工具——未注册的工具不会进入计划。缺少客户 → `clarify`。
- 置信度初始 0.8（smalltalk 0.9），客户级对象缺失 −0.12，有上下文 +0.1，复合 −0.05，钳制在 [0.3, 0.99]。

**硬边界**：`graph._dispatch()` **仅**在 `plan.mode=="composite"` 且子任务 ≥2 时切到编排节点。任何单意图改动都必须让答案逐字节一致（见 [eval-regression](../eval-regression/SKILL.md)）。这正是"聪明"的规划从不改写 37 条稳定答案的原因。

## Composite orchestration / 复合任务编排（`nodes/orchestrate.py`）

<!-- EN -->
Translates sub-tasks into a tool plan with `$ref` deps, isolates each sub-task failure into a safe error result, then merges via `integration.integrate()` into a structured answer (summary / sections / citations / confidence / next steps / warnings). Reuse pre-fetched tool results per the rules in [tool-layer](../tool-layer/SKILL.md). Prefix regression PFX-04 pins that memory inheritance still refuses H07-style custom windows.

<!-- 中文 -->
把子任务翻译成带 `$ref` 依赖的工具计划，将每个子任务失败隔离为安全的错误结果，再通过 `integration.integrate()` 合并为结构化答案（摘要 / 分段 / 引用 / 置信度 / 下一步 / 警告）。按 [tool-layer](../tool-layer/SKILL.md) 的规则复用预取的工具结果。前缀回归 PFX-04 固定了记忆继承后仍须拒绝 H07 式自定义窗口。

## Verification / 验证

<!-- EN -->
Tests: `tests/test_memory.py` (10), `tests/test_enhancements.py` planning cases, prefix cases in eval. Add a follow-up regression whenever resolution behavior changes, then run the full eval gate including prefix 4/4.

<!-- 中文 -->
测试：`tests/test_memory.py`（10 项）、`tests/test_enhancements.py` 中的规划用例、评估中的前缀用例。消解行为变化时补充追问回归测试，然后跑完整评估门禁（含前缀 4/4）。

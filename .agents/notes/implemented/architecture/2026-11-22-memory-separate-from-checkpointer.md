# Agent Note: 上下文记忆与 LangGraph checkpointer 解耦

> Agent Note: Decoupling Conversation Memory from the LangGraph Checkpointer
>
> Both languages are authoritative. Chinese sections are marked `<!-- 中文 -->`, English sections `<!-- EN -->`. Section headings stay in English verbatim for mechanical validation. / 两种语言同等有效；章节标题保持英文原样以通过机械校验。

Status: implemented

## Problem

<!-- 中文 -->
需要多轮对话记忆（指代消解、意图继承、追问补全），但 LangGraph 的 checkpointer 会把整图状态序列化进会话存储：体积大、每轮读写开销高，且其生命周期与"记住用户说过什么"不是一回事。生产模式希望关掉 checkpointer 降延迟和内存，却不能因此失去上下文能力。

<!-- EN -->
We need multi-turn memory (pronoun resolution, intent inheritance, follow-up completion), but LangGraph's checkpointer serializes the entire graph state into session storage: bulky, expensive to read/write each turn, and its lifecycle is not the same as "remember what the user said". Production wants checkpointer off to cut latency and memory, without losing conversational context.

## Decision

<!-- 中文 -->
独立实现 `ConversationMemory`（`src/crm_agent/memory.py`），与 checkpointer 完全解耦。`USE_CHECKPOINTER` 只控制图状态持久化，默认生产 `false`；记忆层由 `MEMORY_ENABLED` / `MEMORY_PERSIST` 单独控制。`SalesAgent.ask()` 在调用图之前注入历史，在图返回之后写入本轮问答。持久化用独立 SQLite 表 `conversation_turns`，采用**整会话重写**（DELETE 该 session 全部行再逐条 INSERT），而非增量删除。详细取舍见 `docs/design-notes.md` §10、§12、§14。

<!-- EN -->
Implement `ConversationMemory` (`src/crm_agent/memory.py`) independently, fully decoupled from the checkpointer. `USE_CHECKPOINTER` controls only graph-state persistence and defaults to `false` in production; the memory layer is controlled separately by `MEMORY_ENABLED` / `MEMORY_PERSIST`. `SalesAgent.ask()` injects history before invoking the graph and records this turn's Q&A after it returns. Persistence uses its own SQLite table `conversation_turns` with a **whole-session rewrite** (DELETE all rows for the session, then INSERT each one) rather than incremental deletion. Full rationale: `docs/design-notes.md` §10, §12, §14.

## Alternatives considered

### 为什么不用 checkpointer 直接存对话历史？ / Why not store dialogue history directly in the checkpointer?

<!-- 中文 -->
checkpointer 存的是完整 `AgentState`（evidence、trace、citations、plan…），远超"记住用户问题与回答摘要"的需要；把它当用户记忆会让每轮读写变重，且关掉它降延迟时记忆也一起没了。

<!-- EN -->
The checkpointer stores the full `AgentState` (evidence, trace, citations, plan…), far beyond what "remember the question and an answer summary" needs. Using it as user memory makes every turn heavier, and turning it off to cut latency removes memory along with it.

### 为什么不用增量删除/追加写？ / Why not incremental delete/append writes?

<!-- 中文 -->
轮次裁剪（`max_turns`）会让 `seq` 前移，增量删除会漏删或错删旧行，重启后历史条目数与裁剪结果不一致（实测 4 轮变 3 轮）。整会话重写以少量写换取确定性。

<!-- EN -->
Turn trimming (`max_turns`) shifts `seq` forward, so incremental deletion misses or over-deletes old rows: after a restart the history count disagrees with the trim result (observed 4 turns becoming 3). Whole-session rewrite trades a few extra writes for determinism.

### 为什么不让模型自己总结历史？ / Why not let the model summarize history itself?

<!-- 中文 -->
模型润色可能改写数字和事实，与"数字只能来自本轮工具返回"的硬原则冲突；历史只放用户问题 + 回答摘要，且提示词声明数字以本轮为准。

<!-- EN -->
Model paraphrasing can rewrite numbers and facts, conflicting with the hard rule that numbers may only come from this turn's tool results. History holds only the user question + answer summary, and the prompt states that this turn's numbers are authoritative.

## Consequences

<!-- 中文 -->
- 生产可在零 checkpointer 下保留完整多轮能力，延迟与内存下降。
- 记忆写入是每轮两次 DB 操作（先删后插）的会话级重写；会话轮数有 `memory_max_turns` 上界，成本可控。
- 用户可见的历史提示词超长轮次用 `continue` 跳过而非 `break`，代价是完全跳过装不下的长回答，但保住更早承载指代对象的短问句。
- 持久化失败退化为纯内存，只记 warning —— 记忆是增强项，不是服务可用性的前提。

<!-- EN -->
- Production keeps full multi-turn capability with zero checkpointer, with lower latency and memory.
- Each turn's memory write is a session-level rewrite of two DB operations (delete then insert); `memory_max_turns` bounds the cost.
- The history prompt skips oversized turns with `continue`, not `break`: long answers that do not fit are dropped entirely, but older short questions carrying the reference target survive.
- Persistence failure degrades to in-memory with a warning only — memory is an enhancement, not a prerequisite for service availability.

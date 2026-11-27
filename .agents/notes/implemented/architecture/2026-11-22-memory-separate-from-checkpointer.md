# Agent Note: 上下文记忆与 LangGraph checkpointer 解耦

Status: implemented

## Problem

需要多轮对话记忆（指代消解、意图继承、追问补全），但 LangGraph 的 checkpointer 会把整图状态序列化进会话存储：体积大、每轮读写开销高，且其生命周期与"记住用户说过什么"不是一回事。生产模式希望关掉 checkpointer 降延迟和内存，却不能因此失去上下文能力。

## Decision

独立实现 `ConversationMemory`（`src/crm_agent/memory.py`），与 checkpointer 完全解耦。`USE_CHECKPOINTER` 只控制图状态持久化，默认生产 `false`；记忆层由 `MEMORY_ENABLED` / `MEMORY_PERSIST` 单独控制。`SalesAgent.ask()` 在调用图之前注入历史，在图返回之后写入本轮问答。持久化用独立 SQLite 表 `conversation_turns`，采用**整会话重写**（DELETE 该 session 全部行再逐条 INSERT），而非增量删除。详细取舍见 `docs/design-notes.md` §10、§12、§14。

## Alternatives considered

### 为什么不用 checkpointer 直接存对话历史？

checkpointer 存的是完整 `AgentState`（evidence、trace、citations、plan…），远超"记住用户问题与回答摘要"的需要；把它当用户记忆会让每轮读写变重，且关掉它降延迟时记忆也一起没了。

### 为什么不用增量删除/追加写？

轮次裁剪（`max_turns`）会让 `seq` 前移，增量删除会漏删或错删旧行，重启后历史条目数与裁剪结果不一致（实测 4 轮变 3 轮）。整会话重写以少量写换取确定性。

### 为什么不让模型自己总结历史？

模型润色可能改写数字和事实，与"数字只能来自本轮工具返回"的硬原则冲突；历史只放用户问题 + 回答摘要，且提示词声明数字以本轮为准。

## Consequences

- 生产可在零 checkpointer 下保留完整多轮能力，延迟与内存下降。
- 记忆写入是每轮两次 DB 操作（先删后插）的会话级重写；会话轮数有 `memory_max_turns` 上界，成本可控。
- 用户可见的历史提示词超长轮次用 `continue` 跳过而非 `break`，代价是完全跳过装不下的长回答，但保住更早承载指代对象的短问句。
- 持久化失败退化为纯内存，只记 warning —— 记忆是增强项，不是服务可用性的前提。

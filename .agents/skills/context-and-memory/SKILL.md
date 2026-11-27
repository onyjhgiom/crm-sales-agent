---
name: context-and-memory
description: Use when changing conversation memory, multi-turn follow-ups, pronoun/intent resolution, context slots, planning, or composite task orchestration in crm-sales-agent. Covers ConversationMemory persistence, history prompt assembly, rule-based reference resolution, slot extraction, the single-intent-vs-composite boundary, and ghost-memory suppression.
---

# Context & memory

Multi-turn context makes follow-ups meaningful ("他现在有什么商机", "那转化率呢") without letting the model overwrite facts. See design-notes §6, §12–14, §20, §31 for rationale.

## State rule (`state.py`)

`AgentState` holds only structured facts (intent, advertiser_id, evidence, citations, trace, plan, retry, history summary) — **not** raw dialogue. `SalesAgent.ask()` resets `advertiser_id` and `trace` each turn (`agent.py:52-58`) to prevent cross-turn leakage.

## ConversationMemory (`memory.py`)

- `Turn`: `role` (user/assistant), `content`, `intent`, `advertiser_id`, `ts`. One Q&A round writes **two** turns (user + assistant) — do not assume one.
- Public API: `get(session_id)` (returns turns and LRU `move_to_end`), `append_turn(session_id, question, answer, intent, advertiser_id)`, `clear`, `clear_all`, `stats`, `close`.
- `append_turn` truncates the question to 500 chars and the answer summary to `answer_chars` (240). Persistence writes the **whole session** — DELETE all rows for the session then re-INSERT (`memory.py:140-163`). Incremental deletion breaks when turn trimming shifts `seq` forward and misses/over-deletes rows. Keep this strategy.
- Persistence DB: `data/conversations.db`, table `conversation_turns`, key `(session_id, seq)`. Failure degrades to in-memory with a warning. Session count over `max_sessions` evicts LRU and deletes its rows.
- Only successful answers (`intent != "error"`) and non-cancelled requests are recorded. See [offline-resilience](../offline-resilience/SKILL.md) for ghost-memory suppression.

## History prompt (`build_history_prompt`, `memory.py:205-230`)

Walks newest→oldest, includes each user question + answer summary, capped by `max_chars`. An oversized turn is **skipped with `continue`, not `break`** — a `break` would drop all older turns, including the short one that often carries the reference target ("A003 呢"). Numbers in history are context only; the current turn's tool results remain authoritative.

## Rule-based reference & intent resolution (`nodes/router.py`)

Pronouns and follow-ups ("他/这家/继续/还有呢/那转化率呢") are resolved by rules, not by the model: inherit `advertiser_id` and intent from the last turn only when the current text has no explicit target. A batch question ("分别看下 A003 和 A005") must not bind to the previous customer. `advertiser_prefixes` — a startup-built ordered map of name-prefix(3 chars)→ID — resolves colloquial names without a per-request full-table scan; build failure falls back to the original query. Do not reorder it: `setdefault` preserves the consumption-descending first match.

## Context slots & planning (`planning.py`)

`extract_slots()` produces `turns / advertiser_id / intent / metrics (≤8) / window / last_advice (≤120 chars)`; `slots_prompt()` formats them for **online** prompts only (offline templates do not assemble them). `Planner.plan()`:

- Splits compounds on `同时/并且/另外/此外/顺便/以及/再帮我/还要/分别/；;`, filters sub-clauses by intent signal, then classifies each.
- Sets `mode="composite"` only when there are ≥2 **different** intents and a sub-task count in `[2, max_steps]` (default 4); otherwise it returns single-intent.
- Recommends tools by `registry.names()` — unregistered tools never enter a plan. Missing customer → `clarify`.
- Confidence starts at 0.8 (smalltalk 0.9), −0.12 for a customer-level missing object, +0.1 with context, −0.05 for composite, clamped [0.3, 0.99].

**Hard boundary**: `graph._dispatch()` switches to the orchestrate node **only** for `plan.mode=="composite"` and ≥2 sub-tasks. Every single-intent change must leave answers byte-identical (see [eval-regression](../eval-regression/SKILL.md)). This is why "smart" planning never rewrites the 37 stable answers.

## Composite orchestration (`nodes/orchestrate.py`)

Translates sub-tasks into a tool plan with `$ref` deps, isolates each sub-task failure into a safe error result, then merges via `integration.integrate()` into a structured answer (summary / sections / citations / confidence / next steps / warnings). Reuse pre-fetched tool results per the rules in [tool-layer](../tool-layer/SKILL.md). Prefix regression PFX-04 pins that memory inheritance still refuses H07-style custom windows.

## Verification

Tests: `tests/test_memory.py` (10), `tests/test_enhancements.py` planning cases, prefix cases in eval. Add a follow-up regression whenever resolution behavior changes, then run the full eval gate including prefix 4/4.

---
name: sql-and-data
description: Use when touching Text-to-SQL, the read-only guard, data generation, metric definitions, or custom time-window handling in crm-sales-agent. Covers the single-SELECT whitelist, table-name parsing, ro connection, scope-boundary refusal, and the fixed-seed local dataset.
---

# SQL & data

The database is the truth source for advertising operations. Every query path is read-only and whitelisted; every business number must be reproducible offline. See design-notes §4, §7, §27 for rationale.

## Read-only guard — the only entry (`sql_agent.py:61-75`)

`guard(sql)` returns `(allowed, reason)`. A SQL is accepted only when:

1. Non-empty, single statement after stripping the trailing `;`.
2. No forbidden keywords (`insert/update/delete/drop/alter/create/attach/detach/pragma/vacuum/replace into/trigger`), checked case-insensitively.
3. Starts with `select`.
4. Every table extracted by `_table_names()` is in `ALLOWED_TABLES = {advertisers, campaigns, metrics, interactions}`.

`_table_names()` (`sql_agent.py:39-58`) must parse the **complete** table list after `FROM`/`JOIN`, including comma-separated multi-table forms (`FROM advertisers, sqlite_master` must be rejected), stopping at `WHERE/ON/JOIN/GROUP/ORDER/LIMIT`, and stripping comments and string literals first so `WHERE name='from sqlite_master'` is not mistaken for a table. Never narrow this parser to the first table. `replace` is matched only as `replace into` so legal columns like `replaced_at` are not flagged.

Execution goes through `run_readonly()` (`sql_agent.py:78-86`) using `file:<path>?mode=ro` — a second safety net independent of the keyword guard. Row caps: 50 detail, 200 aggregate. Do not raise these without a reason.

## Three-layer Text-to-SQL (`sql_agent.py`)

`nl_to_sql(question, llm, advertiser_id)` tries, in order: model generation (when available) → fixed templates (`template_sql`, :122-212) → scope refusal. The SQL is always passed through `guard()` before execution. The source (`llm`/`template`/`none`) is surfaced in evidence for traceability.

**Scope boundary is checked before model and template matching** (`unsupported_window`, :225-241): a custom window like "近 45 天" with N ≠ 30 must return `source="unsupported_window"` and a refusal answer, not a plausible-looking wrong number. The 30-day default and monthly trend wording must NOT be misclassified — `evals/prefix_cases.jsonl` PFX-02/03 pin this. H07 in `cases.jsonl` pins the refusal (forbid_substrings `1,464,090` / `1464090` / `户均消耗`). When adding a new window rule, add both a boundary case and a prefix case.

## Near-window and comparison wording

`_sql_question()` (`nodes/analysis.py:19-36`) rewrites a follow-up question using the previous turn's analysis question (from memory slots), so "那转化率呢" inherits the metric and scope. A plain "还有呢" with no SQL of its own is completed from history rather than sent empty to the tool. Comparison questions (`is_compare()` in router) pick the metric first (`design-notes §8`) and produce a conclusion plus attribution (`_compare_conclusion`, analysis.py:99-141).

## Dataset and fixed seed (`data/generate_data.py`)

Data is generated locally with a fixed seed (`:24-27`) so results are reproducible and no real customer data is used. Four tables: `advertisers`, `campaigns`, `metrics`, `interactions`. 42 advertisers, 41 knowledge chunks (4 SOP + 4 cases split). Regenerating with `python data/generate_data.py` must yield identical data. The DB and `data/knowledge/` are local truth sources and are gitignored — never commit them.

Business thresholds live in `config/rules.json` (14 thresholds + `_说明`), loaded by `scoring.py:load_rules()` with a built-in `DEFAULTS` fallback. `CRM_RULES_PATH` can override the path. Out-of-range values fall back per item; the service must still start.

## Verification

- A new guard rule needs a test in `tests/test_daily_optimizations.py` (e.g. multi-table rejection, legal-column non-regression).
- A new window/scope rule needs a boundary case + prefix case and the full [eval-regression](../eval-regression/SKILL.md) gate.
- Never return a number that did not come from `run_readonly` on the current question's SQL. If scope is unsupported or data is unavailable, refuse with a safe message (`DATA_UNAVAILABLE` / `UNSUPPORTED_SCOPE`) — do not synthesize.

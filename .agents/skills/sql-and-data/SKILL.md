---
name: sql-and-data
description: Use when touching Text-to-SQL, the read-only guard, data generation, metric definitions, or custom time-window handling in crm-sales-agent. Covers the single-SELECT whitelist, table-name parsing, ro connection, scope-boundary refusal, and the fixed-seed local dataset. | 在 crm-sales-agent 中改动 Text-to-SQL、只读护栏、造数、指标口径或自定义时间窗口时使用，覆盖单条 SELECT 白名单、表名解析、只读连接、口径边界拒绝与固定 seed 本地数据集。
---

# SQL & data / SQL 与数据

<!-- EN -->
The database is the truth source for advertising operations. Every query path is read-only and whitelisted; every business number must be reproducible offline. See design-notes §4, §7, §27 for rationale.

<!-- 中文 -->
数据库是投放业务的真相源。所有查询路径均为只读且经过白名单；每个业务数字都必须可离线复现。理由见 design-notes §4、§7、§27。

> Both languages are authoritative; English comes first (`<!-- EN -->`), Chinese follows (`<!-- 中文 -->`). / 两种语言同等有效；英文在前，中文在后。

## Read-only guard — the only entry / 只读护栏——唯一入口（`sql_agent.py:61-75`）

<!-- EN -->
`guard(sql)` returns `(allowed, reason)`. A SQL is accepted only when:

1. Non-empty, single statement after stripping the trailing `;`.
2. No forbidden keywords (`insert/update/delete/drop/alter/create/attach/detach/pragma/vacuum/replace into/trigger`), checked case-insensitively.
3. Starts with `select`.
4. Every table extracted by `_table_names()` is in `ALLOWED_TABLES = {advertisers, campaigns, metrics, interactions}`.

`_table_names()` (`sql_agent.py:39-58`) must parse the **complete** table list after `FROM`/`JOIN`, including comma-separated multi-table forms (`FROM advertisers, sqlite_master` must be rejected), stopping at `WHERE/ON/JOIN/GROUP/ORDER/LIMIT`, and stripping comments and string literals first so `WHERE name='from sqlite_master'` is not mistaken for a table. Never narrow this parser to the first table. `replace` is matched only as `replace into` so legal columns like `replaced_at` are not flagged.

Execution goes through `run_readonly()` (`sql_agent.py:78-86`) using `file:<path>?mode=ro` — a second safety net independent of the keyword guard. Row caps: 50 detail, 200 aggregate. Do not raise these without a reason.

<!-- 中文 -->
`guard(sql)` 返回 `(allowed, reason)`。SQL 仅在同时满足以下条件时被放行：

1. 去掉末尾 `;` 后非空且为单条语句。
2. 不含禁用关键字（`insert/update/delete/drop/alter/create/attach/detach/pragma/vacuum/replace into/trigger`），大小写不敏感。
3. 以 `select` 开头。
4. `_table_names()` 提取出的**每个**表都在 `ALLOWED_TABLES = {advertisers, campaigns, metrics, interactions}` 中。

`_table_names()`（`sql_agent.py:39-58`）必须解析 `FROM`/`JOIN` 之后的**完整**表列表，包括逗号分隔的多表形式（`FROM advertisers, sqlite_master` 必须被拒绝），在 `WHERE/ON/JOIN/GROUP/ORDER/LIMIT` 处停止，并先剥离注释与字符串字面量，避免把 `WHERE name='from sqlite_master'` 误当成表名。绝不可把解析器收窄为只读第一个表。`replace` 仅按 `replace into` 匹配，以免误伤 `replaced_at` 这类合法列名。

执行经 `run_readonly()`（`sql_agent.py:78-86`）走 `file:<path>?mode=ro`——这是独立于关键字护栏的第二道安全网。行数上限：明细 50、聚合 200。无正当理由不得提高。

## Three-layer Text-to-SQL / 三层 Text-to-SQL（`sql_agent.py`）

<!-- EN -->
`nl_to_sql(question, llm, advertiser_id)` tries, in order: model generation (when available) → fixed templates (`template_sql`, :122-212) → scope refusal. The SQL is always passed through `guard()` before execution. The source (`llm`/`template`/`none`) is surfaced in evidence for traceability.

**Scope boundary is checked before model and template matching** (`unsupported_window`, :225-241): a custom window like "近 45 天" with N ≠ 30 must return `source="unsupported_window"` and a refusal answer, not a plausible-looking wrong number. The 30-day default and monthly trend wording must NOT be misclassified — `evals/prefix_cases.jsonl` PFX-02/03 pin this. H07 in `cases.jsonl` pins the refusal (forbid_substrings `1,464,090` / `1464090` / `户均消耗`). When adding a new window rule, add both a boundary case and a prefix case.

<!-- 中文 -->
`nl_to_sql(question, llm, advertiser_id)` 依次尝试：模型生成（可用时）→ 固定模板（`template_sql`，:122-212）→ 口径拒绝。SQL 在执行前一律经过 `guard()`。来源（`llm`/`template`/`none`）会写入 evidence 以便追溯。

**口径边界在模型与模板匹配之前检查**（`unsupported_window`，:225-241）：像"近 45 天"这种 N ≠ 30 的自定义窗口必须返回 `source="unsupported_window"` 和拒绝答案，而不是一个看起来合理的错误数字。30 天默认口径与月度趋势措辞**不得**被误判——`evals/prefix_cases.jsonl` 的 PFX-02/03 固定了这一点。`cases.jsonl` 的 H07 固定了拒绝行为（forbid_substrings `1,464,090` / `1464090` / `户均消耗`）。新增窗口规则时，边界用例与前缀用例都要加。

## Near-window and comparison wording / 追问窗口与对比措辞

<!-- EN -->
`_sql_question()` (`nodes/analysis.py:19-36`) rewrites a follow-up question using the previous turn's analysis question (from memory slots), so "那转化率呢" inherits the metric and scope. A plain "还有呢" with no SQL of its own is completed from history rather than sent empty to the tool. Comparison questions (`is_compare()` in router) pick the metric first (`design-notes §8`) and produce a conclusion plus attribution (`_compare_conclusion`, analysis.py:99-141).

<!-- 中文 -->
`_sql_question()`（`nodes/analysis.py:19-36`）用上一轮的分析问题（来自记忆槽位）补全追问，使"那转化率呢"继承指标与口径。没有自身 SQL 的纯追问"还有呢"会从历史补全，而不是把空问题发给工具。对比类问题（router 中的 `is_compare()`）先选指标（`design-notes §8`），再产出结论与归因（`_compare_conclusion`，analysis.py:99-141）。

## Dataset and fixed seed / 数据集与固定 seed（`data/generate_data.py`）

<!-- EN -->
Data is generated locally with a fixed seed (`:24-27`) so results are reproducible and no real customer data is used. Four tables: `advertisers`, `campaigns`, `metrics`, `interactions`. 42 advertisers, 41 knowledge chunks (4 SOP + 4 cases split). Regenerating with `python data/generate_data.py` must yield identical data. The DB and `data/knowledge/` are local truth sources and are gitignored — never commit them.

Business thresholds live in `config/rules.json` (14 thresholds + `_说明`), loaded by `scoring.py:load_rules()` with a built-in `DEFAULTS` fallback. `CRM_RULES_PATH` can override the path. Out-of-range values fall back per item; the service must still start.

<!-- 中文 -->
数据在本地用固定 seed 生成（`:24-27`），确保结果可复现且不使用真实客户数据。四张表：`advertisers`、`campaigns`、`metrics`、`interactions`。42 个广告客户、41 个知识切片（4 篇 SOP + 4 篇案例切分）。用 `python data/generate_data.py` 重新生成必须得到完全相同的数据。数据库与 `data/knowledge/` 是本地真相源且已 gitignore——绝不提交。

业务阈值在 `config/rules.json`（14 个阈值 + `_说明`），由 `scoring.py:load_rules()` 加载，内置 `DEFAULTS` 兜底。`CRM_RULES_PATH` 可覆盖路径。越界值逐项回落；服务必须仍能启动。

## Verification / 验证

<!-- EN -->
- A new guard rule needs a test in `tests/test_daily_optimizations.py` (e.g. multi-table rejection, legal-column non-regression).
- A new window/scope rule needs a boundary case + prefix case and the full [eval-regression](../eval-regression/SKILL.md) gate.
- Never return a number that did not come from `run_readonly` on the current question's SQL. If scope is unsupported or data is unavailable, refuse with a safe message (`DATA_UNAVAILABLE` / `UNSUPPORTED_SCOPE`) — do not synthesize.

<!-- 中文 -->
- 新增护栏规则需要在 `tests/test_daily_optimizations.py` 加测试（如多表拒绝、合法列不回归）。
- 新增窗口/口径规则需要边界用例 + 前缀用例，并跑完整的 [eval-regression](../eval-regression/SKILL.md) 门禁。
- 绝不返回并非来自当前问题 SQL 的 `run_readonly` 结果数字。口径不支持或数据不可用时，用安全文案拒绝（`DATA_UNAVAILABLE` / `UNSUPPORTED_SCOPE`）——不要编造。

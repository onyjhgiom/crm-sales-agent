# AGENTS.md

CRM 销售提效 Agent：Python + LangGraph + SQLite + FastAPI 的离线优先问答系统。本文件是所有协作者（人 / agent）的**硬约束与导航**；设计取舍的"为什么"集中在 [docs/design-notes.md](docs/design-notes.md)，此处只给结论和链接，不复制理由。改代码前先读对应 skill 与 design-notes 的相关节。

<!-- EN -->
CRM Sales Efficiency Agent: an offline-first Q&A system built with Python, LangGraph, SQLite and FastAPI. This file holds the **hard constraints and navigation** for every collaborator (human or agent). Design rationale lives in [docs/design-notes.md](docs/design-notes.md); only conclusions and links are kept here, never duplicated reasoning. Before editing code, read the relevant skill and design-notes section.

> 语言 / Language: 中文 / English — 下文每节先中文后英文 / Every section below gives Chinese first, then English. 两种语言同等有效，若出现分歧须在同一改动中同时修正 / Both are authoritative; if they ever diverge, fix both in the same change.

## 首要原则（违反即视为回归） / Core Principles (violations count as regressions)

<!-- 中文 -->
1. **离线优先**：无模型 Key 时全功能可跑。规则负责算分，模型只负责表达；每个节点都必须有模板兜底。`LLM_MODE=offline` 时零外部调用。任何改动不得让离线路径依赖网络。
2. **评估零回归门禁**：任何代码改动后，`boundary` 2/2、`retention` 35/35 必须逐字节一致，`prefix` 4/4 通过，整体通过率 100%。`python evals/run_eval.py --repeats 3` 返回码必须为 0。详见 [eval-regression skill](.agents/skills/eval-regression/SKILL.md)。
3. **数字事实不被模型改写**：回答中的数字只能来自本轮工具返回。数字核对只产出 `warnings`，绝不改写正文。见 design-notes §1、§22。
4. **SQL 只读白名单**：只允许单条 `SELECT`，表白名单 `advertisers/campaigns/metrics/interactions`，必须走 `file:...?mode=ro`。`guard()` 是唯一入口，禁止绕过。见 [sql-and-data skill](.agents/skills/sql-and-data/SKILL.md)。
5. **错误脱敏**：用户只看到 `AgentError.message`（固定安全文案），异常原文/路径/堆栈/SQL 只进 `detail` 与日志。禁止回显内部信息。
6. **单意图走原路径**：`graph._dispatch()` 仅在 `plan.mode=="composite"` 且子任务 ≥2 时切编排节点。规划器不得改变任何单意图答案。

<!-- EN -->
1. **Offline-first**: every feature works with no model API key. Rules compute the facts; the model only phrases them; every node has a template fallback. `LLM_MODE=offline` makes zero external calls. No change may make the offline path depend on the network.
2. **Zero-regression evaluation gate**: after any code change, `boundary` 2/2 and `retention` 35/35 must stay byte-for-byte identical, `prefix` 4/4 must pass, and overall pass rate must be 100%. `python evals/run_eval.py --repeats 3` must exit 0. See the [eval-regression skill](.agents/skills/eval-regression/SKILL.md).
3. **Model never rewrites numeric facts**: numbers in an answer may only come from this turn's tool results. Number checks emit `warnings` only — never rewrite the body text. See design-notes §1 and §22.
4. **Read-only SQL whitelist**: a single `SELECT` only, tables limited to `advertisers/campaigns/metrics/interactions`, always via `file:...?mode=ro`. `guard()` is the sole entry point and must not be bypassed. See the [sql-and-data skill](.agents/skills/sql-and-data/SKILL.md).
5. **Error redaction**: users see only `AgentError.message` (fixed safe text); raw exceptions, paths, stack traces and SQL go to `detail` and logs only. Never echo internal information.
6. **Single intent keeps the original path**: `graph._dispatch()` switches to the orchestrate node only when `plan.mode=="composite"` with at least two sub-tasks. The planner must not change any single-intent answer.

## 仓库布局 / Repository Layout

<!-- 中文 -->
```
src/crm_agent/
  agent.py        SalesAgent.ask() 统一入口（CLI/API/评估共用），记忆写入与 FIFO 淘汰
  config.py       Settings(29 项) + load_settings，环境变量 > .env > 默认值，逐项范围校验
  context.py      Context 依赖容器，装配 store/kb/llm/memory/registry/planner
  state.py        AgentState TypedDict —— 只放结构化事实，不放对话原文
  graph.py        LangGraph 图：router → 原子节点 → review 条件边；会话滞回淘汰
  errors.py       12 个错误码 + AgentError（message/detail 两层）
  memory.py       ConversationMemory：SQLite 持久化、整会话重写、build_history_prompt
  planning.py     Planner：复合任务拆分、槽位抽取、置信度
  integration.py  复合结果整合 IntegratedAnswer；number_mismatch 只警告
  llm.py          OpenAI 兼容封装：超时/重试/熔断/降级，chat() 失败返回 None
  store.py        CRMStore：每线程独立只读连接（mode=ro）
  nodes/          router/profile/opportunity/analysis/knowledge/smalltalk/review/orchestrate
  tools/          registry(工具层) / bootstrap(13 内置工具) / rag(BM25) / scoring(规则) / sql_agent
api/server.py     FastAPI 7 端点 + 单 IP 限流 + 请求超时 + request_id
evals/            cases.jsonl(37) / prefix_cases.jsonl(4) / verifiers / failure_attribution / run_eval
tests/            5 个测试文件，共 55 项
scripts/          daily_update.py（每日任务）/ daily_update.cmd（Windows 触发）
config/           daily_update.json / rules.json
data/             generate_data.py（固定 seed 造数）/ crm.db / knowledge/（本地真相源，不进仓库）
docs/             design-notes.md（31 节取舍）/ demo-script.md / portfolio-one-pager.md
web/index.html    单文件前端
run.py            统一启动入口
```

<!-- EN -->
```
src/crm_agent/
  agent.py        SalesAgent.ask() — single entry point shared by CLI/API/evals; memory writes + FIFO eviction
  config.py       Settings (29 fields) + load_settings; env vars > .env > defaults, per-field range checks
  context.py      Context dependency container: store/kb/llm/memory/registry/planner
  state.py        AgentState TypedDict — structured facts only, never raw dialogue
  graph.py        LangGraph: router → atomic nodes → conditional review edge; hysteresis session eviction
  errors.py       12 error codes + AgentError (message/detail two layers)
  memory.py       ConversationMemory: SQLite persistence, whole-session rewrite, build_history_prompt
  planning.py     Planner: composite splitting, slot extraction, confidence
  integration.py  IntegratedAnswer for composite results; number_mismatch only warns
  llm.py          OpenAI-compatible wrapper: timeout/retry/circuit breaker/degradation; chat() returns None on failure
  store.py        CRMStore: thread-local read-only connection (mode=ro)
  nodes/          router/profile/opportunity/analysis/knowledge/smalltalk/review/orchestrate
  tools/          registry / bootstrap (13 built-ins) / rag (BM25) / scoring (rules) / sql_agent
api/server.py     FastAPI 7 endpoints + per-IP rate limit + request timeout + request_id
evals/            cases.jsonl (37) / prefix_cases.jsonl (4) / verifiers / failure_attribution / run_eval
tests/            5 test files, 55 tests total
scripts/          daily_update.py (daily task) / daily_update.cmd (Windows trigger)
config/           daily_update.json / rules.json
data/             generate_data.py (fixed-seed fixtures) / crm.db / knowledge/ (local truth, gitignored)
docs/             design-notes.md (31 decisions) / demo-script.md / portfolio-one-pager.md
web/index.html    single-file frontend
run.py            unified launch entry point
```

## 命令 / Commands

<!-- 中文 -->
```sh
# 环境与依赖（用系统 Python；仓库无虚拟环境运行前提）
python -m pip install -r requirements.txt

# 造数与本地运行
python data/generate_data.py
python run.py                       # 交互 REPL
python run.py --demo
python run.py --ask "A003 现在有什么商机" --adv A003

# Web 服务（离线演示，零外部调用）
LLM_MODE=offline USE_CHECKPOINTER=false python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
# Windows 等价：先 set OPENAI_API_KEY= 清空残留 Key

# 验证（提交/推送前必跑）
python -m compileall -q src api tests scripts
python -m unittest discover -s tests -v          # 55 项全过
python evals/run_eval.py --repeats 3             # 四指标 + Pass³ + 边界/保留/前缀，退出码 0

# 每日更新任务（幂等）
python scripts/daily_update.py                    # 调度入口
python scripts/daily_update.py --force           # 手动，忽略当日去重
python scripts/daily_update.py --dry-run         # 只验证评估，不提交不推送
scripts\daily_update.cmd --force                 # Windows 一键
```

Unix 下清空残留 Key 的等价写法为 `env -u OPENAI_API_KEY ...`；Windows 由 `.cmd` 用 `set "OPENAI_API_KEY="` 完成。评估与每日任务内部已自动强制离线与清 Key（见 `scripts/daily_update.py:base_env()`），无需手动。

<!-- EN -->
```sh
# Environment and dependencies (system Python; no virtualenv required)
python -m pip install -r requirements.txt

# Generate data and run locally
python data/generate_data.py
python run.py                       # interactive REPL
python run.py --demo
python run.py --ask "A003 现在有什么商机" --adv A003

# Web service (offline demo, zero external calls)
LLM_MODE=offline USE_CHECKPOINTER=false python -m uvicorn api.server:app --host 127.0.0.1 --port 8000
# Windows equivalent: clear any leftover key first with set OPENAI_API_KEY=

# Verification (mandatory before commit/push)
python -m compileall -q src api tests scripts
python -m unittest discover -s tests -v          # 55 tests pass
python evals/run_eval.py --repeats 3             # four metrics + Pass^k + boundary/retention/prefix, exit 0

# Daily update task (idempotent)
python scripts/daily_update.py                    # scheduled entry point
python scripts/daily_update.py --force           # manual; bypass same-day dedup
python scripts/daily_update.py --dry-run         # verify/evaluate only, no commit or push
scripts\daily_update.cmd --force                 # one-click on Windows
```

On Unix, clear a leftover key with `env -u OPENAI_API_KEY ...`; on Windows the `.cmd` does `set "OPENAI_API_KEY="`. The evaluator and daily task already force offline mode and clear the key internally (see `scripts/daily_update.py:base_env()`), so no manual step is needed.

## 约定 / Conventions

<!-- 中文 -->
- **语言**：模块 docstring、行内注释、日志理由全部中文；`from __future__ import annotations` 打头；`snake_case` / `PascalCase` / `UPPER_SNAKE`，内部前缀 `_`。
- **类型**：显式注解，用 `str | None` / `list[dict[str, Any]]` / `TypedDict` / `@dataclass`；公开函数写返回类型。
- **日志**：`logging.getLogger("crm_agent.<module>")`；只记 `type(exc).__name__`，不打印 Key/堆栈/完整响应；宽泛 `except Exception` 带 `# noqa: BLE001` 并写中文理由。
- **错误处理**：优先返回结构化结果（`ToolResult` / `AgentError.to_dict()` / 节点的 `answer` 字段），而非向上抛异常；只对 `retryable=True` 的错误重试；配置越界逐项回落默认值，不让服务起不来。
- **配置**：新增环境变量必须在 `config.py` 的 `Settings` 加字段 + 范围校验 + `.env.example` 说明；禁止在插件里放硬编码可调参数。
- **参数校验**：外部输入（尤其 `advertiser_id`，`^A\d{3}$`）先过 `validate_params()`；未知工具返回 `TOOL_UNAVAILABLE` 而非抛异常。
- **ctx 注入**：工具 handler 只认显式 `ctx` 参数（`inspect.signature` 判定），不认 `**kwargs`，不靠捕获异常文案。见 `tools/registry.py:56-66`。

<!-- EN -->
- **Language**: module docstrings, inline comments and log rationale are written in Chinese; start files with `from __future__ import annotations`; naming is `snake_case` / `PascalCase` / `UPPER_SNAKE`, internal names prefixed with `_`.
- **Types**: explicit annotations using `str | None`, `list[dict[str, Any]]`, `TypedDict`, `@dataclass`; public functions declare return types.
- **Logging**: `logging.getLogger("crm_agent.<module>")`; log only `type(exc).__name__`, never keys, stack traces or full responses; broad `except Exception` carries `# noqa: BLE001` plus a Chinese rationale.
- **Error handling**: prefer structured results (`ToolResult`, `AgentError.to_dict()`, a node's `answer` field) over raising; retry only when `retryable=True`; out-of-range config falls back per field so the service always starts.
- **Configuration**: every new environment variable needs a `Settings` field in `config.py`, range validation, and an `.env.example` entry; no hard-coded tunable parameters in plugins.
- **Parameter validation**: external inputs (especially `advertiser_id`, `^A\d{3}$`) go through `validate_params()` first; unknown tools return `TOOL_UNAVAILABLE` instead of raising.
- **ctx injection**: tool handlers accept an explicit `ctx` parameter only (detected via `inspect.signature`); `**kwargs` does not count, and exception-message sniffing is forbidden. See `tools/registry.py:56-66`.

## 专项工作流（按需读 skill） / Specialized Workflows (read skills on demand)

<!-- 中文 -->
| 场景 | Skill |
|---|---|
| 改代码后验证与零回归快照比对 | [eval-regression](.agents/skills/eval-regression/SKILL.md) |
| 新增/修改工具、参数校验、重试回退、编排 | [tool-layer](.agents/skills/tool-layer/SKILL.md) |
| SQL / 数据 / 口径边界 / 造数 | [sql-and-data](.agents/skills/sql-and-data/SKILL.md) |
| 离线降级 / 熔断 / 超时 / 生产稳定性 | [offline-resilience](.agents/skills/offline-resilience/SKILL.md) |
| 多轮上下文 / 记忆 / 指代消解 / 规划 | [context-and-memory](.agents/skills/context-and-memory/SKILL.md) |
| 提交前检查与两端同步 | [pre-push-checks](.agents/skills/pre-push-checks/SKILL.md) |
| 写决策记录（Agent Note） | [.agents/notes/README.md](.agents/notes/README.md) |

<!-- EN -->
| Scenario | Skill |
|---|---|
| Verify and diff snapshots after a code change | [eval-regression](.agents/skills/eval-regression/SKILL.md) |
| Add/modify tools, validation, retry/fallback, orchestration | [tool-layer](.agents/skills/tool-layer/SKILL.md) |
| SQL / data / scope boundaries / fixtures | [sql-and-data](.agents/skills/sql-and-data/SKILL.md) |
| Offline degradation / circuit breaking / timeouts / production stability | [offline-resilience](.agents/skills/offline-resilience/SKILL.md) |
| Multi-turn context / memory / reference resolution / planning | [context-and-memory](.agents/skills/context-and-memory/SKILL.md) |
| Pre-commit checks and two-remote sync | [pre-push-checks](.agents/skills/pre-push-checks/SKILL.md) |
| Write a decision record (Agent Note) | [.agents/notes/README.md](.agents/notes/README.md) |

## 文档所有权（一事实一处） / Documentation Ownership (one fact, one home)

<!-- 中文 -->
- `README.md`：项目全貌、快速开始、按日期的增量变更日志。
- `docs/design-notes.md`：31 节"为什么 + 备选方案 + 放弃理由"，面试追问专用。改动触及既有决策时**更新对应节**，不要新开重复论述。
- `.agents/notes/`：跨改动仍成立、代码和上面两份文档都装不下的**决策记录**（见 README 的分类与格式）。
- `evals/report.md`：评估生成物，每次评估覆盖，**列入每日任务的保护路径**，手改无效。

<!-- EN -->
- `README.md`: project overview, quick start, dated incremental changelog.
- `docs/design-notes.md`: 31 sections of "why + alternatives + what we gave up", for interview follow-ups. When a change touches an existing decision, **update that section** — do not open a duplicate discussion.
- `.agents/notes/`: decision records that survive across changes and do not fit in code, README or design-notes (see their README for taxonomy and format).
- `evals/report.md`: generated evaluation report, overwritten on every run, and on the daily task's **protected-path list** — manual edits do not stick.

## 审查清单（完成前自检） / Review Checklist (self-check before finishing)

<!-- 中文 -->
- [ ] 离线路径（无 Key / 熔断中）仍可跑通，未引入网络依赖。
- [ ] `unittest` 55 项 + 评估门禁全过；必要时用干净 worktree 导出基线做答案逐条快照比对，差异为 0。
- [ ] 单意图答案未变；新增能力只在明确边界（如 `plan.mode=="composite"`）才改变行为。
- [ ] 用户可见文案无内部路径、异常原文、SQL 或 Key。
- [ ] 新增配置项有范围校验和 `.env.example` 说明；README/design-notes 同步更新。
- [ ] 若改变了既有决策的实现事实（路径/默认值/机制），design-notes 或 Agent Note 同步更新，无陈旧陈述。

<!-- EN -->
- [ ] The offline path (no key / circuit open) still works; no network dependency introduced.
- [ ] 55 unit tests + evaluation gate pass; when needed, diff answers against a clean worktree baseline — zero differences.
- [ ] Single-intent answers unchanged; new behavior only activates behind explicit boundaries such as `plan.mode=="composite"`.
- [ ] User-visible text contains no internal paths, raw exceptions, SQL, or keys.
- [ ] New config fields have range validation and `.env.example` entries; README/design-notes updated accordingly.
- [ ] If an existing decision's implementation facts changed (path/default/mechanism), design-notes or the Agent Note was updated — no stale statements.

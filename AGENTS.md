# AGENTS.md

CRM 销售提效 Agent：Python + LangGraph + SQLite + FastAPI 的离线优先问答系统。本文件是所有协作者（人 / agent）的**硬约束与导航**；设计取舍的"为什么"集中在 [docs/design-notes.md](docs/design-notes.md)，此处只给结论和链接，不复制理由。改代码前先读对应 skill 与 design-notes 的相关节。

## 首要原则（违反即视为回归）

1. **离线优先**：无模型 Key 时全功能可跑。规则负责算分，模型只负责表达；每个节点都必须有模板兜底。`LLM_MODE=offline` 时零外部调用。任何改动不得让离线路径依赖网络。
2. **评估零回归门禁**：任何代码改动后，`boundary` 2/2、`retention` 35/35 必须逐字节一致，`prefix` 4/4 通过，整体通过率 100%。`python evals/run_eval.py --repeats 3` 返回码必须为 0。详见 [eval-regression skill](.agents/skills/eval-regression/SKILL.md)。
3. **数字事实不被模型改写**：回答中的数字只能来自本轮工具返回。数字核对只产出 `warnings`，绝不改写正文。见 design-notes §1、§22。
4. **SQL 只读白名单**：只允许单条 `SELECT`，表白名单 `advertisers/campaigns/metrics/interactions`，必须走 `file:...?mode=ro`。`guard()` 是唯一入口，禁止绕过。见 [sql-and-data skill](.agents/skills/sql-and-data/SKILL.md)。
5. **错误脱敏**：用户只看到 `AgentError.message`（固定安全文案），异常原文/路径/堆栈/SQL 只进 `detail` 与日志。禁止回显内部信息。
6. **单意图走原路径**：`graph._dispatch()` 仅在 `plan.mode=="composite"` 且子任务 ≥2 时切编排节点。规划器不得改变任何单意图答案。

## 仓库布局

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

## 命令

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

## 约定

- **语言**：模块 docstring、行内注释、日志理由全部中文；`from __future__ import annotations` 打头；`snake_case` / `PascalCase` / `UPPER_SNAKE`，内部前缀 `_`。
- **类型**：显式注解，用 `str | None` / `list[dict[str, Any]]` / `TypedDict` / `@dataclass`；公开函数写返回类型。
- **日志**：`logging.getLogger("crm_agent.<module>")`；只记 `type(exc).__name__`，不打印 Key/堆栈/完整响应；宽泛 `except Exception` 带 `# noqa: BLE001` 并写中文理由。
- **错误处理**：优先返回结构化结果（`ToolResult` / `AgentError.to_dict()` / 节点的 `answer` 字段），而非向上抛异常；只对 `retryable=True` 的错误重试；配置越界逐项回落默认值，不让服务起不来。
- **配置**：新增环境变量必须在 `config.py` 的 `Settings` 加字段 + 范围校验 + `.env.example` 说明；禁止在插件里放硬编码可调参数。
- **参数校验**：外部输入（尤其 `advertiser_id`，`^A\d{3}$`）先过 `validate_params()`；未知工具返回 `TOOL_UNAVAILABLE` 而非抛异常。
- **ctx 注入**：工具 handler 只认显式 `ctx` 参数（`inspect.signature` 判定），不认 `**kwargs`，不靠捕获异常文案。见 `tools/registry.py:56-66`。

## 专项工作流（按需读 skill）

| 场景 | Skill |
|---|---|
| 改代码后验证与零回归快照比对 | [eval-regression](.agents/skills/eval-regression/SKILL.md) |
| 新增/修改工具、参数校验、重试回退、编排 | [tool-layer](.agents/skills/tool-layer/SKILL.md) |
| SQL / 数据 / 口径边界 / 造数 | [sql-and-data](.agents/skills/sql-and-data/SKILL.md) |
| 离线降级 / 熔断 / 超时 / 生产稳定性 | [offline-resilience](.agents/skills/offline-resilience/SKILL.md) |
| 多轮上下文 / 记忆 / 指代消解 / 规划 | [context-and-memory](.agents/skills/context-and-memory/SKILL.md) |
| 提交前检查与两端同步 | [pre-push-checks](.agents/skills/pre-push-checks/SKILL.md) |
| 写决策记录（Agent Note） | [.agents/notes/README.md](.agents/notes/README.md) |

## 文档所有权（一事实一处）

- `README.md`：项目全貌、快速开始、按日期的增量变更日志。
- `docs/design-notes.md`：31 节"为什么 + 备选方案 + 放弃理由"，面试追问专用。改动触及既有决策时**更新对应节**，不要新开重复论述。
- `.agents/notes/`：跨改动仍成立、代码和上面两份文档都装不下的**决策记录**（见 README 的分类与格式）。
- `evals/report.md`：评估生成物，每次评估覆盖，**列入每日任务的保护路径**，手改无效。

## 审查清单（完成前自检）

- [ ] 离线路径（无 Key / 熔断中）仍可跑通，未引入网络依赖。
- [ ] `unittest` 55 项 + 评估门禁全过；必要时用干净 worktree 导出基线做答案逐条快照比对，差异为 0。
- [ ] 单意图答案未变；新增能力只在明确边界（如 `plan.mode=="composite"`）才改变行为。
- [ ] 用户可见文案无内部路径、异常原文、SQL 或 Key。
- [ ] 新增配置项有范围校验和 `.env.example` 说明；README/design-notes 同步更新。
- [ ] 若改变了既有决策的实现事实（路径/默认值/机制），design-notes 或 Agent Note 同步更新，无陈旧陈述。

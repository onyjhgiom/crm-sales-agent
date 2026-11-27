# CRM 销售提效 Agent

> 面向**批量中小广告主**的销售提效智能体：把「客户画像、商机推荐、经营分析」三件事从"翻后台 + 拍脑袋"变成"一句话问出来，并给出可执行的下一步"。

本项目是 [datawhalechina/hello-agents](https://github.com/datawhalechina/hello-agents) 仓库内的**二次开发实践**：方法论参考了第 4 章（ReAct / Reflection）、第 7 章（自研框架分层）、第 8 章（记忆与检索）、第 9 章（上下文工程）、第 13 章（综合应用），**业务侧代码为独立实现**，具体差异见 [与 hello-agents 的关系](#与-hello-agents-的关系)。

---

## 1. 它解决什么问题

代理商/平台侧的中小客户运营，真实的痛点是"客户太多、信号太散"：

- 一个运营要维护 60~100 个中小广告主，只能靠 Excel + 后台反复切；
- 判断"这个客户该不该增投、要不要挽留"，全靠个人经验，新人上手要两三个月；
- 几百个客户里，今天该先打给谁，没人能一眼看出来。

所以这个 Agent 只做三件销售每天都要做的事：

| 能力 | 输入 | 输出 |
|---|---|---|
| **客户画像** | 客户 ID / 客户名 | 一句话判断 + 经营概况 + 趋势 + 风险信号 + 建议动作 |
| **商机辅助** | 客户 ID，或"今天该跟进谁" | 加量/续费/升级/挽留哪一类商机、几分、为什么、怎么做、话术、预估增量 |
| **经营分析** | 自然语言问题 | 生成的 SQL、查询结果、结论与归因方向 |

对应岗位职责的落点（投递作品集时用的对照）：

| 岗位要求 | 本项目对应实现 |
|---|---|
| 基于数据与批量中小客户场景搭建流程提效模型 | `tools/scoring.py` 规则引擎：流失风险 5 条信号 + 6 类商机打分，可逐条追溯到指标 |
| 整合关键过程指标、通过技术内化 AI 提效 | `nodes/` 三个子 Agent + LangGraph 编排，指标口径集中在 `scoring.py` 与知识库 |
| 与运营团队协作，方案从设计到落地 | 知识库沉淀 SOP/话术（`data/knowledge/`），输出自带"怎么做"和"开口话术"，能直接执行 |
| Python / LangGraph / RAG | LangGraph StateGraph + 自实现中文 BM25 检索 + Text-to-SQL |

---

## 2. 架构

```
                     ┌──────────────────────────── LangGraph ────────────────────────────┐
  用户问题 ──▶ router ─┬─▶ profile      （画像：档案/计划/指标/跟进 → 结构化画像卡）      │
  (含客户ID)           ├─▶ opportunity  （商机：规则打分 + 案例检索 → 动作/话术/增量）    │
                       ├─▶ analysis     （分析：NL→SQL → 只读执行 → 结论 + 归因）          │
                       ├─▶ knowledge    （知识：BM25 检索 SOP/案例 → 带引用回答）          │
                       └─▶ smalltalk    （能力介绍 + 示例问题）                           │
                                     │                                                  │
                                     ▼                                                  │
                                  review  （反思自检：有没有数字、有没有引用、够不够用）  │
                                     │                                                  │
                            未通过 ──┴─▶ 回退原子 Agent 重跑一次（最多一次）             │
                     └──────────────────────────────────────────────────────────────────┘

数据层：SQLite（advertisers / campaigns / metrics / interactions）   +   data/knowledge（策略与案例 Markdown）
```

几个刻意的设计选择，面试时被追问最多的地方：

1. **规则与模型分工**。风险信号、商机分数由纯规则算，因为"哪条规则命中"必须可审计，销售才敢照名单打电话；模型只负责把事实说成人话。分工后，模型幻觉不会污染分数。
2. **路由"规则优先、模型兜底"**。真实场景 80% 的问法固定，关键词规则又快又稳；只有完全没命中时才花一次模型调用。离线模式下整条链路 10ms 级。
3. **降级不是兜底，是默认路径**。没有 API Key 时走规则模板，功能全通、结果可复现；有 Key 时才叠加模型表达。这让评估可以脱离模型波动进行。
4. **反思节点可度量**。review 检查"数字、引用、信息量"，不通过回退一次，仍不通过就显式追加自检提示——不确定的地方宁可在回答里标出来。
5. **比较类问题先判意图、再定指标**。"比上个月好还是差"没有"下滑"这类分析词，先被商机分支截走就会答成一堆建议；现在的做法是先判成分析，再由 `pick_metric` 决定比的是消耗、CVR 还是转化成本，结论里必须给出两边的真实数字。

---

## 3. 与 hello-agents 的关系

这是我读 hello-agents 之后落地的业务项目，边界必须说清楚：

| 维度 | hello-agents | 本项目 |
|---|---|---|
| 定位 | 教程 + 示例代码 | 可直接演示的业务 Agent |
| 编排 | 第 7 章自研 HelloAgents 框架 | **LangGraph StateGraph**（岗位要求） |
| 检索 | 第 8 章 RAGTool（向量检索） | 自实现中文 BM25（零依赖，可解释，可替换为 bge-m3 + Chroma） |
| 记忆 | 第 8 章记忆工具 | LangGraph MemorySaver + 显式状态瘦身（只放结构化事实） |
| 反思 | 第 4 章 Reflection 范式 | 落成 `review` 节点，并定义可度量指标（自检通过率） |
| 数据 | 无业务数据 | 自建 42 个广告主的模拟 CRM 数据集，表间强一致 |
| 评估 | 第 12 章讲方法 | 37 条用例 + 结构化验证器 + 失败归因 + Pass^k + 轨迹前缀回归 |

**参考的是范式与写法，没有复制业务代码。** hello-agents 采用 CC BY-NC-SA 4.0 协议（署名 · 非商业性使用 · 相同方式共享），本目录作为其仓库内的衍生内容，沿用同一协议并保留署名，不用于任何商业用途。

---

## 4. 评估结果

`python evals/run_eval.py --repeats 3`（离线规则模式，37 条用例 × 3 轮，结果可完整复现）：

| 指标 | 数值 | 说明 |
|---|---|---|
| 意图准确率 | 100.0% ± 0 | 37 条全部路由正确（含比较类问题） |
| 关键事实命中率 | 100.0% ± 0 | 每条答案都命中了数据中的客户名/指标（结构化验证器核对，非关键词巧合） |
| 引用覆盖率 | 100.0% | 知识类与商机类问题均带出处 |
| 反思自检通过率 | 100.0% | review 节点判定可用 |
| **Pass³（连续 3 次全过）** | **100.0%** | 可靠性指标：每条用例重复 3 次都通过的比例 |
| 边界集 / 保留集 | 各 100.0% | 2 条边界用例 + 35 条保留用例分别统计 |
| 轨迹前缀回归 | 4/4 | 冻结上下文，验证下一步决策落在可接受动作集合内 |
| 平均延迟 | ~10 ms 量级 | 离线模式（无模型调用），含落盘 |

评估体系按《深入理解 AI Agent》第 7 章升级过一轮，核心是**验证器核实可复核事实，而不是采信 Agent 的自我陈述**：
- **验证器分类**（`evals/verifiers.py`）：普通用例核对事实关键词；边界用例走 `refusal` 验证器，必须识别口径边界、且**不得出现被禁止的编造数字**。
- **边界集 + 保留集**：任何改动都要同时在"应当改变"的边界用例（H07/A07）和"不该影响"的 35 条保留用例上验证——只测前者会把过拟合当成进步，只测后者会把无效修改当成安全。
- **失败归因**（`evals/failure_attribution.py`）：定位首个错误步骤、错误类别、责任方、根因与后果，而不是把最后的报错当根因。
- **轨迹前缀回归**（`evals/prefix_cases.jsonl`）：冻结初始状态，只验证"下一步动作"是可接受集合里的哪一个（含"追问继承客户后遇自定义窗口仍要拒绝"这类组合场景）。
- **统计口径**：多轮运行报均值与 95% 置信区间；`Pass@k`（至少一次成功，能力上限）与 `Pass^k`（连续 k 次全过，业务可靠性）分开看，避免用单次通过率过度解读。

**已知短板（不修饰）**：

- **自定义时间窗口（如近 45 天）无法查询**：库里只有近 30 天与月度两套口径。早期版本在这里有个真 bug——"近45天消耗"被模糊命中到"消耗合计"模板，返回一个像模像样的全库数字（1,464,090），答非所问却给了个错数字，比直接说答不出更危险。现在改成识别口径边界后明确拒绝，并说明可用口径；修这个 bug 的过程本身就是失败归因 + 边界回归的一次实战。要真正支持，得先在数据层做按天明细。
- 比较类只覆盖两种问法：两个/三个客户之间的单指标对比、单客户的月度环比。更复杂的问法（"这季度比上季度"、"和行业均值比"）没有模板，接了大模型才可能答出来。
- 离线模板的表述偏机械；接上大模型后表达质量明显更好，但延迟上升到秒级、结果不再完全可复现（两种模式分开评估，不混报）。

评估集的发展是"越测越严"：初版 32 条 → 补 H01~H07 到 37 条 → H07 暴露口径 bug → 修复后没有删掉这条用例，而是把它升级成 `refusal` 验证用例 + 轨迹前缀回归用例，让"正确识别边界并拒绝"成为一条可度量的能力，而不是一句口头承诺。

完整明细见 [`evals/report.md`](evals/report.md)；本轮无失败时 [`evals/attributions.json`](evals/attributions.json) 为空数组，H07 修复前的历史归因与已固化的回归用例记录在报告的"失败归因"段与 `cases.jsonl` 的 `note` 字段。

---

## 5. 快速开始

```bash
# 1. 数据与知识库（数据已随仓库提供，需要重建时执行）
python data/generate_data.py

# 2. 命令行：离线可跑，无需 Key
python run.py                 # 交互式
python run.py --demo          # 演示脚本（录屏用）
python run.py --ask "A003 现在有什么商机" --adv A003

# 3. 接大模型（任意 OpenAI 兼容接口）
copy .env.example .env        # 填 OPENAI_API_KEY / OPENAI_BASE_URL / LLM_MODEL

# 4. 网页端
python -m uvicorn api.server:app --port 8000
# 浏览器打开 http://127.0.0.1:8000

# 5. 评估
python evals/run_eval.py          # 离线
python evals/run_eval.py --llm    # 在线

# 6. 调业务阈值（不用改代码，重启即生效）
#    文件：config/rules.json；填错类型或比率写成 20 会被自动忽略并回落默认值
```

依赖：Python 3.10+（实测 3.13），`pip install -r requirements.txt`。不依赖 pandas / 向量库，clone 下来即可运行。

---

## 6. 目录结构

```
crm-sales-agent/
├── run.py                  # 统一入口
├── api/server.py           # FastAPI（/api/chat、/api/advertisers、/api/health）
├── web/index.html          # 单文件前端（含执行轨迹展示）
├── data/
│   ├── generate_data.py    # 模拟 CRM 数据生成（确定性 seed）
│   ├── crm.db              # 42 个广告主 / 82 条计划 / 126 条月度指标 / 133 条跟进
│   └── knowledge/          # 4 篇 SOP + 4 个案例（RAG 语料）
├── src/crm_agent/
│   ├── graph.py            # LangGraph 图装配（router → 子 Agent → review）
│   ├── agent.py            # 对外统一入口（CLI/API/评估共用）
│   ├── state.py            # 状态定义（只装结构化事实）
│   ├── llm.py              # OpenAI 兼容封装，失败降级
│   ├── nodes/              # router / profile / opportunity / analysis / knowledge / smalltalk / review
│   └── tools/
│       ├── scoring.py      # 风险信号 + 商机打分（读 config/rules.json）
│       ├── rag.py          # 中文 BM25 检索
│       └── sql_agent.py    # Text-to-SQL + 白名单校验 + 只读执行 + 对比/环比模板
├── config/rules.json       # 业务阈值（改这里 → 重启即生效，不用改代码）
├── evals/                  # 37 条用例 + 自动跑分 + 报告
└── docs/                   # 一页作品集说明、录屏脚本、设计取舍
```

---

## 7. 数据与合规说明

- 数据全部为**脚本生成的模拟数据**（固定随机种子，无任何真实客户信息），仅用于演示与评估；
- 客户名、报价、行业基准均为虚构，不代表任何公司真实政策；
- 代码仅用于求职作品集展示，遵循 hello-agents 的 CC BY-NC-SA 4.0 协议。

---

## 8. 下一步

- 支持自定义时间窗口（"近 45 天"），需要先补按天明细数据；
- 检索层替换为 bge-m3 + Chroma，加 rerank，观察召回变化；
- 在网页端加一个"规则阈值"面板，直接改 `config/rules.json` 并预览名单变化；
- 批量名单增加"一键生成跟进任务"（对接飞书任务/多维表格）。

## 9. 提交作品集前的 checklist（给未来要投简历的我）

- [ ] 截图三张：网页首屏、一次完整商机问答、`evals/report.md` 结果表（放在 README 第 2 节下面）
- [ ] 录一段 3 分钟演示视频（脚本见 `docs/demo-script.md`），上传后把链接写进 README
- [ ] 仓库推到自己的 GitHub，标题写清楚"基于 Datawhale Hello-Agents 的二次开发"，不要抹掉原作者
- [ ] `docs/portfolio-one-pager.md` 里的链接、数据与仓库现状对齐后再转 PDF 发 HR
- [ ] `docs/other-projects.md` 三张卡片填上真实项目名与数字
- [ ] 面试前过一遍 `docs/design-notes.md`，每个取舍都要能讲出"备选是什么、为什么放弃"

---

## 10. 生产部署与推理性能（2026-11-13 补充）

本地演示仍按第 5 节跑即可；部署到公网/容器时用：

```bash
HOST=0.0.0.0 PORT=8000 LLM_MODE=offline USE_CHECKPOINTER=false \
  python -m uvicorn api.server:app --host 0.0.0.0 --port 8000
```

`LLM_MODE=offline` 会强制走规则模板，**不发起任何模型调用**，也不依赖环境变量里是否残留 API Key；`USE_CHECKPOINTER=false` 编译不带 MemorySaver 的图，避免公网访客会话把进程内存撑爆。需要多轮状态时可设回 `true`（默认值），此时由 `MAX_SESSIONS` 做 FIFO 淘汰。

### 可调参数（均带范围校验，填错自动回落默认值）

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `LLM_MODE` | `auto` | `auto` / `offline` / `llm` |
| `LLM_TIMEOUT` | `8` | 单次模型调用超时（秒），原为 30 |
| `LLM_MAX_RETRIES` | `1` | SDK 重试次数，原为 2 |
| `LLM_CB_THRESHOLD` / `LLM_CB_COOLDOWN` | `3` / `30` | 连续失败熔断阈值与冷却秒数 |
| `USE_CHECKPOINTER` | `true` | 生产建议 `false` |
| `MAX_SESSIONS` | `256` | 会话状态上界（FIFO 淘汰） |
| `RATE_LIMIT_RPM` | `60` | 单 IP 每分钟请求数，超限返回 429 |
| `REQUEST_TIMEOUT` | `15` | 单请求超时（秒），超时返回 504 |
| `MAX_QUESTION_CHARS` | `500` | 提问长度上限，超长返回 422 |

`/api/health` 现在会返回模式、模型熔断状态、调用/失败/降级计数、请求数与错误数、会话数与各项限额；`/api/metrics` 单独给运行时指标。日志带 `request_id` 和耗时，只记异常类型，不打印 Key。

### 优化前后（同一台机器、同一基准脚本，离线模式，n=200）

| 指标 | 优化前 | 默认模式 | 生产模式（关 checkpointer） |
|---|---|---|---|
| 单请求平均 / p50 | 14.95 / 6.78 ms | 11.85 / 5.24 ms | **10.90 / 3.25 ms** |
| 16 并发 × 200 请求失败数 | **44** | **0** | **0** |
| 16 并发 p50 | 101.66 ms | 46.26 ms | **28.28 ms** |
| +500 会话内存增长 | +12.25 MB | +5.74 MB | **+0.03 MB** |
| 37 条答案 | 基准 | 逐字节一致 | 逐字节一致 |

四项评估指标在性能优化前后完全不变（当时为 100% / 97.3% / 100% / 100%；后续第 7 节评估体系升级修掉 H07 口径 bug 后，事实命中率提升为 100%）。改动只涉及检索索引、连接管理、会话生命周期、超时与限流，**没有改动任何打分口径或提示词**，所以数值结论的可追溯性不受影响。

> 说明：第 4 节与 `evals/report.md` 里的 `~8 ms` 是原开发机上的结果；同一脚本在本次环境实测为 9.5~14.9 ms，属机器与依赖版本差异，两种环境都用"10 ms 量级"描述更稳妥。


## 11. 上下文记忆与多轮对话（2026-11-22 补充）

销售的真实用法是一连串追问，而不是每条都从零开始：「A003 有什么商机」→「他的画像」→「继续」→「那转化率呢」。v0.2 虽然图里挂了 `MemorySaver`、状态里也留了 `history` 字段，但字段从没被读写，`ask()` 每轮还主动把 `advertiser_id` 清空——实际是无记忆的。这一版补上真正的会话上下文。

**设计：记忆层与 checkpointer 解耦。** 新增 `src/crm_agent/memory.py`，按 `session_id` 存用户可见的轮次（问题 + 回答摘要 + 意图 + 客户 + 时间），与图的执行状态无关。这样即便生产模式 `USE_CHECKPOINTER=false`，多轮上下文照样生效——不会为了性能关掉 checkpointer 就把记忆一起关掉。

**能做什么**
- **指代消解**：「他 / 这家 / 该客户 / 对方」继承上一轮客户；「继续 / 还有呢 / 再详细点 / 那转化率呢」继承上一轮意图。
- **不误伤批量问题**：「其他客户呢」「转化率最低的 10 个客户」不会被绑到上一轮那个客户；不同 `session_id` 之间彼此隔离。
- **追问补全**：分析类追问本身没有查询词（「还有呢」），会把上一轮的分析问题一起交给 Text-to-SQL，避免"有记忆反而答不出"。
- **在线模式带背景**：模型调用时注入最近 6 轮摘要，但数字仍以本轮工具返回为准，摘要不参与算分。
- **可持久化、可清空**：默认写 `data/conversations.db`（已 gitignore），重启后记忆还在；`DELETE /api/history` 开新话题。前端 `session_id` 落 localStorage，刷新页面接得上。

**接口**
```
GET    /api/history?session_id=xxx   查看该会话的轮次与统计
DELETE /api/history?session_id=xxx   清空该会话记忆
POST   /api/chat                     响应新增 turn（当前轮数）与 history_turns
```

**可调参数**（均带范围校验）

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `MEMORY_ENABLED` | `true` | 关掉则完全无记忆，回到单轮行为 |
| `MEMORY_PERSIST` | `true` | 是否落盘；`false` 为纯内存 |
| `MEMORY_DB_PATH` | `data/conversations.db` | 记忆库路径，独立于模拟数据 `crm.db` |
| `MEMORY_MAX_TURNS` | `12` | 单会话保留轮数（一问一答算 1 轮） |
| `MEMORY_MAX_SESSIONS` | `128` | 进程级会话上界，LRU 淘汰 |
| `MEMORY_ANSWER_CHARS` | `240` | 存进上下文的回答摘要长度 |

**验证**：`python -m unittest discover -s tests -v`，20 项测试覆盖记忆层（指代、意图继承、追问补全、批量隔离、会话隔离、轮数/会话上限、持久化重启、API 多轮与清除）与评估框架（验证器、失败归因、动作分类、一票否决）。37 条评估答案在加入记忆前后**逐字节一致**（评估每条用独立 thread，历史为空）。

## 12. 评估体系升级（2026-11-22 补充）

按《深入理解 AI Agent：设计原理与工程实践》（bojieli/ai-agent-book）第 7 章重构了评估。起因是一个真实 bug：`H07`「A003 近45天消耗多少」被模糊命中到"消耗合计"模板，返回全库数字 1,464,090——**答非所问却给了个像模像样的数字，比答不出更危险**。旧评估只因答案没出现关键词"45"判它失败，压根没发现这是错口径输出。这说明单一通过率掩盖了"做对了但说错了"。

**新增能力**（都在 `evals/`）：

| 文件 | 作用 | 对应书中概念 |
|---|---|---|
| `verifiers.py` | 结构化验证器：`keywords` / `refusal` / `contains_not`，未知类型保守判失败 | 验证器核实可复核事实，不采信自我陈述 |
| `failure_attribution.py` | 定位首个错误、类别、责任方、根因与后果 | 失败归因：从整条轨迹定位首个错误 |
| `prefix_cases.jsonl` | 4 条轨迹前缀回归用例（含追问继承客户后仍要拒绝） | 轨迹前缀回归：冻结上下文，验证下一步动作 |
| `cases.jsonl` | 每条新增 `set`（boundary/retention）与 `verifier` 字段 | 边界集 + 保留集 |
| `run_eval.py` | 多轮聚合、Pass^k、均值/95% CI、分组统计、归因 | Pass^k 可靠性、统计显著性 |

**关键设计取舍**：
- **refusal 验证器带一票否决**：即便答案说了"不支持"，只要漏出被禁止的编造数字（`forbid_substrings`）就判失败——防止"嘴上说拒绝、顺手报个错数"。
- **动作分类只看客观证据**：轨迹前缀的动作判定优先看 `evidence`（是否触发护栏、执行了什么 SQL），而不是扫答案文本的信号词——结论里的"口径/矛盾"等词曾让分类器把合法月度查询误判成拒绝。审核者只看产物，不与产出方共享推理。
- **边界集与保留集分开报**：这次改动只应影响 2 条边界用例（H07/A07），其余 35 条必须逐字节保持。37 条快照比对确认：改造前后仅 H07 的答案变化。
- **Pass^k 而非只看单次**：离线模式每次结果相同，Pass³ 与单次通过率都是 100%——这本身说明规则模式可复现；在线模型模式才真正体现连续多次不犯错的价值。

**统计口径说明**：`--repeats N` 报的是同一批用例重复 N 次的均值与波动。离线模式确定性执行，置信区间为 0；这不代表系统"绝对可靠"，只代表没有随机性。评估集只有 37 条，±几十分之一的差异不要过度解读——分差要超过噪声、在配对分析中成立，才值得据此决策。

**验证**：20 项专项测试（其中 10 项专门测评估框架本身，含"检测器会不会报警"的构造样例），37 条快照比对仅 H07 变化，评估四指标 + Pass³ + 边界/保留集 + 前缀回归 4/4 全过。

## 13. 统一工具层与规划编排（2026-11-24 补充）

这一轮的目标是在不改变已有答案的前提下，让 Agent 在**复杂任务、工具失败、外部服务不可用**时仍能给出可用结果。

### 新增能力

| 模块 | 作用 |
|---|---|
| `src/crm_agent/tools/registry.py` | 统一工具接入：`ToolSpec`（名字/描述/参数 schema/回退）+ `ToolResult`（状态/数据/错误码/耗时/降级标记）+ `ToolRegistry`（注册、调用、依赖编排、指标） |
| `src/crm_agent/tools/bootstrap.py` | 把现有 store / BM25 / 规则打分 / Text-to-SQL / 模型探针注册成 13 个工具；新增工具只需在这里加一条 `ToolSpec` |
| `src/crm_agent/planning.py` | 任务理解与规划：复合问题拆分、子任务/工具/依赖/步数预算、对话上下文槽位（客户、意图、指标、时间口径）与置信度 |
| `src/crm_agent/nodes/orchestrate.py` | 复合任务编排节点：逐项调用原子节点并隔离异常，同时用工具层执行带 `$ref` 依赖的计划 |
| `src/crm_agent/integration.py` | 结果整合：摘要 / 分段 / 引用 / 置信度 / 下一步 / 警告，可渲染 Markdown，也可作为 API 结构化字段 |
| `src/crm_agent/errors.py` | 统一错误码与安全文案：`message` 给用户、`detail` 只进日志，绝不回显路径或异常原文 |

### 行为边界（重要）

- **单意图问题完全走原路径**：`router → 原节点 → review`，37 条评估答案与改造前逐字节一致。
- **只有明确识别为复合任务**（如「给我 A003 的客户画像，同时分析各行业消耗汇总」）才走编排；拆不出两个不同意图、或超过步数预算时自动退回单任务。
- **LLM 不可用不影响核心功能**：无 Key / 熔断 / 网络异常时，检索、规则、SQL 模板与编排照常运行；`/api/health` 的 `llm.circuit_open` 与 `tools.metrics.degraded` 可观测降级状态。

### 工具框架怎么用

```python
from crm_agent.tools.registry import ToolRegistry, ToolSpec
from crm_agent.tools.bootstrap import P

reg = ToolRegistry()
reg.register(ToolSpec(
    name="echo", description="回显",
    handler=lambda text: "got:" + text,
    parameters={"text": P("str", required=True, min_len=2)},
    retries=1, fallback=lambda **kw: "fallback",
))
r = reg.call("echo", {"text": "ab"})     # ToolResult(ok, data, error_code, degraded, latency_ms...)
steps = [
    {"id": "a", "tool": "echo", "params": {"text": "hi"}},
    {"id": "b", "tool": "echo", "params": {"text": {"$ref": "a.data"}}, "depends_on": ["a"]},
]
results = reg.call_plan(steps)           # 依赖解析、重试、回退、结果汇总全部在框架内
```

参数 schema 支持 `type / required / default / enum / min / max / min_len / max_len / pattern`；校验失败返回 `invalid_parameters`，不执行 handler。上游失败时下游标记 `dependency_failed`，避免拿错数据继续算。

### 新增接口与配置

- `GET /api/tools`：工具目录（含参数 schema）+ 调用指标（次数/失败/降级/平均耗时）。
- `/api/chat` 响应新增 `plan`、`structure`、`confidence`、`degraded`、`warnings`、`tool_results`；旧字段保持不变。
- `/api/health` 与 `/api/metrics` 暴露工具层指标与规划开关。
- 环境变量（全部带范围校验，填错自动回落默认值）：`PLANNING_ENABLED`、`PLANNING_MAX_STEPS`、`TOOL_TIMEOUT_S`、`TOOL_MAX_RETRIES`、`TOOL_FALLBACK_ENABLED`、`DEGRADE_ON_EXTERNAL_FAILURE`。

**验证**：37 项测试全过（原 20 + 新增 17），评估四指标 / Pass³ / 边界集 2/2 / 保留集 35/35 / 前缀回归 4/4 全部 100%，平均延迟 52.8 ms；37 条答案快照与改造前零差异。

## 14. 每日优化与更新任务（定时 + 手动）

每天 22:56 由自动化调度执行；也可随时手动触发。任务是**幂等的更新流水线**：验证 → 评估 → 提交范围内改动 → 推送两端 → 写日志，不会为了"有产出"而制造重复提交。

### 任务入口

```bash
python scripts/daily_update.py                 # 每日调度入口
python scripts/daily_update.py --force         # 手动触发（忽略当日去重）
python scripts/daily_update.py --dry-run       # 预演：只验证评估，不提交不推送
scripts\daily_update.cmd --force               # Windows 双击/命令行一键触发
```

CLI 可覆盖配置：`--stages verify,evaluate,commit,push,report`、`--paths src,docs`、`--remotes gitee`、`--max-retries 5`、`--eval-repeats 3`、`--config <路径>`。

### 可配置范围

全部在 `config/daily_update.json`（修改该文件需手动提交，默认不自动同步）：

| 字段 | 含义 | 默认 |
|---|---|---|
| `stages` | 执行阶段，可裁剪 | 全部五阶段 |
| `scope_paths` | 允许的更新路径白名单 | `src/api/tests/evals/docs/web/config/README.md` 等 |
| `protected_paths` | 永不自动提交的路径 | `config/daily_update.json`、`evals/report.md`（延迟会波动的生成物）、`scripts/`、`logs/`、`data/` |
| `remotes` | 推送目标与分支 | `github-ssh:main`、`gitee:master` |
| `max_retries` / `retry_delay_s` | 每阶段重试次数与退避基数 | 3 次 / 2 秒（指数退避） |
| `eval_repeats` / `min_overall_pass_rate` | 评估轮次与通过率下限 | 3 轮 / 100% |
| `continue_on_failure` | 阶段失败后是否继续 | `false`（默认中止，防止带病推送） |
| `offline` / `ssh_home` | 强制离线评估 / SSH 密钥目录 | `true` / `/c/ssh-home` |

### 幂等与防重复

- **文件锁**：`data/daily_update.lock` 保证同一时刻只有一个实例；超过 `lock_ttl_s` 的陈旧锁自动清理。
- **当日去重**：`data/daily_update_state.json` 记录成功日期，当天再次运行直接跳过（手动触发用 `--force`）。
- **内容去重**：范围内无改动不提交；远端已含本地 HEAD 不推送。因此反复执行只会产生零次额外提交与推送。

### 日志与异常处理

- 文本日志：`logs/daily_update/YYYY-MM-DD.log`；结构化摘要：`logs/daily_update/latest.json` 与 `runs/<run_id>.json`。日志与状态文件均已加入 `.gitignore`。
- 每个阶段独立重试，指数退避（2s → 4s）；评估通过率不达标直接终止，不进入提交与推送。
- 提交前扫描暂存区密钥，命中即 `git reset` 取消暂存并中止，避免凭据入库。
- 推送按远端独立处理：一端失败记录为 `failed`，不影响另一端；整体状态为 `failed` 时退出码 1，锁冲突为 2，成功/跳过为 0。

## 15. 2026-11-26 稳定性与性能优化

本轮优化均针对真实代码路径，未改变任何评估答案：

- **超时不再写"幽灵记忆"**：`POST /api/chat` 超时后客户端收到 504，后台线程继续算出的答案不再写入会话；`SalesAgent.ask` 新增可选 `cancel_event` 参数，旧调用方行为不变。
- **复合编排空客户修复**：不带客户名的复合分析问题不再把 `advertiser_id=None` 传给要求字符串的 SQL 工具，避免连锁的依赖失败。
- **SQL 白名单加固**：`guard()` 现在能识别逗号分隔的多表（`FROM advertisers, sqlite_master` 会被拒绝），同时不再把 `replaced_at` / `created_at` 这类合法列名误判为危险关键字。
- **客户名匹配零重复查询**：`Context` 构建时一次性生成"客户名前 3 字 → ID"的有序映射，顺序与原全表查询一致；实测 50 次含客户名的提问触发 **0 次** `list_advertisers` 调用（原来 50 次）。
- **长驻服务跨天口径正确**：`scoring.days_since/days_until` 改为调用时取当天日期，不再使用模块导入时固定的 `TODAY`。
- **定时任务暂存安全**：密钥扫描失败时只重置本任务暂存的路径，不再清空用户事先 `git add` 的内容。

**验证**：49 项测试全过（原 37 + 新增 12），评估四指标 / Pass³ / 边界集 / 保留集 / 前缀回归全部 100%，37 条答案与优化前逐字节一致（差异 0）。

## 16. 2026-11-27 工具结果复用与健壮性优化

本轮不追求新功能，重点是把已有工具层的能力真正用在复合任务主链路上，并收敛几处生产风险：

- **复合编排复用工具结果**：`orchestrate_node` 把成功的 `sql.generate` / `sql.execute` / `kb.retrieve` 结果按 `step_id` 预置给子任务，分析/知识节点优先复用，不再重复查库、重复跑 BM25、重复调用模型生成 SQL。实测两个复合问题里 `nl_to_sql` 调用从 4 次降到 2 次，知识节点的重复检索从 1 次降到 0 次。降级（fallback）结果不复用，避免空值覆盖真实数据；追问因问题被补全，也不复用。
- **ctx 注入改用签名判定**：工具注册后用 `inspect.signature` 判断 handler 是否显式声明 `ctx`，不再靠捕获 TypeError 文案猜测——后者在 handler 自身抛出同样文案时会重复执行，造成副作用。
- **不可重试错误立即中止**：`ValueError/TypeError/KeyError/AttributeError` 归为 `invalid_parameters` 且不再重试，避免对配置/逻辑错误反复发起调用。
- **连接释放后线程本地重置**：`CRMStore.close()` 清空 thread-local 连接引用，避免同线程关闭后再查询拿到已关闭连接。
- **上下文不再被长回答截断**：`build_history_prompt` 遇到超长轮次改为跳过而非中止，更早的短轮次仍可进入模型背景，追问时不易丢指代对象。

**验证**：55 项测试全过（原 49 + 新增 6），评估四指标 / Pass³ / 边界集 / 保留集 / 前缀回归全部 100%，37 条答案与优化前逐字节一致（差异 0）。

## 17. Agent 协作文件体系（AGENTS.md / skills / notes）

> Agent collaboration files (AGENTS.md / skills / notes) — bilingual Chinese / English

<!-- 中文 -->
参照 DeepSeek Harness 的组织方式，为人与 AI 协作者补了一套"硬约束 + 专项 skill + 决策记录"的三层文件。它不改变产品功能，只让后续协作有据可依、不重复造轮子：

- **`AGENTS.md`**（根目录）：协作总纲。只放六条首要原则（离线优先、评估零回归、数字不被模型改写、SQL 只读白名单、错误脱敏、单意图走原路径）、仓库布局、命令、编码约定和审查清单；设计取舍的"为什么"全部指向 `docs/design-notes.md`，不复制。
- **`.agents/skills/*/SKILL.md`**（6 个专项技能，均带 YAML frontmatter）：`eval-regression`（验证与零回归快照比对）、`tool-layer`（工具注册/校验/重试/编排/复用）、`sql-and-data`（只读护栏/口径边界/造数）、`offline-resilience`（离线降级/熔断/幽灵记忆）、`context-and-memory`（记忆/指代/规划/编排）、`pre-push-checks`（提交前检查与两端同步）。每个 skill 只写"何时用 + 怎么做 + 验证"，引用真实文件路径。
- **`.agents/notes/`**：决策记录体系，按 `lifecycle`（proposed/implemented/rejected）× `class`（feature/bug-fix/simplification/architecture/process/testing）双轴编码路径。已落地 3 条 implemented 记录（记忆与 checkpointer 解耦、评估零回归门禁、定时任务只验证与同步），每条含 `## Problem / Decision / Alternatives considered / Consequences`。规范见 `.agents/notes/README.md`。

**中英双语**：以上所有文件均为单文件双语——每节先用一种语言、紧接另一种语言的等价译文（`<!-- 中文 -->` / `<!-- EN -->` 标记）。两种语言同等有效，改动须同步更新两处；章节标题保持原样以维持机器校验与 Markdown 锚点。

**分工**：README 管项目全貌与变更日志；design-notes 管 31 节"为什么 + 备选方案"；Agent Notes 管需要独立状态生命周期与备选清单的具体决策；`evals/report.md` 是生成物。一处事实只有一个归属，改实现时同步更新对应归属，不留陈旧陈述。

<!-- EN -->
Following DeepSeek Harness's organization, we added a three-layer set of files — "hard constraints + specialized skills + decision records" — for human and AI collaborators. It changes no product behavior; it only makes later collaboration traceable and avoids reinventing the wheel:

- **`AGENTS.md`** (root): the collaboration charter. It holds only six core principles (offline-first, zero-regression gate, numbers never rewritten by the model, read-only SQL whitelist, error redaction, single intent keeps the original path), repository layout, commands, conventions, and a review checklist. All "why" rationale points to `docs/design-notes.md` and is not duplicated here.
- **`.agents/skills/*/SKILL.md`** (6 skills, each with YAML frontmatter): `eval-regression` (verification and zero-regression snapshot diffing), `tool-layer` (registration/validation/retry/orchestration/reuse), `sql-and-data` (read-only guard/scope boundaries/fixtures), `offline-resilience` (offline degradation/circuit breaking/ghost memory), `context-and-memory` (memory/reference resolution/planning/orchestration), `pre-push-checks` (pre-commit checks and two-remote sync). Each covers only "when + how + verification" and cites real file paths.
- **`.agents/notes/`**: decision records whose paths encode `lifecycle` (proposed/implemented/rejected) × `class` (feature/bug-fix/simplification/architecture/process/testing). Three implemented records exist (memory decoupled from checkpointer, zero-regression evaluation gate, daily task restricted to verification and sync), each with `## Problem / Decision / Alternatives considered / Consequences`. See `.agents/notes/README.md` for the format.

**Bilingual**: every file above is bilingual within a single file — each section gives one language followed immediately by the equivalent translation (`<!-- 中文 -->` / `<!-- EN -->` markers). Both languages are authoritative and every change must update both; section headings stay verbatim to preserve mechanical validation and Markdown anchors.

**Ownership**: README owns the project overview and changelog; design-notes owns 31 sections of "why + alternatives"; Agent Notes own specific decisions needing their own status lifecycle and alternatives ledger; `evals/report.md` is generated. One fact has one home — update the matching home when implementation changes, leaving no stale statements.

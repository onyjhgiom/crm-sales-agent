# Agent Notes

<!-- 中文 -->
本项目只有一类设计文档放这里：**Agent Note** —— 记录一个影响代码库的决策或提案，重点写 *为什么* 和 *放弃了什么*，也就是代码和 README/design-notes 都承载不下的部分。本文件定义它们放哪里、何时写、文件内格式。

> 与 `docs/design-notes.md` 的分工：design-notes 是面试追问用的"32 节为什么 + 备选方案"清单，按主题组织、长期维护；Agent Note 按决策的**状态与类别**组织，记录一个具体决策从提案到落地（或被否）的完整理由。已经写进 design-notes 的决策不要在这里复制；只有当某个决策的备选方案、负面保证或重新引入条件需要独立、可追溯的载体时才写 Agent Note。

<!-- EN -->
There is exactly one kind of design document here: the **Agent Note** — a decision or proposal affecting the codebase, focused on *why* and *what was given up*, i.e. what code, README and design-notes cannot carry. This file defines where they live, when to write one, and their in-file format.

> Division of labor with `docs/design-notes.md`: design-notes is a topic-organized, long-lived list of 31 "why + alternatives" sections for interview follow-ups. Agent Notes are organized by decision **status and class**, recording the full reasoning of one decision from proposal to implementation (or rejection). Do not duplicate decisions already in design-notes; write an Agent Note only when a decision's alternatives, negative guarantees, or reintroduction conditions need an independent, traceable home.

> Both languages are authoritative. Chinese sections are marked `<!-- 中文 -->`, English sections `<!-- EN -->`. Headings stay in Chinese here so existing anchors such as `#文件内格式` and `#归档与删除` keep working. / 两种语言同等有效；标题保留中文以维持 `#文件内格式`、`#归档与删除` 等既有锚点。

## 布局与命名（路径双轴编码）

> Layout & naming (path encodes two axes)

<!-- 中文 -->
每个 Agent Note 的路径为 `{lifecycle}/{class}/yyyy-mm-dd-topic-title.md`，两个维度都编码在路径里：

- **Lifecycle**（顶层目录）是决策状态，状态变化即移动文件：
  - `proposed/` —— 实现前评审过的提案，尚未落地（或部分落地）。
  - `implemented/` —— 已发布的决策，记录决定了什么、放弃了什么，并**随实际发布内容保持最新**（代码改了路径/默认值/机制，同一改动里更新事实，不改决策本身）。见 [implemented/AGENTS.md](implemented/AGENTS.md)。
  - `rejected/` —— 评审后否决。仅当理由能阻止一个诱人的重大错误时保留，否则删除整套文件。
- **Class**（嵌套目录）是决策类别，取自封闭集合：
  - `feature` 新增用户/模型可见能力
  - `bug-fix` 修正缺陷或补上复盘暴露的缺口
  - `simplification` 删减代码/行为而不新增能力
  - `architecture` 关于**已发布源码**的结构决策（包关系、运行时词汇）
  - `process` 代码周围的工具/策略/工作流（门禁、包管理、定时任务）
  - `testing` 测试基础设施与策略

文件名日期是**首次提案**日期（以 git 历史为准）。交叉引用用相对 markdown 链接，不用裸文本或编号，这样可被机械校验且能随文件移动存活。活跃树即工作清单；不要建集中 `INDEX.md`——它重复路径元数据，还让无关改动争抢同一个生成文件。

<!-- EN -->
Every Agent Note lives at `{lifecycle}/{class}/yyyy-mm-dd-topic-title.md`; both axes are encoded in the path:

- **Lifecycle** (top-level directory) is the decision status; a status change moves the file:
  - `proposed/` — a reviewed pre-implementation proposal, not yet shipped (or only partly).
  - `implemented/` — a shipped decision: what was decided and what was given up, **kept current with what actually shipped** (if code changes a path/default/mechanism, update the fact in the same change without rewriting the decision). See [implemented/AGENTS.md](implemented/AGENTS.md).
  - `rejected/` — rejected after review. Keep only when the rationale prevents an attractive major mistake; otherwise delete the whole file.
- **Class** (nested directory) is a closed set of decision categories:
  - `feature` — new user- or model-visible capability
  - `bug-fix` — a defect corrected or a gap found in retrospective
  - `simplification` — code/behavior removed without adding capability
  - `architecture` — structural decisions about **shipped source** (package relations, runtime vocabulary)
  - `process` — tooling/policy/workflow around the code (gates, package management, scheduled tasks)
  - `testing` — test infrastructure and strategy

The date in the filename is the **first proposal** date (per git history). Cross-reference with relative markdown links, not bare text or numbers, so links are mechanically checkable and survive file moves. The active tree is the work list; do not create a centralized `INDEX.md` — it duplicates path metadata and makes unrelated changes fight over one generated file.

## 何时写

> When to write

<!-- 中文 -->
仅在**代码、测试和现有文档都解释不了**的持久决策理由上，于同一改动里新增或更新 Agent Note：

- 对未来工作的实质提案，从 `proposed/` 开始；已做出的决策，从 `implemented/` 开始。
- 选匹配决策的 class 目录。
- 更新**已经承载该决策**的 Agent Note 即满足要求，不要新建重复件。
- 机械/局部改动（含局部 UI 表现）豁免。
- 一个 Agent Note 不能被编辑成**另一个决策**：那要用新 Note 并交叉链接。

<!-- EN -->
Add or update an Agent Note in the same change only for durable rationale that **code, tests and existing docs cannot explain**:

- A substantive proposal for future work starts in `proposed/`; an already-made decision starts in `implemented/`.
- Pick the class directory matching the decision.
- Updating the Agent Note **already carrying that decision** satisfies the requirement; do not create duplicates.
- Mechanical/local changes (including local UI presentation) are exempt.
- An Agent Note must not be edited into **a different decision**: create a new Note and cross-link instead.

## 文件内格式

> In-file format

<!-- 中文 -->
活跃 Agent Note 的头三行严格为：

```markdown
# Agent Note: <title>

Status: <status>
```

`Status` 三选一，且必须与所在目录一致：`proposed` / `implemented` / `rejected — <一行原因>`。日期放文件名，git 记录其余历史。

正文以 `## Problem`（动机，独立于解法）开头，其余按生命周期用固定章节名：

**`proposed/`**
```
## Problem  ## Proposal  …自定义技术节…  ## Alternatives considered  ## Acceptance criteria  ## Risks
```

**`implemented/`**
```
## Problem  ## Decision  …自定义技术节…  ## Alternatives considered  ## Consequences
```
`implemented/` 禁用提案期标题：`## Proposal` / `## Plan` / `## Migration plan` / `## Acceptance criteria`。

**每个 Agent Note 必须有 `## Alternatives considered`**：每个真实备选方案 + 为何落败，一段一方案。没有备选方案的决策会招致重新争论——这正是 Agent Note 要防的失败。

<!-- EN -->
The first three lines of an active Agent Note are strictly:

```markdown
# Agent Note: <title>

Status: <status>
```

`Status` has exactly three values and must match the directory: `proposed` / `implemented` / `rejected — <one-line reason>`. The date lives in the filename; git records the rest of the history.

The body opens with `## Problem` (motivation, independent of solution), then uses lifecycle-specific fixed section names:

**`proposed/`**
```
## Problem  ## Proposal  ...custom technical sections...  ## Alternatives considered  ## Acceptance criteria  ## Risks
```

**`implemented/`**
```
## Problem  ## Decision  ...custom technical sections...  ## Alternatives considered  ## Consequences
```
`implemented/` forbids proposal-phase headings: `## Proposal`, `## Plan`, `## Migration plan`, `## Acceptance criteria`.

**Every Agent Note must have `## Alternatives considered`**: one paragraph per real alternative and why it lost. A decision without alternatives invites re-litigation — exactly the failure an Agent Note exists to prevent.

> 说明 / Note: 上文代码块中的章节名必须保持英文原样，校验脚本依赖它们；中文译文写在章节内部。 / Section names in the code blocks above must stay in English verbatim — validation scripts depend on them; Chinese translations live inside each section.

## 归档与删除

> Archiving & deletion

<!-- 中文 -->
- 只描述小 UI 调整或纯机械改动的 implemented Note：删除（中文对应文件一起删，修好入链）。
- 局部 bug 修复、性能改动、新能力、实质行为决策**不因实现小**而被删除。
- 其余 implemented Note 仅在决策已完整、理由不太可能指导未来、但仍有历史决策价值时归档到 `archived/{class}/yyyy-mm-dd-topic-title.md`（归档树没有 implemented 层）。一旦封存即**永久冻结**，不再编辑，也不作为当前行为的依据。
- 永不归档 proposed Note；过时提案直接否决。rejected Note 仅在能阻止合理错误时保留。

本项目尚无 `archived/` 树；首次需要归档时按上述规则创建，并遵守冻结约定。

<!-- EN -->
- An implemented Note describing only a small UI tweak or purely mechanical change: delete it (delete the counterpart-language file too, and fix inbound links).
- Local bug fixes, performance changes, new capabilities, and substantive behavior decisions are **not** deleted merely because the implementation was small.
- Other implemented Notes are archived to `archived/{class}/yyyy-mm-dd-topic-title.md` (the archive tree has no implemented layer) only when the decision is complete, its rationale is unlikely to guide future work, but it still has historical value. Once sealed it is **permanently frozen**: no further edits, and no longer an authority for current behavior.
- Never archive a proposed Note; reject outdated proposals instead. Keep a rejected Note only when it can prevent a plausible mistake.

This project has no `archived/` tree yet; create it per the rules above when archiving is first needed, and honor the freeze contract.

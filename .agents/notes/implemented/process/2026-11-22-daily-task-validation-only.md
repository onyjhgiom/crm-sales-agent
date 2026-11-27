# Agent Note: 每日定时任务只做验证与同步，不改代码

> Agent Note: The Daily Scheduled Task Only Verifies and Syncs — It Never Edits Code
>
> Both languages are authoritative. Chinese sections are marked `<!-- 中文 -->`, English sections `<!-- EN -->`. Section headings stay in English verbatim for mechanical validation. / 两种语言同等有效；章节标题保持英文原样以通过机械校验。

Status: implemented

## Problem

<!-- 中文 -->
"每晚自动优化"若让无人值守任务直接改写源码，风险是：没有评审、缺乏上下文、失败时留下半成品，并把坏状态推送到两端。需要一种在无人值守下仍能给出确定性结论的机制。

<!-- EN -->
If "nightly auto-optimization" lets an unattended task rewrite source code directly, the risks are: no review, no context, half-finished state on failure, and a bad state pushed to both remotes. We need a mechanism that still yields a deterministic conclusion when nobody is watching.

## Decision

<!-- 中文 -->
`scripts/daily_update.py` 的任务边界限定为**验证 → 评估 → 提交已有改动 → 推送两端 → 写日志**，不做任何代码生成或改写。代码优化仍由开发者在会话中完成，任务只做质量把关与同步。三重去重保证可重复执行且不产生重复提交/推送：文件锁（防并发 + TTL 清理陈旧锁）、当日状态去重（`--force` 只绕过这一层）、内容去重（无范围改动不提交、远端已含 HEAD 不推送）。评估通过率不达标直接阻断提交与推送；`evals/report.md` 因延迟数字波动列入保护路径，避免每天产生无意义提交。详见 skill [pre-push-checks](../../../skills/pre-push-checks/SKILL.md) 与 design-notes §23–25。

<!-- EN -->
`scripts/daily_update.py` is bounded to **verify → evaluate → commit existing changes → push both remotes → write logs**; it never generates or rewrites code. Code optimization stays in developer sessions; the task only gates quality and syncs. Three dedup layers make repeated runs safe without duplicate commits/pushes: a file lock (concurrency plus TTL cleanup of stale locks), same-day state dedup (`--force` bypasses only this layer), and content dedup (no scoped change → no commit; remote already has HEAD → no push). An evaluation pass rate below threshold blocks commit and push; `evals/report.md` is on the protected path because latency numbers fluctuate, preventing meaningless daily commits. See the [pre-push-checks](../../../skills/pre-push-checks/SKILL.md) skill and design-notes §23–25.

## Alternatives considered

### 为什么不让任务自动改代码（如自动调参、自动修 bug）？ / Why not let the task edit code automatically (auto-tuning, auto bug fixes)?

<!-- 中文 -->
无人值守改动没有评审与上下文，失败会留半成品，且"为了有产出"会制造无意义提交。验证与同步是确定性任务，能稳定给出"当前是否健康、是否已同步"的结论；代码优化交给有人审阅的会话更可靠。

<!-- EN -->
Unattended edits have no review or context, leave half-finished state on failure, and create meaningless commits "to have output". Verification and sync are deterministic and reliably answer "is the project healthy, is it synced". Code optimization is safer in a reviewed session.

### 为什么三重去重，而不只按日期？ / Why three dedup layers instead of a date check alone?

<!-- 中文 -->
只按日期会漏两类真实场景：上次推送失败后次日需要补推、手动强制重跑。文件锁管并发，当日状态管重复跑，内容检查管重复提交/推送。`--force` 只绕过第二层，另外两层仍生效——这正是"可手动触发但不造成重复更新"的关键。

<!-- EN -->
A date check alone misses two real scenarios: a failed push that must be retried the next day, and a deliberate manual rerun. The file lock handles concurrency, same-day state handles repeated runs, and content checks handle duplicate commits/pushes. `--force` bypasses only the second layer; the other two still apply — that is the key to "manually triggerable without duplicate updates".

### 为什么评估不达标仍要阻断推送？ / Why block pushes when evaluation fails?

<!-- 中文 -->
若验证/评估失败后仍允许提交推送，会把坏状态同步到两端，凌晨无人值守难以察觉。`continue_on_failure=false` 是默认，失败即中止且状态文件不记成功，次日仍可重试。

<!-- EN -->
Allowing commit/push after a failed verify/eval would sync a bad state to both remotes at an hour when nobody notices. `continue_on_failure=false` is the default: failure aborts, the state file does not record success, and the next day can still retry.

## Consequences

<!-- 中文 -->
- 每晚得到的是健康与同步状态报告，不是不可预测的代码差异。
- 任务成功/跳过退出码 0、失败 1、锁冲突 2；失败不记为当日成功，保留重试机会。
- 推送两端独立处理：GitHub 走 SSH（HTTPS 代理 502），Gitee 走 HTTPS；一端失败不影响另一端，整体记为 partial/failed。
- 提交前扫描暂存区密钥，命中只重置本任务暂存的路径，不清空用户预先 stage 的内容。

<!-- EN -->
- Each night produces a health-and-sync status report, not unpredictable code diffs.
- Exit codes: 0 success/skip, 1 failure, 2 lock conflict. A failure is not recorded as a successful day, preserving the retry opportunity.
- Remotes are handled independently: GitHub over SSH (HTTPS proxy returns 502), Gitee over HTTPS; one side failing does not block the other, and the overall status becomes partial/failed.
- The staged diff is scanned for secrets before commit; on a hit only this task's paths are unstaged, never the user's pre-staged work.

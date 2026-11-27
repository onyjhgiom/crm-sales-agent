# Agent Note: 评估拆验证器/归因/回归，并以零回归作为改动门禁

> Agent Note: Splitting Evaluation into Verifiers / Attribution / Regression, with Zero Regression as the Change Gate
>
> Both languages are authoritative. Chinese sections are marked `<!-- 中文 -->`, English sections `<!-- EN -->`. Section headings stay in English verbatim for mechanical validation. / 两种语言同等有效；章节标题保持英文原样以通过机械校验。

Status: implemented

## Problem

<!-- 中文 -->
早期评估只有一个"通过率"，答错了不知道错在哪一步，改动后也无法区分"边界问题修好了"与"其他答案被改坏了"。一次 H07 口径错误（把近 45 天问成全库消耗 `1,464,090`）如果只看总通过率会被 36 个正确用例掩盖。

<!-- EN -->
Early evaluation had a single "pass rate": a wrong answer gave no clue which step failed, and after a change one could not distinguish "the boundary case was fixed" from "other answers broke". An H07 scope error (answering a 45-day question with the all-database total `1,464,090`) was masked by 36 correct cases when looking only at the overall rate.

## Decision

<!-- 中文 -->
评估体系拆为三层（`evals/`）：`verifiers.py` 三类验证器（keywords / refusal / contains_not）+ `failure_attribution.py` 六签名失败归因（定位**首个**偏离步）+ `prefix_cases.jsonl` 轨迹前缀回归（冻结上下文只看下一步动作）。用例按 `set=boundary|retention` 分组，指标含四率、Pass^k、95% CI、边界/保留集通过率、前缀回归。`run_eval.py` 对 boundary、retention、prefix 三项任一低于 100% 即返回码 1；`scripts/daily_update.py` 进一步以 `overall_pass_rate` 阻断提交与推送。所有改动还需用干净 worktree 导出 HEAD 基线，对 37 条答案逐字节比对——差异必须为 0（除非是有意且经验证的边界修复）。详见 skill [eval-regression](../../../skills/eval-regression/SKILL.md) 与 design-notes §15–17。

<!-- EN -->
Evaluation was split into three layers (`evals/`): `verifiers.py` with three verifier types (keywords / refusal / contains_not); `failure_attribution.py` with six-signature attribution that locates the **first** deviating step; and `prefix_cases.jsonl` trajectory-prefix regression (frozen context, only the next action is scored). Cases are grouped by `set=boundary|retention`; metrics include the four rates, Pass^k, 95% CI, boundary/retention pass rates, and prefix regression. `run_eval.py` exits 1 if boundary, retention, or prefix falls below 100%; `scripts/daily_update.py` additionally blocks commit/push on `overall_pass_rate`. Every change must also export a clean HEAD worktree and diff all 37 answers byte-for-byte — differences must be zero (unless an intentional, verified boundary fix). See the [eval-regression](../../../skills/eval-regression/SKILL.md) skill and design-notes §15–17.

## Alternatives considered

### 为什么不用单一通过率？ / Why not a single pass rate?

<!-- 中文 -->
单一数字无法定位失败步骤，也无法证明"修一个错没弄坏其他"。边界集只有 2 条，靠总通过率看不出它被修好；保留集 35 条的逐字节一致才是"没变坏"的证据。

<!-- EN -->
A single number cannot locate the failing step, nor prove that fixing one thing did not break another. The boundary set has only 2 cases, so an overall rate cannot show it was fixed; byte-for-byte identity of the 35 retention cases is the evidence that nothing got worse.

### 为什么验证器要一票否决 forbid_substrings？ / Why does the verifier one-vote-veto on `forbid_substrings`?

<!-- 中文 -->
H07 这类错误的特征是"给出了像模像样的错数字"。只检查是否命中拒绝信号词不够——答案可能既说了"口径不支持"又顺手报了全库总数。命中任一 `forbid_substrings`（如 `1,464,090`）即失败，消除这种侥幸。

<!-- EN -->
The signature of an H07-class error is "a plausible-looking wrong number". Checking only for refusal signal words is insufficient — an answer might say "unsupported scope" and still quote the all-database total. Hitting any `forbid_substrings` entry (e.g. `1,464,090`) fails immediately, removing that loophole.

### 为什么归因看首个偏离而非最后报错？ / Why attribute the first deviation rather than the last error?

<!-- 中文 -->
最后一个错误往往是级联后果。按六个签名顺序扫描定位**首个**偏离步，才能给出根因与后果的区分，避免把症状当病因。

<!-- EN -->
The last error is usually a cascading consequence. Scanning the six signatures in order to locate the **first** deviation gives a root cause distinct from its consequences, avoiding treating a symptom as the disease.

### 为什么前缀回归单独成集？ / Why a separate prefix regression set?

<!-- 中文 -->
多轮记忆继承会改变上下文，但下一步动作（如仍须拒绝自定义窗口）不能变。把它独立成 4 条冻结前缀，能专门守住"变聪明了却越界"的回归。

<!-- EN -->
Multi-turn memory inheritance changes context, but the next action (e.g. still refusing a custom window) must not change. Four frozen prefixes guard specifically against the regression of "getting smarter but stepping outside the boundary".

## Consequences

<!-- 中文 -->
- 每次行为改动都要跑完整门禁并做快照比对，验证成本上升；换来的是可证明的"零回归"，这成为本项目的核心工程承诺。
- 评估离线确定性执行（CI=0），延迟数字随机器波动，报告只以量级表述，不作为门禁。
- `evals/report.md` 是生成物，列入每日任务保护路径，手改无效。
- 边界修复时允许**仅该边界用例**的答案变化，但需要新的 refusal 验证与前缀回归同时通过。

<!-- EN -->
- Every behavior change requires the full gate plus a snapshot diff, raising verification cost; in exchange we get provable "zero regression", now a core engineering commitment of this project.
- Evaluation runs deterministically offline (CI=0); latency fluctuates by machine, so reports state orders of magnitude only and latency is not a gate.
- `evals/report.md` is generated, listed on the daily task's protected path — manual edits do not stick.
- A boundary fix may change **only that boundary case's** answer, provided a new refusal verification and prefix regression both pass.

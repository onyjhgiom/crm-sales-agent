---
name: eval-regression
description: Use after any code change in crm-sales-agent, before claiming a fix/optimization works or before committing, to run the smallest sufficient verification and prove zero regression against a clean baseline. Covers the unittest suite, the four evaluation metrics with Pass^k, boundary/retention/prefix sets, keyless offline replay, and byte-for-byte answer snapshot comparison. | 在任何代码改动后、宣称修复/优化有效或提交之前使用，执行最小充分验证并证明相对干净基线零回归。覆盖单元测试、四项评估指标与 Pass^k、边界/保留/前缀集、无 Key 离线重放与逐字节答案快照比对。
---

# Eval & zero-regression gate / 评估与零回归门禁

<!-- EN -->
Any behavior-affecting change must prove two things separately: **the boundary case got better** and **the retention set stayed byte-for-byte identical**. Running only one of them is not evidence. The evaluation is keyless and deterministic in offline mode, so a green result is reproducible.

<!-- 中文 -->
任何影响行为的改动都必须分别证明两件事：**边界用例变好了**，**保留集仍逐字节一致**。只跑其中一项不构成证据。评估在无 Key 的离线模式下确定性执行，因此绿灯结果可复现。

> Both sections are authoritative. Each part below appears in English first (`<!-- EN -->`), followed by the Chinese translation (`<!-- 中文 -->`). / 两种语言同等有效；下文每部分先英文后中文。

## When to use / 何时使用

<!-- EN -->
- After editing anything under `src/crm_agent/`, `api/`, or `evals/`.
- Before committing, pushing, or running `scripts/daily_update.py` (which enforces this gate itself).
- When fixing a wrong answer (e.g. a H07-style scope error) or adding a capability (e.g. composite orchestration).

<!-- 中文 -->
- 修改 `src/crm_agent/`、`api/`、`evals/` 下任何内容之后。
- 提交、推送或运行 `scripts/daily_update.py` 之前（脚本自身也会强制此门禁）。
- 修复错误答案（如 H07 式口径错误）或新增能力（如复合任务编排）时。

## Step 1 — unit tests and compile / 步骤 1 — 单元测试与编译

```sh
python -m compileall -q src api tests scripts
env -u OPENAI_API_KEY python -m unittest discover -s tests -v   # Unix; see AGENTS.md for Windows
```

<!-- EN -->
55 tests across five files must pass. New behavior needs its own regression test file (current: `test_memory.py`, `test_eval_framework.py`, `test_enhancements.py`, `test_daily_optimizations.py`, `test_reuse_and_robustness.py`). A fix without a test that fails on the old code is not a fix.

<!-- 中文 -->
5 个文件中的 55 项测试必须全过。新行为需要自己的回归测试文件（当前为 `test_memory.py`、`test_eval_framework.py`、`test_enhancements.py`、`test_daily_optimizations.py`、`test_reuse_and_robustness.py`）。没有能在旧代码上失败的测试，就不算修复。

## Step 2 — evaluation gate / 步骤 2 — 评估门禁

```sh
env -u OPENAI_API_KEY python evals/run_eval.py --repeats 3
```

<!-- EN -->
Required outcome, all at once:

- Intent accuracy / answer (keyword) hit rate / citation coverage / self-check rate = 100%.
- `overall_pass_rate.mean` = 1.0; `pass_pow_k` = 100% for the chosen `--repeats`.
- `boundary` set = 2/2 (A07 number-scope, H07 refusal), `retention` set = 35/35.
- Prefix regression = 4/4 (PFX-01..04); PFX-04 must still refuse a custom window after multi-turn memory inheritance.
- Exit code 0. `run_eval.py` returns 1 if any of boundary rate, retention rate, or prefix pass is below 1.0.

Metrics come from `evals/run_eval.py:aggregate()`: each has `mean/std/ci95/runs`; offline runs show CI=0 because execution is deterministic. Report latency as an order of magnitude (e.g. "tens of ms"), never a fixed number — it varies by machine and dependency version.

<!-- 中文 -->
必须同时满足以下全部条件：

- 意图识别准确率 / 回答（关键词）命中率 / 引用覆盖率 / 自检通过率 = 100%。
- `overall_pass_rate.mean` = 1.0；所选 `--repeats` 下的 `pass_pow_k` = 100%。
- `boundary` 集 = 2/2（A07 数字口径、H07 拒绝），`retention` 集 = 35/35。
- 前缀回归 = 4/4（PFX-01..04）；PFX-04 在多轮记忆继承后仍必须拒绝自定义窗口。
- 退出码 0。边界率、保留率或前缀通过率任一低于 1.0，`run_eval.py` 返回 1。

指标来自 `evals/run_eval.py:aggregate()`：每项含 `mean/std/ci95/runs`；离线运行因确定性执行而 CI=0。延迟只报量级（如"几十毫秒"），不要报固定数值——它随机器与依赖版本波动。

## Step 3 — byte-for-byte snapshot comparison / 步骤 3 — 逐字节快照比对

<!-- EN -->
Tests and aggregate rates can hide a changed answer string. Compare the 37 answers against a clean baseline:

1. Export a pristine HEAD into a throwaway worktree:
   ```sh
   git worktree add -f <tmp>/baseline HEAD
   ```
2. Run the same dump script against both `baseline` and the working tree. The dump script must, for every case in `evals/cases.jsonl`, call `SalesAgent(use_llm=False).ask(question, advertiser_id, thread_id=eval-{id})` and record `answer / intent / advertiser_id / citations / review_ok`. Use a fresh thread id per case so history is empty and cross-turn contamination is impossible.
3. `diff` the two JSON outputs. Requirement: **zero differences** unless the change is intentionally a boundary fix, in which case only the named boundary case may differ, and its new answer must pass the `refusal` verifier.
4. Remove the worktree and all temp files afterward (`git worktree remove --force <tmp>/baseline`).

Historical results: the tool-layer/unified-registry change, the 11-26 stability fixes, and the 11-27 result-reuse change each produced 37/37 identical answers (diff = 0). Treat any unexpected diff as a regression, not as "the model said it slightly differently".

<!-- 中文 -->
测试与汇总通过率可能掩盖某条答案字符串被改动。需将 37 条答案与干净基线比对：

1. 把纯净的 HEAD 导出到一次性工作树：
   ```sh
   git worktree add -f <tmp>/baseline HEAD
   ```
2. 对 `baseline` 与当前工作树跑同一个导出脚本。脚本必须对 `evals/cases.jsonl` 中每条用例调用 `SalesAgent(use_llm=False).ask(question, advertiser_id, thread_id=eval-{id})`，记录 `answer / intent / advertiser_id / citations / review_ok`。每条用例使用全新 thread id，确保历史为空、不可能跨轮污染。
3. `diff` 两份 JSON 输出。要求：**零差异**；除非改动是有意的边界修复，此时只允许具名边界用例不同，且新答案必须通过 `refusal` 验证器。
4. 事后清理工作树与全部临时文件（`git worktree remove --force <tmp>/baseline`）。

历史结果：工具层/统一注册中心改动、11-26 稳定性修复、11-27 结果复用改动均得到 37/37 相同答案（差异 = 0）。任何意外差异都按回归处理，不要当成"模型措辞略有不同"。

## Verifier semantics (when adding cases) / 验证器语义（新增用例时）

<!-- EN -->
From `evals/verifiers.py`:

- `keywords`: every string in `expect_keywords` must appear in the answer.
- `refusal`: the answer must hit one of `refusal_signals`, contain `expect_keywords`, and **must not** contain any `forbid_substrings` — a single forbidden substring is an automatic fail (one-vote veto).
- `contains_not`: passes when the forbidden string is absent.
- Unknown verifier types fail closed (conservative).

For a scope-error fix, add the case to `set=boundary`, use `verifier=refusal`, list the exact wrong numbers in `forbid_substrings` (e.g. the all-database total `1,464,090`), and mirror it in `evals/prefix_cases.jsonl` so it also holds after multi-turn context. Action classification in prefix replay must read `evidence` first, falling back to answer text only when evidence is empty.

<!-- 中文 -->
来自 `evals/verifiers.py`：

- `keywords`：`expect_keywords` 中每个字符串都必须出现在答案里。
- `refusal`：答案必须命中 `refusal_signals` 之一、包含 `expect_keywords`，且**不得**包含任何 `forbid_substrings`——命中任一禁用子串即自动失败（一票否决）。
- `contains_not`：禁用字符串不存在即通过。
- 未知验证器类型一律保守判失败（fail closed）。

修复口径错误时，把用例加入 `set=boundary`，使用 `verifier=refusal`，在 `forbid_substrings` 中列出确切的错误数字（如全库总数 `1,464,090`），并在 `evals/prefix_cases.jsonl` 中镜像一条，确保多轮上下文后依然成立。前缀重放的动作分类必须先读 `evidence`，仅在证据为空时才回退到答案文本。

## Failure attribution / 失败归因

<!-- EN -->
When a case fails, use `evals/failure_attribution.py:attribute()` to find the **first** deviating step (six signatures: route/intent, scope, sql, execution, citation, self-check). Report the root cause and its consequence separately; do not report the last error as the cause. Read `## 失败归因（首个错误，非最后报错）` in `evals/report.md`.

<!-- 中文 -->
用例失败时，用 `evals/failure_attribution.py:attribute()` 找到**首个**偏离步骤（六类签名：路由/意图、口径、SQL、执行、引用、自检）。分别报告根因与其后果；不要把最后一个错误当成原因。参见 `evals/report.md` 中的 `## 失败归因（首个错误，非最后报错）`。

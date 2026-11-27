---
name: eval-regression
description: Use after any code change in crm-sales-agent, before claiming a fix/optimization works or before committing, to run the smallest sufficient verification and prove zero regression against a clean baseline. Covers the unittest suite, the four evaluation metrics with Pass^k, boundary/retention/prefix sets, keyless offline replay, and byte-for-byte answer snapshot comparison.
---

# Eval & zero-regression gate

Any behavior-affecting change must prove two things separately: **the boundary case got better** and **the retention set stayed byte-for-byte identical**. Running only one of them is not evidence. The evaluation is keyless and deterministic in offline mode, so a green result is reproducible.

## When to use

- After editing anything under `src/crm_agent/`, `api/`, or `evals/`.
- Before committing, pushing, or running `scripts/daily_update.py` (which enforces this gate itself).
- When fixing a wrong answer (e.g. a H07-style scope error) or adding a capability (e.g. composite orchestration).

## Step 1 — unit tests and compile

```sh
python -m compileall -q src api tests scripts
env -u OPENAI_API_KEY python -m unittest discover -s tests -v   # Unix; see AGENTS.md for Windows
```

55 tests across five files must pass. New behavior needs its own regression test file (current: `test_memory.py`, `test_eval_framework.py`, `test_enhancements.py`, `test_daily_optimizations.py`, `test_reuse_and_robustness.py`). A fix without a test that fails on the old code is not a fix.

## Step 2 — evaluation gate

```sh
env -u OPENAI_API_KEY python evals/run_eval.py --repeats 3
```

Required outcome, all at once:

- Intent accuracy / answer (keyword) hit rate / citation coverage / self-check rate = 100%.
- `overall_pass_rate.mean` = 1.0; `pass_pow_k` = 100% for the chosen `--repeats`.
- `boundary` set = 2/2 (A07 number-scope, H07 refusal), `retention` set = 35/35.
- Prefix regression = 4/4 (PFX-01..04); PFX-04 must still refuse a custom window after multi-turn memory inheritance.
- Exit code 0. `run_eval.py` returns 1 if any of boundary rate, retention rate, or prefix pass is below 1.0.

Metrics come from `evals/run_eval.py:aggregate()`: each has `mean/std/ci95/runs`; offline runs show CI=0 because execution is deterministic. Report latency as an order of magnitude (e.g. "tens of ms"), never a fixed number — it varies by machine and dependency version.

## Step 3 — byte-for-byte snapshot comparison

Tests and aggregate rates can hide a changed answer string. Compare the 37 answers against a clean baseline:

1. Export a pristine HEAD into a throwaway worktree:
   ```sh
   git worktree add -f <tmp>/baseline HEAD
   ```
2. Run the same dump script against both `baseline` and the working tree. The dump script must, for every case in `evals/cases.jsonl`, call `SalesAgent(use_llm=False).ask(question, advertiser_id, thread_id=eval-{id})` and record `answer / intent / advertiser_id / citations / review_ok`. Use a fresh thread id per case so history is empty and cross-turn contamination is impossible.
3. `diff` the two JSON outputs. Requirement: **zero differences** unless the change is intentionally a boundary fix, in which case only the named boundary case may differ, and its new answer must pass the `refusal` verifier.
4. Remove the worktree and all temp files afterward (`git worktree remove --force <tmp>/baseline`).

Historical results: the tool-layer/unified-registry change, the 11-26 stability fixes, and the 11-27 result-reuse change each produced 37/37 identical answers (diff = 0). Treat any unexpected diff as a regression, not as "the model said it slightly differently".

## Verifier semantics (when adding cases)

From `evals/verifiers.py`:

- `keywords`: every string in `expect_keywords` must appear in the answer.
- `refusal`: the answer must hit one of `refusal_signals`, contain `expect_keywords`, and **must not** contain any `forbid_substrings` — a single forbidden substring is an automatic fail (one-vote veto).
- `contains_not`: passes when the forbidden string is absent.
- Unknown verifier types fail closed (conservative).

For a scope-error fix, add the case to `set=boundary`, use `verifier=refusal`, list the exact wrong numbers in `forbid_substrings` (e.g. the all-database total `1,464,090`), and mirror it in `evals/prefix_cases.jsonl` so it also holds after multi-turn context. Action classification in prefix replay must read `evidence` first, falling back to answer text only when evidence is empty.

## Failure attribution

When a case fails, use `evals/failure_attribution.py:attribute()` to find the **first** deviating step (six signatures: route/intent, scope, sql, execution, citation, self-check). Report the root cause and its consequence separately; do not report the last error as the cause. Read `## 失败归因（首个错误，非最后报错）` in `evals/report.md`.

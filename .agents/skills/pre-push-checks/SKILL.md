---
name: pre-push-checks
description: Use before committing or pushing crm-sales-agent to GitHub main and Gitee master, and after any daily-update failure, to run the smallest sufficient checks, verify secret-free staged content, push both remotes with the correct SSH config, and confirm remote hashes match local HEAD. Covers the three-layer idempotence of the daily task and its exit codes. | 在提交或推送 crm-sales-agent 到 GitHub main 与 Gitee master 之前、以及每日更新任务失败之后使用，执行最小充分检查、确认暂存区无密钥、按正确 SSH 配置推送两端并核验远端哈希与本地 HEAD 一致。覆盖每日任务的三层幂等与退出码。
---

# Pre-push checks & two-remote sync / 提交前检查与双端同步

<!-- EN -->
Git hooks here are intentionally narrow, and CI is not a substitute for local evidence on the surfaces you touched. Run the relevant checks **once**, commit, push both remotes, then verify the remote refs. See design-notes §23–25 for the daily-task rationale.

<!-- 中文 -->
这里的 Git hooks 刻意保持轻量，CI 不能替代你在所改动面上的本地证据。相关检查**只跑一次**，然后提交、推送两端、核验远端引用。每日任务的设计理由见 design-notes §23–25。

> Both languages are authoritative; English comes first (`<!-- EN -->`), Chinese follows (`<!-- 中文 -->`). / 两种语言同等有效；英文在前，中文在后。

## 1 — Inspect the change / 检查改动

```sh
git status --short --branch
git diff --stat
git log --oneline -5
```

<!-- EN -->
Identify touched surfaces and map them to checks (below). Do not default to "run everything" on an ordinary change, and do not skip a check the diff actually invalidates.

<!-- 中文 -->
识别改动涉及的面并映射到下面的检查。普通改动不要默认"全跑一遍"，也不要跳过 diff 实际使其失效的检查。

## 2 — Select relevant checks / 选择相关检查

<!-- EN -->
- **Any `src/crm_agent/` or `api/` change:** full [eval-regression](../eval-regression/SKILL.md) gate — 55 unit tests + eval exit 0 + byte-for-byte snapshot comparison. This is non-negotiable for behavior changes.
- **Tool/context/memory/SQL changes:** also read the matching skill (tool-layer / context-and-memory / sql-and-data) and add its targeted regression tests.
- **Docs only (`README.md`/`docs/`):** verify every command/default you wrote by running it; fix claims, not tests. Keep design-notes and Agent Notes current with any fact you changed.
- **Config/`config/*.json`/`.env.example`:** confirm range validation in `config.py` and that defaults in docs match.
- **No behavior change:** compile + unit tests are sufficient; still skip the full snapshot only when you can show answers cannot change.

<!-- 中文 -->
- **任何 `src/crm_agent/` 或 `api/` 改动：** 跑完整 [eval-regression](../eval-regression/SKILL.md) 门禁——55 项单元测试 + 评估退出码 0 + 逐字节快照比对。行为改动时此项不可商量。
- **工具/上下文/记忆/SQL 改动：** 还要读对应 skill（tool-layer / context-and-memory / sql-and-data）并补充其专项回归测试。
- **纯文档（`README.md`/`docs/`）：** 逐条运行你写下的命令/默认值来验证；修正陈述而不是改测试。事实变动时同步 design-notes 与 Agent Notes。
- **配置/`config/*.json`/`.env.example`：** 确认 `config.py` 中有范围校验且文档默认值一致。
- **无行为变化：** 编译 + 单元测试即可；只有在能证明答案不可能变化时才可以跳过完整快照。

## 3 — Secret scan and commit / 密钥扫描与提交

```sh
git add -A
git diff --cached | grep -nEi 'sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{16,}|https?://[^@ ]+:[^@ ]+@' || echo NO_SECRET
git commit -m "<type>: <中文概述>"
```

<!-- EN -->
Never commit `.env`, credentials, `data/*.db`, `data/knowledge/`, `evals/last_report.json`, `logs/`, or `.venv-verify/`. If the scan hits, unstage only this task's paths — do not clear a user's pre-staged work (`scripts/daily_update.py:304-310` shows the safe pattern).

<!-- 中文 -->
绝不提交 `.env`、凭据、`data/*.db`、`data/knowledge/`、`evals/last_report.json`、`logs/` 或 `.venv-verify/`。扫描命中时只取消暂存本任务的路径——不要清空用户预先 stage 的内容（安全写法见 `scripts/daily_update.py:304-310`）。

## 4 — Push to both remotes / 推送两端

<!-- EN -->
GitHub uses SSH because HTTPS goes through a proxy that returns 502. Gitee uses HTTPS. Both remotes must end at the same hash.

```sh
# GitHub (SSH)
export HOME=/c/ssh-home USERPROFILE=/c/ssh-home
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15 \
  -o UserKnownHostsFile=/c/ssh-home/.ssh/known_hosts \
  -o IdentityFile=/c/ssh-home/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
GIT_TERMINAL_PROMPT=0 git push github-ssh main:main

# Gitee (HTTPS)
GIT_TERMINAL_PROMPT=0 git push gitee main:master
```

Never use raw `--force`. If a remote moved, fetch and reconcile rather than overwriting.

<!-- 中文 -->
GitHub 走 SSH，因为 HTTPS 经过返回 502 的代理。Gitee 走 HTTPS。两端最终必须停在同一哈希。

```sh
# GitHub（SSH）
export HOME=/c/ssh-home USERPROFILE=/c/ssh-home
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=15 \
  -o UserKnownHostsFile=/c/ssh-home/.ssh/known_hosts \
  -o IdentityFile=/c/ssh-home/.ssh/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
GIT_TERMINAL_PROMPT=0 git push github-ssh main:main

# Gitee（HTTPS）
GIT_TERMINAL_PROMPT=0 git push gitee main:master
```

绝不使用裸 `--force`。若远端有前进，应 fetch 后协调，而不是覆盖。

## 5 — Verify remote refs / 核验远端引用

```sh
# with the SSH env above exported / 需先导出上面的 SSH 环境
git ls-remote github-ssh refs/heads/main
GIT_TERMINAL_PROMPT=0 git ls-remote gitee refs/heads/master
git rev-parse HEAD
```

<!-- EN -->
All three must match. Also confirm `git remote -v` shows no plaintext credentials. Report the short hash in the final answer.

<!-- 中文 -->
三者必须一致。同时确认 `git remote -v` 中没有明文凭据。最终答复中报告短哈希。

## Daily update task / 每日更新任务（`scripts/daily_update.py`）

<!-- EN -->
Stages: `verify → evaluate → commit → push → report`. It enforces this skill automatically: compile + tests, eval gate (blocks commit/push if pass rate is below threshold), secret scan, per-remote push, then logs.

Three layers of idempotence — keep all three:
1. **File lock** `data/daily_update.lock` (O_EXCL), stale after `lock_ttl_s=1800`.
2. **Same-day state** `data/daily_update_state.json` — a successful run skips the next same-day run; `--force` bypasses only this layer.
3. **Content dedup** — no scoped changes → commit skipped; remote already has HEAD → push skipped.

Manual triggers: `python scripts/daily_update.py [--force|--dry-run|--stages ... --paths ... --remotes ...]`, or `scripts\daily_update.cmd --force` on Windows. Logs: `logs/daily_update/YYYY-MM-DD.log`, `logs/daily_update/latest.json`, `logs/daily_update/runs/<run_id>.json` (all gitignored). Exit codes: `0` success/skip, `1` failure, `2` lock conflict. A failed run does not mark the day successful, so the next day still retries. `evals/report.md` is a protected path (latency numbers fluctuate) — never let the daily task commit it.

If `status=failed`, report the failing stage, error, and retry count honestly; fix the cause and rerun once with `--force` rather than claiming success.

<!-- 中文 -->
阶段：`verify → evaluate → commit → push → report`。它自动执行本 skill：编译 + 测试、评估门禁（通过率不足则阻断提交/推送）、密钥扫描、逐远端推送、写日志。

三层幂等——三层都要保留：
1. **文件锁** `data/daily_update.lock`（O_EXCL），`lock_ttl_s=1800` 后视为陈旧。
2. **当日状态** `data/daily_update_state.json`——成功运行后跳过当天后续运行；`--force` 只绕过这一层。
3. **内容去重**——范围内无改动则跳过提交；远端已含 HEAD 则跳过推送。

手动触发：`python scripts/daily_update.py [--force|--dry-run|--stages ... --paths ... --remotes ...]`，Windows 用 `scripts\daily_update.cmd --force`。日志：`logs/daily_update/YYYY-MM-DD.log`、`logs/daily_update/latest.json`、`logs/daily_update/runs/<run_id>.json`（均已 gitignore）。退出码：`0` 成功/跳过、`1` 失败、`2` 锁冲突。失败运行不会标记当日成功，次日仍会重试。`evals/report.md` 是保护路径（延迟数字波动）——绝不让每日任务提交它。

若 `status=failed`，如实报告失败阶段、错误与重试次数；修复原因后用 `--force` 重跑一次，而不是声称成功。

---
name: pre-push-checks
description: Use before committing or pushing crm-sales-agent to GitHub main and Gitee master, and after any daily-update failure, to run the smallest sufficient checks, verify secret-free staged content, push both remotes with the correct SSH config, and confirm remote hashes match local HEAD. Covers the three-layer idempotence of the daily task and its exit codes.
---

# Pre-push checks & two-remote sync

Git hooks here are intentionally narrow, and CI is not a substitute for local evidence on the surfaces you touched. Run the relevant checks **once**, commit, push both remotes, then verify the remote refs. See design-notes §23–25 for the daily-task rationale.

## 1 — Inspect the change

```sh
git status --short --branch
git diff --stat
git log --oneline -5
```

Identify touched surfaces and map them to checks (below). Do not default to "run everything" on an ordinary change, and do not skip a check the diff actually invalidates.

## 2 — Select relevant checks

- **Any `src/crm_agent/` or `api/` change:** full [eval-regression](../eval-regression/SKILL.md) gate — 55 unit tests + eval exit 0 + byte-for-byte snapshot comparison. This is non-negotiable for behavior changes.
- **Tool/context/memory/SQL changes:** also read the matching skill (tool-layer / context-and-memory / sql-and-data) and add its targeted regression tests.
- **Docs only (`README.md`/`docs/`):** verify every command/default you wrote by running it; fix claims, not tests. Keep design-notes and Agent Notes current with any fact you changed.
- **Config/`config/*.json`/`.env.example`:** confirm range validation in `config.py` and that defaults in docs match.
- **No behavior change:** compile + unit tests are sufficient; still skip the full snapshot only when you can show answers cannot change.

## 3 — Secret scan and commit

```sh
git add -A
git diff --cached | grep -nEi 'sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._-]{16,}|https?://[^@ ]+:[^@ ]+@' || echo NO_SECRET
git commit -m "<type>: <中文概述>"
```

Never commit `.env`, credentials, `data/*.db`, `data/knowledge/`, `evals/last_report.json`, `logs/`, or `.venv-verify/`. If the scan hits, unstage only this task's paths — do not clear a user's pre-staged work (`scripts/daily_update.py:304-310` shows the safe pattern).

## 4 — Push to both remotes

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

## 5 — Verify remote refs

```sh
# with the SSH env above exported
git ls-remote github-ssh refs/heads/main
GIT_TERMINAL_PROMPT=0 git ls-remote gitee refs/heads/master
git rev-parse HEAD
```

All three must match. Also confirm `git remote -v` shows no plaintext credentials. Report the short hash in the final answer.

## Daily update task (`scripts/daily_update.py`)

Stages: `verify → evaluate → commit → push → report`. It enforces this skill automatically: compile + tests, eval gate (blocks commit/push if pass rate is below threshold), secret scan, per-remote push, then logs.

Three layers of idempotence — keep all three:
1. **File lock** `data/daily_update.lock` (O_EXCL), stale after `lock_ttl_s=1800`.
2. **Same-day state** `data/daily_update_state.json` — a successful run skips the next same-day run; `--force` bypasses only this layer.
3. **Content dedup** — no scoped changes → commit skipped; remote already has HEAD → push skipped.

Manual triggers: `python scripts/daily_update.py [--force|--dry-run|--stages ... --paths ... --remotes ...]`, or `scripts\daily_update.cmd --force` on Windows. Logs: `logs/daily_update/YYYY-MM-DD.log`, `logs/daily_update/latest.json`, `logs/daily_update/runs/<run_id>.json` (all gitignored). Exit codes: `0` success/skip, `1` failure, `2` lock conflict. A failed run does not mark the day successful, so the next day still retries. `evals/report.md` is a protected path (latency numbers fluctuate) — never let the daily task commit it.

If `status=failed`, report the failing stage, error, and retry count honestly; fix the cause and rerun once with `--force` rather than claiming success.

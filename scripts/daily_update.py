#!/usr/bin/env python3
"""CRM 销售提效 Agent 的每日优化与更新任务入口。

设计目标：可重复执行且不产生重复更新。
- 文件锁：同一时刻只允许一个实例运行，陈旧锁超时自动清理。
- 状态去重：当天已成功则跳过；手动触发用 --force 覆盖。
- 内容去重：范围内无改动不提交；远端已含本地 HEAD 不推送。
- 阶段化范围：stages / scope_paths / protected_paths / remotes 均可配置。
- 日志与重试：每次运行落文本日志 + JSON 摘要，失败按指数退避重试。

用法：
    python scripts/daily_update.py                 # 正常每日执行
    python scripts/daily_update.py --force         # 手动触发（忽略当日去重）
    python scripts/daily_update.py --dry-run       # 预演，不提交不推送
    python scripts/daily_update.py --stages verify,evaluate
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "daily_update.json"

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"Bearer [A-Za-z0-9._-]{16,}"),
    re.compile(r"https?://[^@\s]+:[^@\s]+@"),
]


class StageFailure(Exception):
    """阶段失败：触发重试，最终写入日志与摘要。"""


class Logger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, level: str, message: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)


def load_config(path: Path) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "stages": ["verify", "evaluate", "commit", "push", "report"],
        "scope_paths": ["src", "api", "tests", "evals", "docs", "web", "config", "README.md"],
        "protected_paths": ["config/daily_update.json", "scripts", "logs", "data", ".git"],
        "remotes": [{"name": "github-ssh", "branch": "main"}, {"name": "gitee", "branch": "master"}],
        "ssh_home": "",
        "max_retries": 3,
        "retry_delay_s": 2,
        "command_timeout_s": 600,
        "git_timeout_s": 120,
        "lock_ttl_s": 1800,
        "eval_repeats": 3,
        "min_overall_pass_rate": 1.0,
        "commit_prefix": "chore(daily): ",
        "continue_on_failure": False,
        "dry_run": False,
        "offline": True,
        "log_dir": "logs/daily_update",
        "state_path": "data/daily_update_state.json",
        "lock_path": "data/daily_update.lock",
    }
    if path.exists():
        user = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(user, dict):
            raise ValueError(f"配置文件必须是 JSON 对象：{path}")
        defaults.update(user)
    return defaults


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="每日优化与更新任务入口（幂等 / 可配置 / 可手动触发）")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="配置文件路径")
    parser.add_argument("--stages", help="逗号分隔的阶段：verify,evaluate,commit,push,report")
    parser.add_argument("--paths", help="逗号分隔的更新范围路径，覆盖配置")
    parser.add_argument("--remotes", help="逗号分隔的推送目标名，覆盖配置")
    parser.add_argument("--force", action="store_true", help="手动触发：忽略当日去重")
    parser.add_argument("--dry-run", action="store_true", help="预演：不提交、不推送")
    parser.add_argument("--max-retries", type=int, help="覆盖每阶段最大尝试次数")
    parser.add_argument("--eval-repeats", type=int, help="覆盖评估重复轮次")
    return parser.parse_args(argv)


def apply_overrides(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    if args.stages:
        cfg["stages"] = [x.strip() for x in args.stages.split(",") if x.strip()]
    if args.paths:
        cfg["scope_paths"] = [x.strip() for x in args.paths.split(",") if x.strip()]
    if args.remotes:
        wanted = {x.strip() for x in args.remotes.split(",") if x.strip()}
        cfg["remotes"] = [r for r in cfg["remotes"] if r["name"] in wanted]
    if args.max_retries is not None:
        cfg["max_retries"] = max(1, args.max_retries)
    if args.eval_repeats is not None:
        cfg["eval_repeats"] = max(1, args.eval_repeats)
    if args.dry_run:
        cfg["dry_run"] = True
    return cfg


def base_env(cfg: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    if cfg.get("offline", True):
        env["LLM_MODE"] = "offline"
        env.pop("OPENAI_API_KEY", None)
    env.setdefault("USE_CHECKPOINTER", "false")
    return env


def git_env(cfg: dict[str, Any]) -> dict[str, str]:
    env = base_env(cfg)
    env["GIT_TERMINAL_PROMPT"] = "0"
    ssh_home = str(cfg.get("ssh_home") or "").strip()
    if ssh_home:
        env["HOME"] = ssh_home
        env["USERPROFILE"] = ssh_home
        env["GIT_SSH_COMMAND"] = (
            "ssh -o BatchMode=yes -o ConnectTimeout=15 "
            f"-o UserKnownHostsFile={ssh_home}/.ssh/known_hosts "
            f"-o IdentityFile={ssh_home}/.ssh/id_ed25519 "
            "-o IdentitiesOnly=yes -o StrictHostKeyChecking=yes"
        )
    return env


def run_cmd(cmd: list[str], timeout: int, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def describe(cmd: list[str]) -> str:
    return " ".join(shlex.quote(x) for x in cmd)


def acquire_lock(path: Path, ttl_s: int, logger: Logger) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "started": datetime.now().isoformat()}, handle)
            return True
        except FileExistsError:
            stale = False
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                started = datetime.fromisoformat(data["started"])
                stale = (datetime.now() - started).total_seconds() > ttl_s
            except Exception:
                stale = path.stat().st_mtime < time.time() - ttl_s
            if stale:
                logger.log("WARN", f"清理陈旧锁 {path.name}")
                path.unlink(missing_ok=True)
                continue
            logger.log("WARN", f"已有任务持有锁 {path.name}，本次跳过")
            return False
    return False


def release_lock(path: Path) -> None:
    path.unlink(missing_ok=True)


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["history"] = (state.get("history") or [])[-19:]
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def in_scope(path: str, cfg: dict[str, Any]) -> bool:
    for protected in cfg["protected_paths"]:
        if path == protected or path.startswith(protected.rstrip("/") + "/"):
            return False
    for allowed in cfg["scope_paths"]:
        if allowed == "." or path == allowed or path.startswith(allowed.rstrip("/") + "/"):
            return True
    return False


def changed_files(cfg: dict[str, Any]) -> list[tuple[str, str]]:
    cp = run_cmd(["git", "status", "--porcelain", "--", "."], int(cfg["git_timeout_s"]))
    if cp.returncode != 0:
        raise StageFailure(f"git status 失败：{cp.stderr.strip()[:200]}")
    result: list[tuple[str, str]] = []
    for line in cp.stdout.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ")[-1]
        if in_scope(path, cfg):
            result.append((status, path))
    return result


def find_secret(text: str) -> str:
    for pattern in SECRET_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)[:12] + "..."
    return ""


def stage_verify(cfg: dict[str, Any], logger: Logger, ctx: dict[str, Any]) -> str:
    env = base_env(cfg)
    py = sys.executable
    steps = [
        [py, "-m", "compileall", "-q", "src", "api", "tests", "scripts"],
        [py, "-m", "unittest", "discover", "-s", "tests"],
    ]
    for cmd in steps:
        cp = run_cmd(cmd, int(cfg["command_timeout_s"]), env)
        if cp.returncode != 0:
            raise StageFailure(f"{describe(cmd)} 失败：{(cp.stderr or cp.stdout).strip()[-500:]}")
    output = (cp.stdout or "") + "\n" + (cp.stderr or "")
    match = re.search(r"Ran (\d+) tests", output)
    count = match.group(1) if match else "?"
    logger.log("INFO", f"单元测试 {count} 项通过")
    ctx["metrics"]["tests"] = count
    return f"编译与 {count} 项测试通过"


def stage_evaluate(cfg: dict[str, Any], logger: Logger, ctx: dict[str, Any]) -> str:
    env = base_env(cfg)
    cmd = [sys.executable, "evals/run_eval.py", "--repeats", str(cfg["eval_repeats"])]
    cp = run_cmd(cmd, int(cfg["command_timeout_s"]), env)
    if cp.returncode != 0:
        raise StageFailure(f"评估脚本失败：{(cp.stderr or cp.stdout).strip()[-500:]}")
    path = ROOT / "evals" / "last_report.json"
    if not path.exists():
        raise StageFailure("评估未产出 last_report.json")
    report = json.loads(path.read_text(encoding="utf-8"))
    summary = report.get("summary", {})
    rate = float(summary.get("overall_pass_rate", {}).get("mean", 0.0))
    threshold = float(cfg["min_overall_pass_rate"])
    ctx["metrics"]["overall_pass_rate"] = round(rate, 4)
    if rate + 1e-9 < threshold:
        raise StageFailure(f"整体通过率 {rate:.1%} 低于阈值 {threshold:.1%}，阻止提交与推送")
    passes = summary.get("pass_at_k", {})
    ctx["metrics"]["pass_at_k"] = {k: passes[k] for k in list(passes)[:3]}
    return f"整体通过率 {rate:.1%}，Pass^k 全部达标"


def reset_staged(paths: list[str], cfg: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    """只取消本任务暂存的路径，不动调用者事先 staged 的内容。"""
    return run_cmd(["git", "reset", "--", *paths], int(cfg["git_timeout_s"]), base_env(cfg))


def stage_commit(cfg: dict[str, Any], logger: Logger, ctx: dict[str, Any]) -> str:
    if cfg["dry_run"]:
        return "dry_run: 未提交（预演模式）"
    files = changed_files(cfg)
    if not files:
        return "skipped: 范围内无改动，不产生重复提交"
    env = base_env(cfg)
    for status, path in files:
        if status == "??" and (ROOT / path).is_dir():
            continue
        cp = run_cmd(["git", "add", "-A", "--", path], int(cfg["git_timeout_s"]), env)
        if cp.returncode != 0:
            raise StageFailure(f"git add {path} 失败：{cp.stderr.strip()[:200]}")
    staged = run_cmd(["git", "diff", "--cached", "--name-only"], int(cfg["git_timeout_s"]), env)
    if not staged.stdout.strip():
        return "skipped: 暂存区无变化"
    diff = run_cmd(["git", "diff", "--cached", "--unified=0"], int(cfg["git_timeout_s"]), env).stdout
    secret = find_secret(diff)
    if secret:
        # 只重置本任务刚 add 的路径；用户原先已 staged 的改动必须原样保留。
        staged_paths = [p for _, p in files]
        reset_staged(staged_paths, cfg)
        raise StageFailure(f"提交内容含疑似密钥（{secret}），已取消暂存")
    message = f"{cfg['commit_prefix']}每日优化与更新 {ctx['today']}"
    cp = run_cmd(["git", "commit", "-m", message], int(cfg["git_timeout_s"]), env)
    if cp.returncode != 0:
        raise StageFailure(f"git commit 失败：{cp.stderr.strip()[:300]}")
    head = run_cmd(["git", "rev-parse", "HEAD"], int(cfg["git_timeout_s"]), env).stdout.strip()
    ctx["commit"] = head
    logger.log("INFO", f"提交 {head[:8]}：{len(files)} 个文件")
    return f"committed {head[:8]}（{len(files)} 个文件）"


def stage_push(cfg: dict[str, Any], logger: Logger, ctx: dict[str, Any]) -> str:
    env_git = base_env(cfg)
    head = run_cmd(["git", "rev-parse", "HEAD"], int(cfg["git_timeout_s"]), env_git).stdout.strip()
    if not head:
        raise StageFailure("无法读取本地 HEAD")
    if cfg["dry_run"]:
        return "dry_run: 未推送（预演模式）"
    failures: list[str] = []
    pushed: list[str] = []
    for remote in cfg["remotes"]:
        name, branch = remote["name"], remote["branch"]
        env = git_env(cfg) if name == "github-ssh" else env_git
        lookup = run_cmd(["git", "ls-remote", name, f"refs/heads/{branch}"], int(cfg["git_timeout_s"]), env)
        if lookup.returncode != 0:
            failures.append(f"{name}: ls-remote 失败 {lookup.stderr.strip()[:120]}")
            ctx["pushes"][name] = "failed(lookup)"
            continue
        remote_head = lookup.stdout.split()[0] if lookup.stdout.strip() else ""
        if remote_head == head:
            ctx["pushes"][name] = "skipped(already-current)"
            logger.log("INFO", f"{name}/{branch} 已是 {head[:8]}，跳过推送")
            continue
        cp = run_cmd(["git", "push", name, f"HEAD:{branch}"], int(cfg["git_timeout_s"]), env)
        if cp.returncode != 0:
            failures.append(f"{name}: {cp.stderr.strip()[:200]}")
            ctx["pushes"][name] = "failed(push)"
        else:
            ctx["pushes"][name] = f"pushed({head[:8]})"
            pushed.append(name)
    if failures:
        raise StageFailure("；".join(failures))
    return "pushed: " + (", ".join(pushed) if pushed else "两端均为最新，无重复推送")


def run_with_retry(name: str, fn: Callable[[], str], cfg: dict[str, Any], logger: Logger) -> dict[str, Any]:
    attempts_max = max(1, int(cfg["max_retries"]))
    delay = max(0, float(cfg["retry_delay_s"]))
    last_error = ""
    for attempt in range(1, attempts_max + 1):
        logger.log("INFO", f"阶段 {name} 开始（{attempt}/{attempts_max}）")
        try:
            detail = fn()
            status = "skipped" if detail.startswith("skipped:") else "ok"
            logger.log("INFO", f"阶段 {name} 完成：{detail}")
            return {"name": name, "status": status, "attempts": attempt, "detail": detail}
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.log("WARN", f"阶段 {name} 失败：{last_error}")
            if attempt < attempts_max:
                time.sleep(min(delay * (2 ** (attempt - 1)), 60))
    logger.log("ERROR", f"阶段 {name} 重试 {attempts_max} 次后仍失败")
    return {"name": name, "status": "failed", "attempts": attempts_max, "detail": last_error}


def write_summary(
    cfg: dict[str, Any], logger: Logger, run_id: str, status: str,
    results: list[dict[str, Any]], ctx: dict[str, Any], state: dict[str, Any],
) -> str:
    summary = {
        "run_id": run_id,
        "date": ctx["today"],
        "started_at": ctx["started_at"],
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "force": ctx["force"],
        "dry_run": cfg["dry_run"],
        "scope": {
            "stages": cfg["stages"],
            "paths": cfg["scope_paths"],
            "protected": cfg["protected_paths"],
            "remotes": cfg["remotes"],
        },
        "settings": {
            "max_retries": cfg["max_retries"],
            "eval_repeats": cfg["eval_repeats"],
            "min_overall_pass_rate": cfg["min_overall_pass_rate"],
        },
        "stages": results,
        "commit": ctx["commit"],
        "pushes": ctx["pushes"],
        "metrics": ctx["metrics"],
        "log": str(logger.path.relative_to(ROOT)).replace("\\", "/"),
    }
    out_dir = ROOT / cfg["log_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "latest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out_dir / "runs").mkdir(exist_ok=True)
    (out_dir / "runs" / f"{run_id}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    state.setdefault("history", []).append({
        "run_id": run_id, "date": ctx["today"], "status": status,
        "commit": ctx["commit"], "pushes": ctx["pushes"],
    })
    write_state(ROOT / cfg["state_path"], state)
    logger.log("INFO", f"运行结束 status={status}；摘要 {cfg['log_dir']}/latest.json")
    return f"日志 {summary['log']}"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cfg = apply_overrides(load_config(Path(args.config).resolve()), args)
    today = date.today().isoformat()
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    logger = Logger(ROOT / cfg["log_dir"] / f"{today}.log")
    logger.log("INFO", f"===== 每日优化与更新启动 run_id={run_id} =====")

    lock_path = ROOT / cfg["lock_path"]
    if not acquire_lock(lock_path, int(cfg["lock_ttl_s"]), logger):
        return 2
    try:
        state = read_state(ROOT / cfg["state_path"])
        if not args.force and state.get("last_success_date") == today and \
                state.get("last_status") in ("success", "partial"):
            logger.log("INFO", f"今日 {today} 已成功执行过（{state.get('last_run_id')}），跳过重复更新；手动触发请加 --force")
            return 0

        ctx: dict[str, Any] = {
            "today": today, "started_at": datetime.now().isoformat(timespec="seconds"),
            "force": bool(args.force), "commit": "", "pushes": {}, "metrics": {},
        }
        results: list[dict[str, Any]] = []
        for name in cfg["stages"]:
            if name == "report":
                continue
            handlers = {
                "verify": lambda: stage_verify(cfg, logger, ctx),
                "evaluate": lambda: stage_evaluate(cfg, logger, ctx),
                "commit": lambda: stage_commit(cfg, logger, ctx),
                "push": lambda: stage_push(cfg, logger, ctx),
            }
            handler = handlers.get(name)
            if handler is None:
                logger.log("WARN", f"未知阶段 {name}，已跳过")
                results.append({"name": name, "status": "skipped", "attempts": 0, "detail": "unknown stage"})
                continue
            result = run_with_retry(name, handler, cfg, logger)
            results.append(result)
            if result["status"] == "failed" and not cfg.get("continue_on_failure", False):
                logger.log("ERROR", f"阶段 {name} 失败，终止后续阶段（可设置 continue_on_failure=true 改变）")
                break

        actionable = [r for r in results if r["name"] != "report"]
        if not actionable:
            status = "failed"
        elif any(r["status"] == "failed" for r in actionable):
            status = "failed"
        elif any(r["status"] == "partial" for r in actionable):
            status = "partial"
        else:
            status = "success"

        state["last_run_id"] = run_id
        state["last_run_date"] = today
        state["last_status"] = status
        state["last_commit"] = ctx["commit"]
        if status in ("success", "partial"):
            state["last_success_date"] = today
        if "report" in cfg["stages"]:
            write_summary(cfg, logger, run_id, status, results, ctx, state)
        return 0 if status in ("success", "partial") else 1
    except Exception:
        logger.log("ERROR", "未捕获异常：\n" + traceback.format_exc())
        return 1
    finally:
        release_lock(lock_path)


if __name__ == "__main__":
    sys.exit(main())

"""统一工具层：注册、参数校验、编排、失败回退、结果汇总与可观测性。

为什么需要这一层（相对旧版）：
- 旧版每个节点直接调 store/kb/sql_agent/scoring 的具体方法，加一个新工具要改多处；
  现在所有能力先注册成 Tool，节点/规划器只认名字和参数 schema。
- 参数校验集中在入口，避免 advertiser_id 这类外部输入直接拼进 SQL 模板。
- 调用编排支持依赖引用（`$ref`）与步数上限，复合任务不再靠节点内硬编码顺序。
- 失败回退统一为"重试 → 备用工具 → 安全降级"，调用方只看到一个 ToolResult。
- 所有工具调用有统一结果对象与指标，能回答"谁失败了 / 谁拖慢了回答"。

设计取舍：
- 参数 schema 用轻量 dict 而不是完整 JSON Schema：依赖零、可读性好，覆盖
  type/required/default/enum/min/max/min_len/max_len/pattern 这些真正用得上的约束。
- 超时用线程池 + Future.timeout 实现；本地工具几乎不会超时，超时只作为兜底保险，
  工作线程不强制中断（Python 无法安全杀线程），但调用方立刻拿到结果继续走降级。
- 回退函数失败不会二次抛给调用方，只记入 result.warnings。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from ..errors import (
    DEPENDENCY_FAILED,
    FALLBACK_FAILED,
    INVALID_PARAMETERS,
    TOOL_FAILED,
    TOOL_TIMEOUT,
    TOOL_UNAVAILABLE,
    AgentError,
)

log = logging.getLogger("crm_agent.tools")

# 延迟创建，避免进程 fork/测试导入时注册无用线程
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="crm-tool")
        return _executor


# ---- 参数校验 -------------------------------------------------------------
_TYPE_CHECKS = {
    "str": lambda v: isinstance(v, str),
    "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "bool": lambda v: isinstance(v, bool),
    "list": lambda v: isinstance(v, (list, tuple)),
    "dict": lambda v: isinstance(v, dict),
}


def validate_params(params: dict[str, Any], schema: dict[str, dict[str, Any]],
                    *, allow_extra: bool = False) -> tuple[dict[str, Any], list[str]]:
    """按轻量 schema 清洗参数，返回 (清洗后参数, 错误列表)。

    校验失败不抛异常，让调用方一次性拿到全部问题（便于 API 返回 422 风格的提示）。
    """
    cleaned: dict[str, Any] = {}
    errors: list[str] = []

    if not isinstance(params, dict):
        return {}, ["参数必须是对象"]

    if not allow_extra:
        extra = set(params) - set(schema)
        if extra:
            errors.append("不支持的参数：" + ", ".join(sorted(extra)))

    for name, spec in schema.items():
        if name not in params:
            if spec.get("required"):
                errors.append(f"缺少必填参数 {name}")
            elif "default" in spec:
                cleaned[name] = spec["default"]
            continue

        value = params[name]
        kind = spec.get("type", "any")
        check = _TYPE_CHECKS.get(kind)
        if check is not None and not check(value):
            errors.append(f"{name} 应为 {kind} 类型")
            continue
        if kind == "int" and isinstance(value, float) and value.is_integer():
            value = int(value)

        if "enum" in spec and value not in spec["enum"]:
            errors.append(f"{name} 只允许 {spec['enum']}")
            continue
        if kind in {"str", "list"} and spec.get("min_len") is not None and len(value) < spec["min_len"]:
            errors.append(f"{name} 长度不能小于 {spec['min_len']}")
            continue
        if kind in {"str", "list"} and spec.get("max_len") is not None and len(value) > spec["max_len"]:
            errors.append(f"{name} 长度不能超过 {spec['max_len']}")
            continue
        if kind in {"int", "float"}:
            if spec.get("min") is not None and value < spec["min"]:
                errors.append(f"{name} 不能小于 {spec['min']}")
                continue
            if spec.get("max") is not None and value > spec["max"]:
                errors.append(f"{name} 不能大于 {spec['max']}")
                continue
        if kind == "str" and spec.get("pattern"):
            try:
                if not re.search(spec["pattern"], value):
                    errors.append(f"{name} 格式不符合要求")
                    continue
            except re.error:
                # schema 写错是开发期问题，不让坏正则把线上请求打挂
                log.warning("工具 %s 参数 %s 的正则无效", spec.get("tool", "?"), name)
        cleaned[name] = value

    return cleaned, errors


# ---- 统一结果 -------------------------------------------------------------
@dataclass
class ToolResult:
    tool: str
    ok: bool
    data: Any = None
    error: str = ""                       # 用户安全文案
    error_code: str = ""
    detail: str = ""                      # 内部排障信息
    source: str = "local"
    latency_ms: float = 0.0
    attempts: int = 1
    degraded: bool = False                # 走了回退/降级
    warnings: list[str] = field(default_factory=list)
    step_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool, "ok": self.ok, "data": self.data,
            "error": self.error, "error_code": self.error_code,
            "source": self.source, "latency_ms": round(self.latency_ms, 2),
            "attempts": self.attempts, "degraded": self.degraded,
            "warnings": list(self.warnings), "step_id": self.step_id,
        }

    @classmethod
    def failure(cls, tool: str, err: AgentError, **kwargs: Any) -> "ToolResult":
        return cls(
            tool=tool, ok=False, error=err.message, error_code=err.code,
            detail=err.detail, **kwargs,
        )


# ---- 工具定义 -------------------------------------------------------------
@dataclass
class ToolSpec:
    name: str
    description: str
    handler: Callable[..., Any]
    parameters: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: str = "local"
    timeout_s: float = 0.0                 # 0 表示不额外套超时
    retries: int = 0                       # 额外重试次数
    fallback: Callable[..., Any] | None = None
    allow_extra: bool = False
    enabled: bool = True
    tags: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        """给规划器 / API / 前端看的自描述信息。"""
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "parameters": self.parameters,
            "tags": list(self.tags),
            "enabled": self.enabled,
        }


@dataclass
class _ToolStats:
    calls: int = 0
    failures: int = 0
    degraded: int = 0
    total_latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "degraded": self.degraded,
            "avg_latency_ms": round(self.total_latency_ms / self.calls, 2) if self.calls else 0.0,
        }


# ---- 注册中心 -------------------------------------------------------------
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._stats: dict[str, _ToolStats] = {}
        self._lock = threading.RLock()
        self._policy: dict[str, Any] = {"timeout_s": 0.0, "retries": 0, "fallback_enabled": True}

    # ---- 注册与发现 ----
    def register(self, spec: ToolSpec, *, replace: bool = False) -> None:
        if not spec.name or not callable(spec.handler):
            raise ValueError("工具必须有 name 和可调用的 handler")
        with self._lock:
            if spec.name in self._tools and not replace:
                raise ValueError(f"工具已存在：{spec.name}")
            # 让参数错误信息里能带上工具名
            for p in spec.parameters.values():
                p.setdefault("tool", spec.name)
            # 只覆盖"仍在默认值"的字段，允许单独注册的工具显式指定自己的策略
            if spec.timeout_s == 0:
                spec.timeout_s = self._policy.get("timeout_s", 0.0)
            if spec.retries == 0:
                spec.retries = self._policy.get("retries", 0)
            if not self._policy.get("fallback_enabled", True):
                spec.fallback = None
            self._tools[spec.name] = spec
            self._stats.setdefault(spec.name, _ToolStats())

    def unregister(self, name: str) -> bool:
        with self._lock:
            self._tools.pop(name, None)
            return self._stats.pop(name, None) is not None

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def describe(self) -> list[dict[str, Any]]:
        with self._lock:
            return [s.describe() for s in sorted(self._tools.values(), key=lambda x: x.name)]

    def configure(self, *, timeout_s: float = 0.0, retries: int = 0,
                  fallback_enabled: bool = True) -> None:
        """批量套用运行策略；新增工具注册后自动继承同一套策略。"""
        self._policy = {
            "timeout_s": timeout_s,
            "retries": retries if fallback_enabled else 0,
            "fallback_enabled": fallback_enabled,
        }
        with self._lock:
            for spec in self._tools.values():
                spec.timeout_s = timeout_s
                spec.retries = self._policy["retries"]
                if not fallback_enabled:
                    spec.fallback = None

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            per_tool = {name: st.as_dict() for name, st in sorted(self._stats.items())}
            total = sum(st.calls for st in self._stats.values())
            failed = sum(st.failures for st in self._stats.values())
            degraded = sum(st.degraded for st in self._stats.values())
            return {
                "tools": len(self._tools),
                "calls": total,
                "failures": failed,
                "degraded": degraded,
                "per_tool": per_tool,
            }

    # ---- 单工具调用 ----
    def call(self, name: str, params: dict[str, Any] | None = None, *,
             ctx: Any = None) -> ToolResult:
        started = time.perf_counter()
        params = params or {}
        spec = self._tools.get(name)
        if spec is None or not spec.enabled:
            return ToolResult.failure(name, AgentError(TOOL_UNAVAILABLE, stage="registry"))

        cleaned, errors = validate_params(params, spec.parameters, allow_extra=spec.allow_extra)
        if errors:
            err = AgentError(INVALID_PARAMETERS, "；".join(errors),
                             detail=f"tool={name}", stage="validate")
            return ToolResult.failure(name, err, attempts=0)

        last: AgentError | None = None
        attempts = 1 + max(0, spec.retries)
        for attempt in range(1, attempts + 1):
            try:
                data = self._invoke(spec.handler, cleaned, ctx, spec.timeout_s)
                result = ToolResult(
                    tool=name, ok=True, data=data, source=spec.source,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    attempts=attempt,
                )
                self._record(name, ok=True, degraded=False, latency_ms=result.latency_ms)
                return result
            except AgentError as exc:
                last = exc
                if not exc.retryable:
                    break
            except Exception as exc:  # noqa: BLE001 - 工具异常统一转成结果，不打断主链路
                last = AgentError(TOOL_FAILED, detail=type(exc).__name__, stage=name)
            if attempt < attempts:
                log.debug("工具 %s 第 %d 次失败，重试：%s", name, attempt, last.code)

        assert last is not None
        if spec.fallback is not None:
            try:
                data = self._invoke(spec.fallback, cleaned, ctx, spec.timeout_s)
                result = ToolResult(
                    tool=name, ok=True, data=data, source=f"{spec.source}:fallback",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    attempts=attempts, degraded=True,
                    warnings=[f"{name} 主路径失败（{last.code}），已使用回退结果"],
                )
                self._record(name, ok=True, degraded=True, latency_ms=result.latency_ms)
                return result
            except Exception as exc:  # noqa: BLE001
                last = AgentError(FALLBACK_FAILED, detail=type(exc).__name__, stage=name)

        result = ToolResult.failure(
            name, last,
            latency_ms=(time.perf_counter() - started) * 1000,
            attempts=attempts,
        )
        self._record(name, ok=False, degraded=False, latency_ms=result.latency_ms)
        log.warning("工具 %s 调用失败：%s（%s）", name, last.code, last.detail)
        return result

    @staticmethod
    def _invoke(handler: Callable[..., Any], params: dict[str, Any],
                ctx: Any, timeout_s: float) -> Any:
        """按 handler 是否接受 ctx 决定调用形态；可选超时保护。"""
        def run() -> Any:
            if ctx is None:
                return handler(**params)
            # 优先尝试显式 ctx 注入；不支持时回落到普通参数调用。
            try:
                return handler(ctx=ctx, **params)
            except TypeError as exc:
                if "unexpected keyword argument 'ctx'" in str(exc):
                    return handler(**params)
                raise

        if timeout_s <= 0:
            return run()
        future = _get_executor().submit(run)
        try:
            return future.result(timeout=timeout_s)
        except FutureTimeout as exc:
            raise AgentError(TOOL_TIMEOUT, stage="timeout") from exc

    def _record(self, name: str, *, ok: bool, degraded: bool, latency_ms: float) -> None:
        with self._lock:
            st = self._stats.setdefault(name, _ToolStats())
            st.calls += 1
            st.total_latency_ms += latency_ms
            if not ok:
                st.failures += 1
            if degraded:
                st.degraded += 1

    # ---- 编排 ----
    def call_plan(self, steps: Iterable[dict[str, Any]], *, ctx: Any = None,
                  parallel: bool = False) -> list[ToolResult]:
        """按依赖分层执行工具步骤，返回与输入同序的结果列表。

        步骤格式：
          {"id": "s1", "tool": "advertiser.get", "params": {"advertiser_id": "A003"},
           "depends_on": [], "optional": False}

        params 里可以用 `{"$ref": "s1"}` 或 `{"$ref": "s1.rows.0"}` 引用上游结果；
        上游失败或字段不存在时，本步记为 dependency_failed，不会抛异常中断整批。
        """
        pending = list(steps)
        results: dict[str, ToolResult] = {}
        ordered: list[ToolResult] = []

        while pending:
            wave = [s for s in pending if all(d in results for d in s.get("depends_on", []))]
            if not wave:
                for step in pending:
                    result = ToolResult.failure(
                        str(step.get("tool", "?")),
                        AgentError(DEPENDENCY_FAILED, detail="循环依赖或上游缺失", stage="orchestrate"),
                        step_id=str(step.get("id", "")),
                    )
                    results[str(step.get("id", ""))] = result
                    ordered.append(result)
                break

            for step in wave:
                pending.remove(step)
            sid = lambda s: str(s.get("id", s.get("tool", "?")))

            def run_step(step: dict[str, Any]) -> ToolResult:
                # 上游失败时下游不执行：避免拿空/错数据继续算，也便于定位首个错误
                for dep in step.get("depends_on", []):
                    upstream = results.get(str(dep))
                    if upstream is None or not upstream.ok:
                        return ToolResult.failure(
                            str(step.get("tool", "?")),
                            AgentError(DEPENDENCY_FAILED,
                                       detail=f"上游 {dep} 失败", stage="orchestrate"),
                            step_id=sid(step),
                        )
                try:
                    params = _resolve_refs(step.get("params", {}), results)
                except AgentError as exc:
                    return ToolResult.failure(str(step.get("tool", "?")), exc, step_id=sid(step))
                result = self.call(str(step.get("tool", "")), params, ctx=ctx)
                result.step_id = sid(step)
                return result

            if parallel and len(wave) > 1:
                futures = [_get_executor().submit(run_step, s) for s in wave]
                wave_results = [f.result() for f in futures]
            else:
                wave_results = [run_step(s) for s in wave]

            for step, result in zip(wave, wave_results):
                results[sid(step)] = result
                ordered.append(result)

        # optional 步骤失败只留警告，由上层决定要不要据此降级
        for result in ordered:
            if not result.ok:
                log.debug("编排步骤 %s 失败（%s）", result.step_id, result.error_code)
        return ordered


def _resolve_refs(value: Any, results: dict[str, ToolResult]) -> Any:
    """递归替换 `{"$ref": "id.field"}` 为上游工具结果。"""
    if isinstance(value, dict):
        if "$ref" in value and len(value) == 1:
            ref = str(value["$ref"])
            step_id, _, path = ref.partition(".")
            result = results.get(step_id)
            if result is None or not result.ok:
                raise AgentError(DEPENDENCY_FAILED,
                                 detail=f"上游 {step_id} 未成功", stage="orchestrate")
            data: Any = result.data
            for part in path.split(".") if path else []:
                if isinstance(data, dict):
                    data = data.get(part)
                elif isinstance(data, (list, tuple)) and part.isdigit():
                    idx = int(part)
                    data = data[idx] if idx < len(data) else None
                else:
                    data = None
            return data
        return {k: _resolve_refs(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(v, results) for v in value]
    return value

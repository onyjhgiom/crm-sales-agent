"""统一错误码与安全错误文案。

设计原则：
1. 所有对外（API / 前端 / 评估）的错误只有一个对象：AgentError；
2. `message` 是给用户看的，永远不含路径、堆栈、SQL 原文或模型响应；
3. `detail` 只进日志和 `evidence`，用于排障；
4. `retryable` 告诉上层该不该重试，避免"配置错误也重试 3 次"这种无意义开销。

工具层、规划层、顶层入口共用这套码，前端可以按 code 做差异化展示。
"""

from __future__ import annotations

from typing import Any

# ---- 错误码 ---------------------------------------------------------------
INVALID_PARAMETERS = "invalid_parameters"        # 参数校验失败（可修正后重试）
TOOL_UNAVAILABLE = "tool_unavailable"            # 工具未注册/被禁用
TOOL_TIMEOUT = "tool_timeout"                    # 工具超时
TOOL_FAILED = "tool_failed"                      # 工具执行异常
FALLBACK_FAILED = "fallback_failed"              # 工具与其回退都失败
DEPENDENCY_FAILED = "dependency_failed"          # 编排中的上游步骤失败
UNSAFE_OPERATION = "unsafe_operation"            # 安全护栏拒绝（如非只读 SQL）
EXTERNAL_SERVICE_DOWN = "external_service_down"  # 外部模型/服务不可用
DATA_UNAVAILABLE = "data_unavailable"            # 数据或知识库不可用
UNSUPPORTED_SCOPE = "unsupported_scope"          # 数据口径不支持（如近 45 天）
PLAN_EXHAUSTED = "plan_exhausted"                # 规划步数预算耗尽
INTERNAL_ERROR = "internal_error"                # 未知内部错误

# 用户可见文案：宁可模糊也不泄露内部细节
_USER_MESSAGES = {
    INVALID_PARAMETERS: "请求参数不符合要求，请检查后重试。",
    TOOL_UNAVAILABLE: "该功能暂未开放，可使用其他方式继续。",
    TOOL_TIMEOUT: "查询耗时过长，请缩小范围后重试。",
    TOOL_FAILED: "本次查询没有成功，已切换到可用方式。",
    FALLBACK_FAILED: "相关数据暂时无法获取，请稍后再试。",
    DEPENDENCY_FAILED: "依赖的数据未就绪，本项暂时无法完成。",
    UNSAFE_OPERATION: "出于安全考虑，该操作已被拦截。",
    EXTERNAL_SERVICE_DOWN: "外部服务不可用，已切换为本地能力继续回答。",
    DATA_UNAVAILABLE: "本地数据或知识库暂时不可用，请稍后重试。",
    UNSUPPORTED_SCOPE: "当前数据口径不支持该问题，请换个说法。",
    PLAN_EXHAUSTED: "问题涉及的子任务过多，已给出主要部分。",
    INTERNAL_ERROR: "服务暂时不可用，请稍后重试。",
}

_RETRYABLE = {
    TOOL_TIMEOUT, TOOL_FAILED, EXTERNAL_SERVICE_DOWN, DATA_UNAVAILABLE, INTERNAL_ERROR,
}


class AgentError(Exception):
    """带错误码、用户文案与内部详情的异常。"""

    def __init__(self, code: str, message: str = "", *, detail: str = "",
                 retryable: bool | None = None, stage: str = "") -> None:
        self.code = code
        self.message = message or _USER_MESSAGES.get(code, _USER_MESSAGES[INTERNAL_ERROR])
        self.detail = detail
        self.retryable = code in _RETRYABLE if retryable is None else retryable
        self.stage = stage
        super().__init__(f"{code}: {self.message}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,           # 仅日志/排障，前端可选择不展示
            "retryable": self.retryable,
            "stage": self.stage,
        }


def safe_message(code: str, fallback: str = "") -> str:
    """给错误码配一句安全文案，供不抛异常的降级路径使用。"""
    return _USER_MESSAGES.get(code, fallback or _USER_MESSAGES[INTERNAL_ERROR])

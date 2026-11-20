"""大模型调用封装（OpenAI 兼容协议）+ 熔断与可观测性。

设计取舍：
1. `chat()` 返回 None 表示"这次模型不可用"，由上层节点决定降级到规则模板，
   网络抖动不会让整条链崩掉，评估也能脱离模型波动复现；
2. 超时与重试参数化，连续失败触发熔断：冷却期内直接降级、不再发起网络调用。
   否则模型不可用时每个请求都要白等一个超时，会把 uvicorn 线程池拖垮；
3. 指标（调用数 / 失败数 / 降级数 / 累计耗时 / 熔断状态）暴露给 /api/health，
   日志统一走 logging，只记异常类型，绝不打印 api_key 或完整响应内容。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

from .config import Settings

log = logging.getLogger("crm_agent.llm")


class LLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        self._lock = threading.RLock()

        # 可观测性指标
        self.calls = 0
        self.failures = 0
        self.fallbacks = 0
        self.total_latency_ms = 0.0
        self.circuit_open = False

        self._consecutive_failures = 0
        self._opened_at = 0.0

        if settings.llm_enabled:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=settings.api_key,
                    base_url=settings.base_url,
                    timeout=settings.llm_timeout,
                    max_retries=settings.llm_max_retries,
                )
            except Exception as exc:  # noqa: BLE001 - 依赖缺失或配置错误都不该阻断主流程
                self._client = None
                log.warning("LLM 客户端初始化失败，降级为离线模式：%s", type(exc).__name__)
        elif settings.llm_mode == "llm" and not settings.api_key:
            log.error("LLM_MODE=llm 但缺少 OPENAI_API_KEY，已降级为离线模式")

    # ------------------------------------------------------------------
    def _is_circuit_open(self) -> bool:
        """冷却到期自动半开（重置计数），让服务能自愈。"""
        with self._lock:
            if not self._opened_at:
                return False
            if time.monotonic() - self._opened_at >= self.settings.llm_cb_cooldown:
                self._opened_at = 0.0
                self._consecutive_failures = 0
                self.circuit_open = False
                log.info("LLM 熔断冷却结束，恢复调用")
                return False
            return True

    @property
    def available(self) -> bool:
        # 熔断时 router / 各节点会直接走规则路径，不会白等一次网络调用
        return self._client is not None and not self._is_circuit_open()

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            opened = self._is_circuit_open()
            return {
                "calls": self.calls,
                "failures": self.failures,
                "fallbacks": self.fallbacks,
                "avg_latency_ms": round(self.total_latency_ms / self.calls, 1) if self.calls else 0.0,
                "circuit_open": opened,
                "consecutive_failures": self._consecutive_failures,
            }

    # ------------------------------------------------------------------
    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2,
             max_tokens: int | None = None) -> str | None:
        if not self._client:
            return None
        if self._is_circuit_open():
            with self._lock:
                self.fallbacks += 1
            return None

        with self._lock:
            self.calls += 1
        started = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(
                model=self.settings.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens or self.settings.llm_max_tokens,
            )
            text = (resp.choices[0].message.content or "").strip() or None
            with self._lock:
                self.total_latency_ms += (time.perf_counter() - started) * 1000
                self._consecutive_failures = 0
            return text
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.failures += 1
                self.fallbacks += 1
                self.total_latency_ms += (time.perf_counter() - started) * 1000
                self._consecutive_failures += 1
                should_open = (
                    self._consecutive_failures >= self.settings.llm_cb_threshold
                    and not self.circuit_open
                )
                if should_open:
                    self.circuit_open = True
                    self._opened_at = time.monotonic()
            log.warning("模型调用失败，本次降级为规则模板：%s", type(exc).__name__)
            if should_open:
                log.warning("LLM 连续失败 %d 次，熔断开启（冷却 %.0fs）",
                            self._consecutive_failures, self.settings.llm_cb_cooldown)
            return None

    def chat_json(self, messages: list[dict[str, str]], temperature: float = 0.0) -> dict[str, Any] | None:
        text = self.chat(messages, temperature=temperature, max_tokens=400)
        if not text:
            return None
        return extract_json(text)


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里抠出第一个 JSON 对象。

    模型经常在 JSON 前后加解释或 ```json 代码块，直接 json.loads 会失败，
    这里先按代码块、再按花括号配对兜底。
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        text = text[start : end + 1]
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def timed(fn, *args, **kwargs):
    """给节点加一个极简耗时统计，评估报告里展示端到端延迟。"""
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, round((time.perf_counter() - start) * 1000, 1)

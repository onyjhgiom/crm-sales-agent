"""大模型调用封装（OpenAI 兼容协议）。

几个刻意的设计取舍：
1. 只暴露 `chat()` / `chat_json()`，返回 None 表示"这次模型不可用"，
   由上层节点决定降级到规则模板。这样既不会因为网络抖动整条链崩掉，
   也保证没有 API Key 时项目完整可跑（评估数据才可复现）。
2. 不做重试与超时编排的花活，2 次重试 + 固定超时足够演示，
   真正的生产网关应该放在上游。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .config import Settings


class LLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._client = None
        self._warned = False
        self.calls = 0          # 统计调用次数，评估报告里会用到
        self.failures = 0

        if settings.llm_enabled:
            try:
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=settings.api_key,
                    base_url=settings.base_url,
                    timeout=30,
                    max_retries=2,
                )
            except Exception as exc:  # noqa: BLE001 - 依赖缺失或配置错误都不该阻断主流程
                self._client = None
                self._warn(f"LLM 客户端初始化失败，降级为离线模式：{exc}")

    @property
    def available(self) -> bool:
        return self._client is not None

    def _warn(self, msg: str) -> None:
        if not self._warned:
            print(f"[LLM] {msg}")
            self._warned = True

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2,
             max_tokens: int = 900) -> str | None:
        if not self._client:
            return None
        self.calls += 1
        try:
            resp = self._client.chat.completions.create(
                model=self.settings.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return (resp.choices[0].message.content or "").strip() or None
        except Exception as exc:  # noqa: BLE001
            self.failures += 1
            self._warn(f"模型调用失败，本次降级为规则模板：{type(exc).__name__}")
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

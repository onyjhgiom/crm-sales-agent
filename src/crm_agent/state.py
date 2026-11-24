"""LangGraph 的状态定义。

设计原则：状态里只放"能被下一跳用到的结构化事实"，不放对话原文——
对话原文交给 checkpointer 存，避免状态越滚越大导致上下文失控
（这一点在 hello-agents 第 9 章「上下文工程」里有专门讨论）。
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    question: str                 # 本轮问题
    advertiser_id: str | None     # 目标广告主，可能由 UI 传入或从问题里解析
    intent: str                   # profile / opportunity / analysis / knowledge / smalltalk
    evidence: dict[str, Any]      # 结构化证据（画像、商机、SQL 结果、检索命中）
    answer: str                   # 最终回答
    citations: list[str]          # 引用来源（知识库相对路径）
    trace: list[dict[str, Any]]   # 执行轨迹，前端按步展示
    review: dict[str, Any]        # 反思校验结果
    retry: int                    # 已重试次数
    history: list[dict[str, str]] # 最近几轮问答摘要，用于多轮指代

    # ---- 规划 / 工具 / 结果整合（新增，旧字段语义不变）----
    plan: dict[str, Any]          # router 产出的任务计划（单意图也有，只是不触发编排）
    structure: dict[str, Any]     # IntegratedAnswer 的结构化形式
    confidence: float             # 回答置信度（0~1）
    degraded: bool                # 是否发生过工具/模型降级
    warnings: list[str]           # 核对提示，不影响主答案
    tool_results: list[dict[str, Any]]  # 工具调用结果摘要


INTENTS = {
    "profile": "客户画像",
    "opportunity": "商机辅助",
    "analysis": "经营分析",
    "knowledge": "策略知识",
    "smalltalk": "闲聊/能力介绍",
}

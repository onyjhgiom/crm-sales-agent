"""任务理解与规划层。

相对旧版的增强：
- 旧版"决策"全塞在 router 的 if-else 里，一句话只能出一个意图；
  现在 router 判完意图后由 Planner 产出显式计划（任务列表 + 工具选择 + 依赖 + 步数预算）。
- 单意图问题仍走原来的节点，答案逐字节不变；只有识别到复合任务（"给我画像，
  再分析为什么下滑"）才切到编排节点，把多个子任务结果整合成一份回答。
- 上下文不再只是"最近几轮原文"，而是抽成槽位：当前客户、上轮意图、讨论过的指标、
  时间口径、上轮建议。槽位参与指代消解和规划置信度，也能拼成在线模型的背景说明。
- 规划器是纯函数式、无网络依赖：LLM 不可用时规则照常工作，只是置信度更保守。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 复合任务连接词。刻意不含"还有呢/继续"这类追问延续语——它们属于上下文继承，不是新任务。
_COMPOUND_RE = re.compile(r"(?:同时|并且|另外|此外|顺便|以及|再帮我|还要|分别|；|;)")
_METRIC_WORDS = ("roas", "投产", "cvr", "转化率", "cpc", "点击成本", "cpa", "转化成本",
                 "消耗", "花费", "预算", "利用率", "转化数", "负面", "到期")
_WINDOW_RE = re.compile(r"(?:近|最近|过去|前)\s*(\d+)\s*(天|日|周|个月|月)")

# 每种意图建议的工具（只列该意图真正需要的，规划器据此生成编排步骤）
_INTENT_TOOLS = {
    "profile": ("advertiser.get", "campaign.list", "metrics.list", "interaction.list"),
    "opportunity": ("advertiser.get", "kb.retrieve"),
    "analysis": ("sql.generate", "sql.execute"),
    "knowledge": ("kb.retrieve",),
    "smalltalk": (),
}


@dataclass
class TaskSpec:
    id: str
    question: str
    intent: str
    advertiser_id: str | None = None
    tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "question": self.question, "intent": self.intent,
            "advertiser_id": self.advertiser_id, "tools": list(self.tools),
            "depends_on": list(self.depends_on), "reason": self.reason,
        }


@dataclass
class Plan:
    mode: str                              # single | composite
    tasks: list[TaskSpec]
    confidence: float = 1.0
    clarify: str | None = None
    slots: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tasks": [t.to_dict() for t in self.tasks],
            "confidence": round(self.confidence, 3),
            "clarify": self.clarify,
            "slots": self.slots,
            "reason": self.reason,
        }


def extract_slots(history: list[dict[str, Any]] | None) -> dict[str, Any]:
    """把对话历史压成结构化槽位，而不是只截原文。"""
    slots: dict[str, Any] = {
        "turns": len(history or []),
        "advertiser_id": None,
        "intent": None,
        "metrics": [],
        "window": None,
        "last_advice": "",
    }
    for turn in history or []:
        content = str(turn.get("content", ""))
        if turn.get("role") == "assistant":
            if turn.get("advertiser_id"):
                slots["advertiser_id"] = turn["advertiser_id"]
            if turn.get("intent"):
                slots["intent"] = turn["intent"]
            if content:
                slots["last_advice"] = content[:120]
        for word in _METRIC_WORDS:
            if word in content.lower() and word not in slots["metrics"]:
                slots["metrics"].append(word)
        m = _WINDOW_RE.search(content)
        if m:
            slots["window"] = f"{m.group(1)}{m.group(2)}"
    slots["metrics"] = slots["metrics"][:8]
    return slots


def slots_prompt(slots: dict[str, Any] | None) -> str:
    """把槽位拼成一行模型可读的背景（仅在线提示词使用，离线模板不拼）。"""
    if not slots or not slots.get("turns"):
        return ""
    parts = [f"对话轮数={slots['turns']}"]
    if slots.get("advertiser_id"):
        parts.append(f"当前客户={slots['advertiser_id']}")
    if slots.get("intent"):
        parts.append(f"上轮意图={slots['intent']}")
    if slots.get("metrics"):
        parts.append("讨论指标=" + "/".join(slots["metrics"][:5]))
    if slots.get("window"):
        parts.append(f"时间口径={slots['window']}")
    if slots.get("last_advice"):
        parts.append("上轮建议摘要=" + slots["last_advice"][:60])
    return "【结构化上下文】" + "；".join(parts)


def split_compound(question: str) -> list[str]:
    """按连接词拆出子问题；空片段与纯语气词丢弃。"""
    parts = [p.strip(" 。，,？?！!\n\t") for p in _COMPOUND_RE.split(question or "")]
    return [p for p in parts if len(p) >= 2]


class Planner:
    def __init__(self, max_steps: int = 4, enabled: bool = True) -> None:
        self.max_steps = max(1, max_steps)
        self.enabled = enabled

    def plan(self, question: str, advertiser_id: str | None,
             history: list[dict[str, Any]], ctx: Any, *,
             intent: str, reason: str = "") -> Plan:
        """基于 router 已判定的意图生成执行计划。

        单意图是绝大多数情况，此时 mode=single，下游节点与旧版完全一致。
        """
        # 避免导入环：router 在运行时已经加载完毕
        from .nodes.router import decide_intent, has_intent_signal

        slots = extract_slots(history)
        tasks: list[TaskSpec] = []
        mode = "single"

        if self.enabled:
            clauses = split_compound(question)
            if len(clauses) > 1:
                for idx, clause in enumerate(clauses):
                    if not has_intent_signal(clause):
                        continue
                    sub_intent, sub_reason = decide_intent(clause, advertiser_id)
                    tasks.append(TaskSpec(
                        id=f"t{idx + 1}", question=clause, intent=sub_intent,
                        advertiser_id=advertiser_id if sub_intent in {"profile", "opportunity", "analysis"} else None,
                        tools=list(_INTENT_TOOLS.get(sub_intent, ())),
                        reason=sub_reason,
                    ))
                distinct = {t.intent for t in tasks if t.intent != "smalltalk"}
                if len(distinct) >= 2 and 2 <= len(tasks) <= self.max_steps:
                    mode = "composite"
                else:
                    tasks = []

        if mode != "composite":
            tasks = [TaskSpec(
                id="t1", question=question, intent=intent, advertiser_id=advertiser_id,
                tools=list(_INTENT_TOOLS.get(intent, ())), reason=reason or "单意图直接执行",
            )]

        # 工具名对齐实际注册表：未注册的工具不进计划（可维护：加工具不用改这里）
        registry = getattr(ctx, "registry", None)
        if registry is not None:
            known = set(registry.names())
            for task in tasks:
                task.tools = [t for t in task.tools if t in known]

        confidence = self._confidence(intent, advertiser_id, slots, mode)
        clarify = None
        if intent in {"profile", "opportunity"} and not advertiser_id and not slots.get("advertiser_id"):
            clarify = "请指定客户编号（如 A003），或说明要看批量名单。"

        return Plan(
            mode=mode, tasks=tasks, confidence=confidence, clarify=clarify,
            slots=slots, reason=reason or "规则判定",
        )

    @staticmethod
    def _confidence(intent: str, advertiser_id: str | None,
                    slots: dict[str, Any], mode: str) -> float:
        score = 0.8 if intent != "smalltalk" else 0.9
        if intent in {"profile", "opportunity", "analysis"} and not advertiser_id:
            score -= 0.12  # 批量/全库问题可接受，但客户级问题缺对象要更保守
        if slots.get("turns") and intent != "smalltalk":
            score += 0.1   # 有上下文佐证
        if mode == "composite":
            score -= 0.05  # 拆任务本身有不确定性
        return max(0.3, min(0.99, round(score, 3)))

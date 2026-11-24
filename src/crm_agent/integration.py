"""结果整合层：把多个子任务 / 多个工具的产物合成一份结构化回答。

相对旧版：
- 旧版每个节点各自渲染 Markdown，没有任何统一结构，前端只能整段显示；
- 现在有 IntegratedAnswer（摘要 / 分段 / 引用 / 置信度 / 下一步 / 警告），
  既能渲染成前端可直接显示的 Markdown，也能作为 API 的结构化字段返回；
- 数字核对只产出"警告"而不改写正文——规则算出的事实不能被模型润色覆盖，
  但也不能让编造的数字悄悄混过去。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .state import INTENTS


@dataclass
class AnswerSection:
    task_id: str
    title: str
    content: str
    intent: str
    citations: list[str] = field(default_factory=list)
    evidence_keys: list[str] = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class IntegratedAnswer:
    summary: str
    sections: list[AnswerSection]
    mode: str = "single"
    citations: list[str] = field(default_factory=list)
    confidence: float = 1.0
    next_actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    degraded: bool = False

    def render(self) -> str:
        """单任务直接返回原子节点原文（保证零回归）；多任务渲染合并版。"""
        if self.mode != "composite":
            return self.sections[0].content if self.sections else self.summary

        lines = ["### 任务完成概览", "", self.summary, ""]
        for idx, sec in enumerate(self.sections, 1):
            lines += [f"#### {idx}. {sec.title}", "", sec.content.strip(), ""]
        if self.citations:
            lines += ["**引用来源**：" + "、".join(self.citations), ""]
        lines += [f"**数据置信度**：{self.confidence:.2f}"]
        if self.degraded:
            lines.append("部分工具已降级执行，关键数字请以后台数据为准。")
        if self.next_actions:
            lines += ["", "**建议下一步**：" + "；".join(self.next_actions)]
        if self.warnings:
            lines += ["", "> 核对提示：" + "；".join(self.warnings[:4])]
        return "\n".join(lines).strip() + "\n"

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "mode": self.mode,
            "sections": [{
                "task_id": s.task_id, "title": s.title, "intent": s.intent,
                "content": s.content, "citations": list(s.citations),
                "evidence_keys": list(s.evidence_keys),
                "confidence": round(s.confidence, 3),
            } for s in self.sections],
            "citations": list(self.citations),
            "confidence": round(self.confidence, 3),
            "next_actions": list(self.next_actions),
            "warnings": list(self.warnings),
            "degraded": self.degraded,
        }


def integrate(plan: Any, task_results: list[dict[str, Any]],
              tool_results: list[Any] | None = None) -> IntegratedAnswer:
    """把计划、子任务答案、工具结果汇总成一个 IntegratedAnswer。"""
    tasks = getattr(plan, "tasks", []) or []
    mode = getattr(plan, "mode", "single")
    warnings: list[str] = []
    degraded = False
    failed_tools: list[str] = []

    for result in tool_results or []:
        if not getattr(result, "ok", False):
            failed_tools.append(f"{getattr(result, 'tool', '?')}（{getattr(result, 'error_code', 'tool_failed')}）")
        if getattr(result, "degraded", False):
            degraded = True

    if failed_tools:
        warnings.append("工具调用失败：" + "、".join(failed_tools[:3]))
        degraded = True

    def field(task: Any, name: str, default: Any = "") -> Any:
        # Plan.to_dict() 之后 task 是 dict；直接构造的 TaskSpec 是对象
        if isinstance(task, dict):
            return task.get(name, default)
        return getattr(task, name, default)

    sections: list[AnswerSection] = []
    citations: set[str] = set()
    for idx, task in enumerate(tasks):
        res = task_results[idx] if idx < len(task_results) else {}
        task_id = str(field(task, "id", f"t{idx + 1}"))
        intent = str(field(task, "intent", "smalltalk"))
        content = (res.get("answer") or "").strip() or "（本项未产出内容）"
        review = res.get("review") or {}
        for issue in (review.get("issues") or [])[:2]:
            warnings.append(f"{task_id}：{issue}")
        evidence = res.get("evidence") or {}
        mismatch = number_mismatch(content, evidence)
        if mismatch:
            warnings.append(f"{task_id}：部分数字未见于工具结果（{', '.join(mismatch[:3])}），请核对")
        sec_cites = sorted(set(res.get("citations") or []))
        citations.update(sec_cites)
        sections.append(AnswerSection(
            task_id=task_id,
            title=INTENTS.get(intent, intent),
            content=content,
            intent=intent,
            citations=sec_cites,
            evidence_keys=sorted(k for k in evidence if k != "retrieved"),
            confidence=0.85 if review.get("ok") else 0.65,
        ))

    if mode == "composite":
        titles = "、".join(s.title for s in sections)
        summary = f"已完成 {len(sections)} 项任务：{titles}。"
    else:
        summary = sections[0].content[:120] if sections else ""

    confidence = float(getattr(plan, "confidence", 0.8))
    if failed_tools:
        confidence *= 0.85
    if any(not s.confidence for s in sections):
        confidence *= 0.9
    confidence = max(0.2, min(0.99, round(confidence, 3)))

    clarify = getattr(plan, "clarify", None)
    next_actions = [clarify] if clarify else []
    if mode == "composite" and not next_actions:
        next_actions.append("如需细化某一项，请点名客户或指标后追问。")

    return IntegratedAnswer(
        summary=summary, sections=sections, mode=mode,
        citations=sorted(citations), confidence=confidence,
        next_actions=next_actions, warnings=warnings, degraded=degraded,
    )


_NUM_RE = re.compile(r"(?<![\d.])(\d[\d,]*(?:\.\d+)?)\s*(万元|万|元|%|百分比)?")


def number_mismatch(answer: str, evidence: dict[str, Any]) -> list[str]:
    """检查答案里的带单位数字是否出现在结构化证据里。只报可疑，不改写正文。"""
    rows = evidence.get("rows") or []
    if not rows:
        return []
    def norm(value: Any) -> str:
        text = str(value).replace(",", "").strip()
        try:
            number = float(text)
        except ValueError:
            return text
        return str(int(number)) if number.is_integer() else str(round(number, 4))

    pool: set[str] = set()
    for row in rows:
        for value in (row.values() if isinstance(row, dict) else []):
            pool.add(norm(value))
            match = _NUM_RE.search(str(value))
            if match:
                pool.add(norm(match.group(1)))

    suspicious: list[str] = []
    for match in _NUM_RE.finditer(answer or ""):
        token = match.group(1)
        normalized = norm(token)
        if normalized in pool:
            continue
        # 天数（近 30 天）、条数、月份等小整数不是"结果数字"，跳过以免误报
        try:
            if float(normalized).is_integer() and abs(float(normalized)) <= 31:
                continue
        except ValueError:
            pass
        if len(normalized) > 12:
            continue
        if token not in suspicious:
            suspicious.append(token)
        if len(suspicious) >= 5:
            break
    return suspicious

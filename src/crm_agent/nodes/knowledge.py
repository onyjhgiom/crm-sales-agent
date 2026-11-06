"""策略知识 Agent：面向"怎么做/为什么/判断标准"类问题。

这类问题不查数据库，只查知识库。要求回答里必须带引用来源，
否则销售把一套没出处的"经验"发给客户，比不回答更糟。
"""

from __future__ import annotations

import re

from ..context import Context
from ..state import AgentState
from ..tools.rag import format_context


def _clean(text: str) -> str:
    """去掉 Markdown 标记与表格行，让抽取式回答读起来像人话。"""
    kept: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("|") or set(line) <= set("|-: "):
            continue
        line = re.sub(r"^#+\s*", "", line).replace("**", "")
        kept.append(line)
    return " ".join(kept)


def _points(text: str, limit: int = 3) -> list[str]:
    parts = re.split(r"[。；]\s*", _clean(text))
    return [p.strip() for p in parts if len(p.strip()) > 12][:limit]


def render_template(hits: list) -> str:
    if not hits:
        return "知识库里没有覆盖这个问题，建议补充对应的策略文档后重试。"
    lines = ["### 策略回答", ""]
    for i, hit in enumerate(hits[:2], 1):
        lines.append(f"**要点 {i}：{hit.chunk.title}**（来源《{hit.chunk.source}》）")
        for s in _points(hit.chunk.text, 3):
            lines.append(f"- {s}。")
        lines.append("")
    lines.append("以上内容摘自团队策略文档，具体执行时以客户实际数据为准。")
    return "\n".join(lines)


def knowledge_node(state: AgentState, ctx: Context) -> dict:
    question = state.get("question", "")
    trace = list(state.get("trace", []))
    hits = ctx.kb.retrieve(question, top_k=ctx.settings.top_k)
    trace.append({"step": "knowledge", "detail": f"知识库检索命中 {len(hits)} 段",
                  "sources": [h.chunk.source for h in hits]})

    citations = sorted({h.chunk.source for h in hits})
    answer = None
    if ctx.llm.available and hits:
        answer = ctx.llm.chat([
            {"role": "system", "content":
             "你是广告代理商的内训讲师。只依据【参考资料】回答，不许补充资料外的内容；"
             "回答结构：结论 → 判断标准 → 执行动作；每个要点后用（来源：文件名）标注引用。用 Markdown。"},
            {"role": "user", "content":
             f"问题：{question}\n\n【参考资料】\n{format_context(hits, max_chars=1600)}"},
        ], temperature=0.2, max_tokens=800)

    if not answer:
        answer = render_template(hits)
        trace.append({"step": "knowledge", "detail": "模型不可用，使用抽取式模板"})

    return {"answer": answer, "evidence": {"retrieved": [h.as_dict() for h in hits]},
            "citations": citations, "trace": trace}

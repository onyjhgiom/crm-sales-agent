"""反思（Reflection）节点。

每个子 Agent 出完答案后都过这一道自检，检查的是"能不能拿去用"而不是"文采好不好"：
- 结论里有没有数字（销售不可能拿一句没有数据的判断去打电话）；
- 知识类回答有没有标引用；
- 回答是不是短到没有信息量。

不通过就回退重跑一次；离线模板重跑不会变好，所以第二次直接追加一条自检提示，
把不确定性显式暴露给使用者——这比假装没问题更安全。
"""

from __future__ import annotations

import re

from ..context import Context
from ..state import AgentState

MIN_LEN = {"profile": 200, "opportunity": 200, "analysis": 100, "knowledge": 120, "smalltalk": 100}
NEED_NUMBER = {"profile", "opportunity", "analysis"}


def review_node(state: AgentState, ctx: Context) -> dict:
    answer = state.get("answer", "") or ""
    intent = state.get("intent", "smalltalk")
    issues: list[str] = []

    if len(answer) < MIN_LEN.get(intent, 100):
        issues.append("回答过短，信息量不足")

    if intent in NEED_NUMBER and not re.search(r"\d", answer):
        issues.append("缺少数字支撑")

    if state.get("citations"):
        names = [c.split("/")[-1].replace(".md", "") for c in state["citations"]]
        if not any(n[:4] in answer for n in names):
            issues.append("引用了知识库但正文未标注来源")

    retry = int(state.get("retry", 0))
    trace = list(state.get("trace", []))

    if issues and retry == 0:
        trace.append({"step": "review", "detail": f"自检未通过（{'；'.join(issues)}），回退重跑一次"})
        return {
            "review": {"ok": False, "issues": issues, "retried": False},
            "retry": retry + 1,
            "trace": trace,
        }

    if issues:
        answer += "\n\n> 自检提示：" + "；".join(issues) + "。请结合后台数据核对后再使用。"
        trace.append({"step": "review", "detail": "重跑后仍未通过，已追加自检提示"})
        return {"answer": answer, "review": {"ok": False, "issues": issues, "retried": True}, "trace": trace}

    trace.append({"step": "review", "detail": "自检通过"})
    return {"answer": answer, "review": {"ok": True, "issues": [], "retried": True}, "trace": trace}

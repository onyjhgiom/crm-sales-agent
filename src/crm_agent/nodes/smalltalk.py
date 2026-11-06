"""闲聊 / 能力介绍节点。

销售第一次打开工具时问的多是"你能干嘛"，这个节点负责把能力边界讲清楚，
顺便给几个可以直接点的示例问题，减少"不会用"导致的弃用。
"""

from __future__ import annotations

from ..context import Context
from ..state import AgentState

EXAMPLES = [
    "帮我出一下 A003 的客户画像",
    "A003 现在有什么商机，我该怎么跟进",
    "今天该优先跟进哪些客户，给我个名单",
    "各行业的消耗和 ROAS 汇总",
    "转化率最低的 10 个客户是谁",
    "什么情况下可以谈增投",
    "套餐快到期的客户怎么谈续费",
]


def smalltalk_node(state: AgentState, ctx: Context) -> dict:
    trace = list(state.get("trace", []))
    trace.append({"step": "smalltalk", "detail": "能力介绍"})
    text = (
        "我可以帮你做三件事：\n"
        "1. **客户画像**——把档案、计划、月度指标、跟进记录汇总成一张能直接复述的画像卡；\n"
        "2. **商机辅助**——按规则给客户打分，告诉你下一步做什么、怎么说、大概能带来多少增量；\n"
        "3. **经营分析**——把自然语言问题转成查询，出数并给归因方向。\n\n"
        "知识库里还有增投、续费、分层运营的 SOP 和案例，问“怎么做”我也会带出处回答。\n\n"
        "**可以这样问**：\n" + "\n".join(f"- {q}" for q in EXAMPLES) +
        f"\n\n当前数据：{ctx.store.query('SELECT COUNT(*) AS n FROM advertisers')[0]['n']} 个广告主，"
        f"知识库 {ctx.kb.stats()['chunks']} 段。"
    )
    return {"answer": text, "evidence": {}, "citations": [], "trace": trace}

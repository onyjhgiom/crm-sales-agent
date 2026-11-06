"""命令行入口。

三种用法：
  python run.py                      # 交互式问答
  python run.py --demo               # 跑一遍预设演示脚本（录屏用）
  python run.py --ask "各行业消耗汇总"  # 单条问题
  python run.py --offline            # 强制离线规则模式，不需要 API Key
"""

from __future__ import annotations

import argparse
import sys

from .agent import SalesAgent

DEMO_QUESTIONS = [
    ("A003 这个客户什么情况，帮我出个画像", "A003"),
    ("他现在有什么商机，我该怎么跟进", "A003"),
    ("今天该优先跟进哪些客户？给我个名单", None),
    ("各行业的消耗和 ROAS 汇总一下", None),
    ("转化率最低的 10 个客户是谁", None),
    ("什么情况下可以谈增投？", None),
    ("套餐快到期的客户怎么谈续费", None),
]


def _print_result(result: dict, show_trace: bool = True) -> None:
    print("\n" + "=" * 72)
    print(result["answer"])
    print("-" * 72)
    print(f"意图: {result['intent']}   自检: {'通过' if result.get('review', {}).get('ok') else '未通过'}"
          f"   引用: {', '.join(result.get('citations') or []) or '无'}")
    if show_trace:
        for step in result.get("trace", []):
            print(f"  · {step.get('step')}: {step.get('detail')}")


def run_demo(agent: SalesAgent) -> None:
    for q, adv in DEMO_QUESTIONS:
        print(f"\n\033[1m>>> {q}\033[0m")
        result = agent.ask(q, advertiser_id=adv, thread_id="demo")
        _print_result(result)


def run_repl(agent: SalesAgent) -> None:
    print("crm-sales-agent 交互模式。输入问题开始；/adv A003 切换客户；/quit 退出。")
    thread_id = "cli"
    advertiser_id = None
    while True:
        try:
            line = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见")
            break
        if not line:
            continue
        if line in ("/quit", "/exit"):
            break
        if line.startswith("/adv"):
            advertiser_id = line.split(maxsplit=1)[-1].strip().upper() or None
            print(f"已切换客户: {advertiser_id}")
            continue
        result = agent.ask(line, thread_id=thread_id, advertiser_id=advertiser_id)
        _print_result(result, show_trace=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="CRM 销售提效 Agent")
    parser.add_argument("--ask", help="单条问题")
    parser.add_argument("--demo", action="store_true", help="运行演示脚本")
    parser.add_argument("--offline", action="store_true", help="强制离线规则模式")
    parser.add_argument("--adv", help="指定广告主 ID，如 A003")
    args = parser.parse_args()

    agent = SalesAgent(use_llm=not args.offline)
    mode = "在线（大模型）" if agent.ctx.llm.available else "离线（规则模板）"
    print(f"模式: {mode}   数据: {agent.ctx.settings.db_path}")

    try:
        if args.ask:
            _print_result(agent.ask(args.ask, advertiser_id=args.adv))
        elif args.demo:
            run_demo(agent)
        else:
            run_repl(agent)
    finally:
        agent.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

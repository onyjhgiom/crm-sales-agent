"""评估脚本：跑一遍 cases.jsonl，输出四个可量化的指标。

为什么这些指标而不是"感觉回答不错"：
- 意图准确率：路由错了，后面的检索和生成再好也没用；
- 关键事实命中率：回答里必须出现数据里的名字/指标，证明它真的查了库而不是编；
- 引用覆盖率：知识类回答必须带出处，否则销售不敢转给客户；
- 自检通过率：反思节点的价值要能被度量，否则就是个摆设。

用法：
  python evals/run_eval.py            # 离线规则模式（结果可复现）
  python evals/run_eval.py --llm      # 在线大模型模式（需要 .env 配置 key）
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from crm_agent.agent import SalesAgent  # noqa: E402


def load_cases(path: str) -> list[dict]:
    cases = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def run(use_llm: bool) -> int:
    agent = SalesAgent(use_llm=use_llm)
    cases = load_cases(os.path.join(ROOT, "evals", "cases.jsonl"))

    rows = []
    for case in cases:
        start = time.perf_counter()
        result = agent.ask(case["question"], advertiser_id=case.get("advertiser_id"),
                           thread_id=f"eval-{case['id']}")
        elapsed = (time.perf_counter() - start) * 1000

        answer = result.get("answer", "").lower()
        kw_total = len(case["expect_keywords"])
        kw_hit = sum(1 for k in case["expect_keywords"] if k.lower() in answer)
        rows.append({
            "id": case["id"],
            "question": case["question"],
            "expect": case["expect_intent"],
            "got": result.get("intent"),
            "intent_ok": result.get("intent") == case["expect_intent"],
            "keyword_total": kw_total,
            "keyword_hit": kw_hit,
            "keyword_ok": kw_hit == kw_total,
            "expect_citation": case["expect_citation"],
            "citations": result.get("citations") or [],
            "selfcheck_ok": bool(result.get("review", {}).get("ok")),
            "latency_ms": round(elapsed, 1),
            "answer_len": len(result.get("answer", "")),
        })

    total = len(rows)
    intent_acc = sum(r["intent_ok"] for r in rows) / total
    kw_acc = sum(r["keyword_ok"] for r in rows) / total
    cite_cases = [r for r in rows if r["expect_citation"]]
    cite_rate = (sum(1 for r in cite_cases if r["citations"]) / len(cite_cases)) if cite_cases else 0.0
    selfcheck_rate = sum(r["selfcheck_ok"] for r in rows) / total
    avg_latency = statistics.mean(r["latency_ms"] for r in rows)

    summary = {
        "mode": "llm" if agent.ctx.llm.available else "offline",
        "cases": total,
        "intent_accuracy": round(intent_acc, 3),
        "keyword_pass_rate": round(kw_acc, 3),
        "citation_rate": round(cite_rate, 3),
        "selfcheck_pass_rate": round(selfcheck_rate, 3),
        "avg_latency_ms": round(avg_latency, 1),
    }

    print(f"\n模式：{summary['mode']}    用例数：{total}")
    print(f"意图准确率      {intent_acc * 100:5.1f}%")
    print(f"关键事实命中率  {kw_acc * 100:5.1f}%")
    print(f"引用覆盖率      {cite_rate * 100:5.1f}%")
    print(f"自检通过率      {selfcheck_rate * 100:5.1f}%")
    print(f"平均延迟        {avg_latency:5.1f} ms")

    failures = [r for r in rows if not r["intent_ok"] or not r["keyword_ok"]]
    if failures:
        print("\n未通过的用例：")
        for r in failures:
            print(f"  {r['id']} 意图 {r['expect']}->{r['got']}  关键词命中 {r['keyword_hit']}/{r['keyword_total']}  {r['question']}")

    out_dir = os.path.join(ROOT, "evals")
    with open(os.path.join(out_dir, "last_report.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "rows": rows}, f, ensure_ascii=False, indent=2)

    write_markdown(summary, rows, failures)
    print(f"\n报告已写入 evals/report.md")
    agent.close()
    return 0 if intent_acc >= 0.9 and kw_acc >= 0.85 else 1


def write_markdown(summary: dict, rows: list[dict], failures: list[dict]) -> None:
    lines = [
        "# 评估报告",
        "",
        f"- 模式：{summary['mode']}",
        f"- 用例数：{summary['cases']}",
        f"- 意图准确率：{summary['intent_accuracy'] * 100:.1f}%",
        f"- 关键事实命中率：{summary['keyword_pass_rate'] * 100:.1f}%",
        f"- 引用覆盖率：{summary['citation_rate'] * 100:.1f}%",
        f"- 自检通过率：{summary['selfcheck_pass_rate'] * 100:.1f}%",
        f"- 平均延迟：{summary['avg_latency_ms']} ms",
        "",
        "## 明细",
        "",
        "| # | 问题 | 期望意图 | 实际意图 | 关键词 | 引用 | 自检 | 延迟(ms) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['question']} | {r['expect']} | {r['got']} | "
            f"{r['keyword_hit']}/{r['keyword_total']} | {len(r['citations'])} | "
            f"{'✓' if r['selfcheck_ok'] else '✗'} | {r['latency_ms']} |"
        )

    if failures:
        lines += ["", "## 未通过用例（真实记录，不修饰）", ""]
        for r in failures:
            lines.append(f"- `{r['id']}` {r['question']}：期望 {r['expect']}，实际 {r['got']}，"
                         f"关键词 {r['keyword_hit']}/{r['keyword_total']}")

    with open(os.path.join(ROOT, "evals", "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="使用在线大模型")
    args = parser.parse_args()
    sys.exit(run(args.llm))

"""评估脚本：结构化验证 + 失败归因 + 边界集/保留集 + Pass^k + 轨迹前缀回归。

方法论依据：ai-agent-book 第 7 章「Agent 的评估」。相对旧版（只报四个百分率）的升级：
1. 验证器核实可复核事实（verifiers.py）， refusal 类用例禁止出现编造数字；
2. 边界集 / 保留集分开统计——改动既要修好边界，又不能破坏保留集；
3. 失败归因定位首个错误（failure_attribution.py），区分根因与后果；
4. Pass^k：同一用例连续跑 k 次全部通过的概率，衡量业务可靠性而非能力上限；
5. 统计显著性：多运行的均值/标准差/95% 置信区间，避免把抽样噪声当进步；
6. 轨迹前缀回归：冻结上下文，验证下一步决策落在可接受动作集合内。

用法：
  python evals/run_eval.py                       # 离线模式，1 轮
  python evals/run_eval.py --repeats 5           # 离线模式，跑 5 轮算 Pass^k 与置信区间
  python evals/run_eval.py --llm                 # 在线模型模式（需要 .env key）
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "evals"))

from crm_agent.agent import SalesAgent  # noqa: E402
from verifiers import verify, verify_prefix  # noqa: E402
from failure_attribution import attribute  # noqa: E402


def load_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def run_one_pass(agent: SalesAgent, cases: list[dict]) -> list[dict]:
    """跑一轮完整评估，返回每条用例的结构化验证结果。"""
    results = []
    for case in cases:
        start = time.perf_counter()
        result = agent.ask(case["question"],
                           advertiser_id=case.get("advertiser_id"),
                           thread_id=f"eval-{case['id']}-{time.time_ns()}")
        elapsed = (time.perf_counter() - start) * 1000
        v = verify(case, result)
        v["latency_ms"] = round(elapsed, 1)
        v["answer_len"] = len(result.get("answer", ""))
        results.append(v)
    return results


def aggregate(passes: list[list[dict]], cases: list[dict]) -> dict:
    """跨多轮聚合指标：四指标均值、Pass^k、置信区间、边界集/保留集。"""
    n = len(passes)
    case_ids = [c["id"] for c in cases]

    # 每条用例跨轮的通过情况
    per_case = {}
    for cid in case_ids:
        seq = [next(v for v in one if v["id"] == cid) for one in passes]
        per_case[cid] = seq

    def metric(fn) -> dict:
        vals = [sum(fn(v) for v in one) / len(one) for one in passes]
        mean = statistics.mean(vals)
        sd = statistics.stdev(vals) if n > 1 else 0.0
        se = sd / math.sqrt(n) if n > 1 else 0.0
        ci = 1.96 * se
        return {
            "mean": round(mean, 4),
            "std": round(sd, 4),
            "ci95": round(ci, 4),
            "runs": [round(x, 4) for x in vals],
        }

    intent = metric(lambda v: 1 if v["intent_ok"] else 0)
    answer = metric(lambda v: 1 if v["answer_ok"] else 0)
    citation = metric(lambda v: 1 if v["citation_ok"] else 0)
    selfcheck = metric(lambda v: 1 if v["selfcheck_ok"] else 0)
    overall = metric(lambda v: 1 if v["pass"] else 0)

    # Pass@k（至少一次通过）与 Pass^k（k 次全通过）——针对每条用例的"是否稳定通过"
    pass_at_k = {}
    pass_pow_k = {}
    for cid, seq in per_case.items():
        flags = [1 if v["pass"] else 0 for v in seq]
        pass_at_k[cid] = 1 if any(flags) else 0   # 至少一次
        pass_pow_k[cid] = 1 if all(flags) else 0  # 全部通过

    # 边界集 / 保留集分别统计（最后一轮为准，多轮取均值）
    by_set = {}
    set_map = {c["id"]: c.get("set", "retention") for c in cases}
    for s in ("boundary", "retention"):
        ids = [cid for cid in case_ids if set_map[cid] == s]
        if not ids:
            continue
        rates = [sum(1 for v in one if v["id"] in ids and v["pass"]) / len(ids) for one in passes]
        by_set[s] = {
            "count": len(ids),
            "ids": ids,
            "pass_rate_mean": round(statistics.mean(rates), 4),
            "pass_rate_ci95": round(1.96 * statistics.stdev(rates) / math.sqrt(n), 4) if n > 1 else 0.0,
        }

    avg_latency = statistics.mean(v["latency_ms"] for one in passes for v in one)

    return {
        "repeats": n,
        "intent_accuracy": intent,
        "keyword_pass_rate": answer,  # 兼容旧字段名
        "answer_pass_rate": answer,
        "citation_rate": citation,
        "selfcheck_pass_rate": selfcheck,
        "overall_pass_rate": overall,
        "pass_at_k": pass_at_k,
        "pass_pow_k": pass_pow_k,
        "pass_pow_k_rate": round(sum(pass_pow_k.values()) / len(pass_pow_k), 4),
        "by_set": by_set,
        "avg_latency_ms": round(avg_latency, 1),
    }


def run(use_llm: bool, repeats: int) -> int:
    agent = SalesAgent(use_llm=use_llm)
    cases = load_jsonl(os.path.join(ROOT, "evals", "cases.jsonl"))

    passes = []
    for r in range(repeats):
        passes.append(run_one_pass(agent, cases))

    summary = aggregate(passes, cases)
    summary["mode"] = "llm" if agent.ctx.llm.available else "offline"
    last = passes[-1]

    # 失败归因：对所有轮次里出现过的失败用例做归因（用最后一轮的证据）
    attributions = []
    for v in last:
        attr = attribute(v)
        if attr:
            attributions.append(attr)

    # 轨迹前缀回归
    prefix_cases = load_jsonl(os.path.join(ROOT, "evals", "prefix_cases.jsonl"))
    prefix_results = []
    for pc in prefix_cases:
        result = agent.ask(pc["question"], advertiser_id=pc.get("advertiser_id"),
                           thread_id=f"pfx-{pc['id']}")
        prefix_results.append(verify_prefix(pc, result))

    out_dir = os.path.join(ROOT, "evals")
    with open(os.path.join(out_dir, "last_report.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "last_pass": last,
                   "attributions": attributions, "prefix_results": prefix_results},
                  f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "attributions.json"), "w", encoding="utf-8") as f:
        json.dump(attributions, f, ensure_ascii=False, indent=2)

    write_markdown(summary, last, attributions, prefix_results)
    agent.close()

    print(f"\n模式：{summary['mode']}    用例数：{len(cases)}    重复轮次：{repeats}")
    print(f"意图准确率      {summary['intent_accuracy']['mean'] * 100:5.1f}%  "
          f"(±{summary['intent_accuracy']['ci95'] * 100:.1f})")
    print(f"关键事实命中率  {summary['answer_pass_rate']['mean'] * 100:5.1f}%  "
          f"(±{summary['answer_pass_rate']['ci95'] * 100:.1f})")
    print(f"引用覆盖率      {summary['citation_rate']['mean'] * 100:5.1f}%")
    print(f"自检通过率      {summary['selfcheck_pass_rate']['mean'] * 100:5.1f}%")
    print(f"整体通过率      {summary['overall_pass_rate']['mean'] * 100:5.1f}%")
    print(f"Pass^{repeats}（连续{repeats}次全过） {summary['pass_pow_k_rate'] * 100:5.1f}%")
    for s, info in summary["by_set"].items():
        print(f"  [{s}] {info['count']} 条  通过率 {info['pass_rate_mean'] * 100:.1f}%")
    print(f"轨迹前缀回归    {sum(1 for p in prefix_results if p['pass'])}/{len(prefix_results)} 通过")
    print(f"平均延迟        {summary['avg_latency_ms']} ms")

    # exit code：保留集必须全过，边界集必须全过，前缀回归必须全过
    ret_ok = all(summary["by_set"].get(s, {}).get("pass_rate_mean", 1) >= 1.0
                 for s in ("boundary", "retention"))
    pfx_ok = all(p["pass"] for p in prefix_results)
    return 0 if ret_ok and pfx_ok else 1


def write_markdown(summary: dict, last: list[dict], attributions: list[dict],
                   prefix_results: list[dict]) -> None:
    n = summary["repeats"]
    lines = [
        "# 评估报告（结构化验证）",
        "",
        "## 汇总",
        "",
        f"- 模式：{summary['mode']}",
        f"- 用例数：{len(last)}　重复轮次：{n}",
        f"- 意图准确率：{summary['intent_accuracy']['mean'] * 100:.1f}% ± {summary['intent_accuracy']['ci95'] * 100:.1f}（95% CI）",
        f"- 关键事实命中率：{summary['answer_pass_rate']['mean'] * 100:.1f}% ± {summary['answer_pass_rate']['ci95'] * 100:.1f}",
        f"- 引用覆盖率：{summary['citation_rate']['mean'] * 100:.1f}%",
        f"- 自检通过率：{summary['selfcheck_pass_rate']['mean'] * 100:.1f}%",
        f"- 整体通过率：{summary['overall_pass_rate']['mean'] * 100:.1f}%",
        f"- **Pass^{n}（连续 {n} 次全部通过）：{summary['pass_pow_k_rate'] * 100:.1f}%**",
        f"- 平均延迟：{summary['avg_latency_ms']} ms",
        "",
        "## 边界集 / 保留集",
        "",
        "| 集合 | 用例数 | 通过率 | 说明 |",
        "|---|---|---|---|",
    ]
    set_desc = {"boundary": "本轮专门修正的边界用例", "retention": "本轮不应改变、须逐字节保持的用例"}
    for s, info in summary["by_set"].items():
        lines.append(f"| {s} | {info['count']} | {info['pass_rate_mean'] * 100:.1f}% | {set_desc.get(s, '')} |")
    lines += ["", "边界集用例：" + "、".join(summary["by_set"].get("boundary", {}).get("ids", [])) + "（"
              + summary["by_set"].get("boundary", {}).get("count", 0).__str__() + " 条）", ""]

    lines += [
        "## 明细（最后一轮）",
        "",
        "| # | 集合 | 问题 | 验证器 | 意图 | 事实 | 引用 | 自检 | 通过 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for v in last:
        lines.append(
            f"| {v['id']} | {v['set']} | {v['question']} | {v['verifier']} | "
            f"{'✓' if v['intent_ok'] else '✗'} | "
            f"{v['keyword_hit']}/{v['keyword_total']} | "
            f"{'✓' if v['citation_ok'] else '✗'} | "
            f"{'✓' if v['selfcheck_ok'] else '✗'} | {'✓' if v['pass'] else '✗'} |"
        )

    lines += ["", "## 失败归因（首个错误，非最后报错）", ""]
    if attributions:
        for a in attributions:
            lines.append(
                f"- `{a['case_id']}` **{a['category']}**（责任方 {a['owner']}）："
                f"{a['root_cause']}。首错步 {a['first_error_step']}：{a['first_error_detail']}"
            )
    else:
        lines.append("- 本轮无失败用例（`evals/attributions.json` 为空数组）。")
        lines.append("- 已修复并固化为回归用例的历史失败：`H07` 曾被模糊命中到「消耗合计」模板，"
                     "返回全库数字 1,464,090。根因归类为 **口径边界未拦截**（责任方 harness/tool），"
                     "现已由 refusal 验证器 + 轨迹前缀 PFX-01/04 守住，见 `cases.jsonl` 的 note 字段与 `test_eval_framework.py`。")

    lines += ["", "## 轨迹前缀回归（冻结上下文，只看下一步动作）", "",
              "| # | 场景 | 实际动作 | 可接受集合 | 通过 |", "|---|---|---|---|---|"]
    for p in prefix_results:
        lines.append(f"| {p['id']} | {p['scenario']} | {p['action']} | "
                     f"{'/'.join(p['acceptable'])} | {'✓' if p['pass'] else '✗'} |")

    with open(os.path.join(ROOT, "evals", "report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", action="store_true", help="使用在线大模型")
    parser.add_argument("--repeats", type=int, default=3, help="重复运行轮次（用于 Pass^k 与置信区间）")
    args = parser.parse_args()
    sys.exit(run(args.llm, max(1, args.repeats)))

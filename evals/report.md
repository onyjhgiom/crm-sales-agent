# 评估报告（结构化验证）

## 汇总

- 模式：offline
- 用例数：37　重复轮次：3
- 意图准确率：100.0% ± 0.0（95% CI）
- 关键事实命中率：100.0% ± 0.0
- 引用覆盖率：100.0%
- 自检通过率：100.0%
- 整体通过率：100.0%
- **Pass^3（连续 3 次全部通过）：100.0%**
- 平均延迟：50.3 ms

## 边界集 / 保留集

| 集合 | 用例数 | 通过率 | 说明 |
|---|---|---|---|
| boundary | 2 | 100.0% | 本轮专门修正的边界用例 |
| retention | 35 | 100.0% | 本轮不应改变、须逐字节保持的用例 |

边界集用例：A07、H07（2 条）

## 明细（最后一轮）

| # | 集合 | 问题 | 验证器 | 意图 | 事实 | 引用 | 自检 | 通过 |
|---|---|---|---|---|---|---|---|---|
| P01 | retention | A001 帮我出个客户画像 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| P02 | retention | A003 这个客户什么情况 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| P03 | retention | A006 的画像 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| P04 | retention | A011 客户档案看一下 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| P05 | retention | A030 什么情况 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| O01 | retention | A003 现在有什么商机 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| O02 | retention | A006 该不该谈增投 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| O03 | retention | A011 怎么跟进 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| O04 | retention | A030 有什么机会 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| O05 | retention | A026 下一步做什么 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| O06 | retention | 今天该优先跟进哪些客户 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| O07 | retention | 给我个优先跟进名单 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| O08 | retention | 哪些客户最该先打电话 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| A01 | retention | 各行业消耗汇总 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| A02 | retention | 转化率最低的10个客户 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| A03 | retention | 哪些客户套餐快到期 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| A04 | retention | ROAS最高的客户是谁 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| A05 | retention | 消耗下滑超过20%的客户有哪些 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| A06 | retention | 预算利用率最低的客户 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| A07 | boundary | 全库消耗合计多少 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| A08 | retention | 哪些客户有负面反馈 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| K01 | retention | 什么情况下可以谈增投 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| K02 | retention | 套餐快到期怎么谈续费 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| K03 | retention | 客户流失有哪些预警信号 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| K04 | retention | 中小客户怎么分层运营 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| K05 | retention | 预算利用率低怎么办 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| K06 | retention | 续费报价有什么红线 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| K07 | retention | CVR下滑应该怎么排查 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| S01 | retention | 你能做什么 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| S02 | retention | 你好 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| H01 | retention | A003 这个月比上个月好还是差 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| H02 | retention | A003 和 A011 谁的 ROAS 更高 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| H03 | retention | A024、A013、A030 这三家谁的 ROAS 最低 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| H04 | retention | A011 的 CVR 这个月比上个月涨了还是跌了 | keywords | ✓ | 2/2 | ✓ | ✓ | ✓ |
| H05 | retention | A012 和 A014 谁的转化成本更高 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| H06 | retention | A003 最近三个月的 ROAS 走势 | keywords | ✓ | 1/1 | ✓ | ✓ | ✓ |
| H07 | boundary | A003 近45天消耗多少 | refusal | ✓ | 1/1 | ✓ | ✓ | ✓ |

## 失败归因（首个错误，非最后报错）

- 本轮无失败用例（`evals/attributions.json` 为空数组）。
- 已修复并固化为回归用例的历史失败：`H07` 曾被模糊命中到「消耗合计」模板，返回全库数字 1,464,090。根因归类为 **口径边界未拦截**（责任方 harness/tool），现已由 refusal 验证器 + 轨迹前缀 PFX-01/04 守住，见 `cases.jsonl` 的 note 字段与 `test_eval_framework.py`。

## 轨迹前缀回归（冻结上下文，只看下一步动作）

| # | 场景 | 实际动作 | 可接受集合 | 通过 |
|---|---|---|---|---|
| PFX-01 | 自定义时间窗口消耗查询 | refuse_with_scope | refuse_with_scope/explain_supported_windows | ✓ |
| PFX-02 | 合法30天口径不应被误伤 | query_with_template | query_with_template | ✓ |
| PFX-03 | 月度走势跨月查询应放行 | query_monthly_metrics | query_monthly_metrics | ✓ |
| PFX-04 | 追问继承客户后遇自定义窗口 | refuse_with_scope | refuse_with_scope/explain_supported_windows | ✓ |

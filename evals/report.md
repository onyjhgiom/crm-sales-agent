# 评估报告

- 模式：offline
- 用例数：37
- 意图准确率：100.0%
- 关键事实命中率：97.3%
- 引用覆盖率：100.0%
- 自检通过率：100.0%
- 平均延迟：7.9 ms

## 明细

| # | 问题 | 期望意图 | 实际意图 | 关键词 | 引用 | 自检 | 延迟(ms) |
|---|---|---|---|---|---|---|---|
| P01 | A001 帮我出个客户画像 | profile | profile | 2/2 | 0 | ✓ | 17.4 |
| P02 | A003 这个客户什么情况 | profile | profile | 2/2 | 0 | ✓ | 4.4 |
| P03 | A006 的画像 | profile | profile | 2/2 | 0 | ✓ | 4.9 |
| P04 | A011 客户档案看一下 | profile | profile | 1/1 | 0 | ✓ | 4.9 |
| P05 | A030 什么情况 | profile | profile | 2/2 | 0 | ✓ | 4.8 |
| O01 | A003 现在有什么商机 | opportunity | opportunity | 2/2 | 3 | ✓ | 5.7 |
| O02 | A006 该不该谈增投 | opportunity | opportunity | 1/1 | 3 | ✓ | 4.4 |
| O03 | A011 怎么跟进 | opportunity | opportunity | 2/2 | 3 | ✓ | 4.6 |
| O04 | A030 有什么机会 | opportunity | opportunity | 1/1 | 3 | ✓ | 5.2 |
| O05 | A026 下一步做什么 | opportunity | opportunity | 1/1 | 3 | ✓ | 5.3 |
| O06 | 今天该优先跟进哪些客户 | opportunity | opportunity | 2/2 | 0 | ✓ | 37.7 |
| O07 | 给我个优先跟进名单 | opportunity | opportunity | 1/1 | 0 | ✓ | 36.4 |
| O08 | 哪些客户最该先打电话 | opportunity | opportunity | 1/1 | 0 | ✓ | 37.1 |
| A01 | 各行业消耗汇总 | analysis | analysis | 2/2 | 0 | ✓ | 5.5 |
| A02 | 转化率最低的10个客户 | analysis | analysis | 2/2 | 0 | ✓ | 5.6 |
| A03 | 哪些客户套餐快到期 | analysis | analysis | 1/1 | 0 | ✓ | 5.8 |
| A04 | ROAS最高的客户是谁 | analysis | analysis | 2/2 | 0 | ✓ | 4.6 |
| A05 | 消耗下滑超过20%的客户有哪些 | analysis | analysis | 1/1 | 0 | ✓ | 5.6 |
| A06 | 预算利用率最低的客户 | analysis | analysis | 1/1 | 0 | ✓ | 4.8 |
| A07 | 全库消耗合计多少 | analysis | analysis | 1/1 | 0 | ✓ | 4.9 |
| A08 | 哪些客户有负面反馈 | analysis | analysis | 1/1 | 0 | ✓ | 5.6 |
| K01 | 什么情况下可以谈增投 | knowledge | knowledge | 1/1 | 3 | ✓ | 4.2 |
| K02 | 套餐快到期怎么谈续费 | knowledge | knowledge | 1/1 | 3 | ✓ | 5.1 |
| K03 | 客户流失有哪些预警信号 | knowledge | knowledge | 1/1 | 2 | ✓ | 4.3 |
| K04 | 中小客户怎么分层运营 | knowledge | knowledge | 1/1 | 2 | ✓ | 5.0 |
| K05 | 预算利用率低怎么办 | knowledge | knowledge | 1/1 | 3 | ✓ | 6.2 |
| K06 | 续费报价有什么红线 | knowledge | knowledge | 1/1 | 3 | ✓ | 4.4 |
| K07 | CVR下滑应该怎么排查 | knowledge | knowledge | 1/1 | 3 | ✓ | 6.1 |
| S01 | 你能做什么 | smalltalk | smalltalk | 2/2 | 0 | ✓ | 5.1 |
| S02 | 你好 | smalltalk | smalltalk | 1/1 | 0 | ✓ | 3.8 |
| H01 | A003 这个月比上个月好还是差 | analysis | analysis | 1/1 | 0 | ✓ | 5.1 |
| H02 | A003 和 A011 谁的 ROAS 更高 | analysis | analysis | 1/1 | 0 | ✓ | 4.2 |
| H03 | A024、A013、A030 这三家谁的 ROAS 最低 | analysis | analysis | 2/2 | 0 | ✓ | 5.8 |
| H04 | A011 的 CVR 这个月比上个月涨了还是跌了 | analysis | analysis | 2/2 | 0 | ✓ | 4.1 |
| H05 | A012 和 A014 谁的转化成本更高 | analysis | analysis | 1/1 | 0 | ✓ | 5.0 |
| H06 | A003 最近三个月的 ROAS 走势 | analysis | analysis | 1/1 | 0 | ✓ | 4.8 |
| H07 | A003 近45天消耗多少 | analysis | analysis | 0/1 | 0 | ✓ | 4.0 |

## 未通过用例（真实记录，不修饰）

- `H07` A003 近45天消耗多少：期望 analysis，实际 analysis，关键词 0/1

# AGENTS.md — Implemented Agent Notes

<!-- EN -->
These Agent Notes describe shipped decisions. Follow the [root instructions](../../../AGENTS.md) and the [Agent Note format](../README.md#文件内格式); a reviewer checks the lifecycle-specific structure.

<!-- 中文 -->
这些 Agent Note 记录已发布的决策。遵循[根目录总纲](../../../AGENTS.md)与 [Agent Note 格式规范](../README.md#文件内格式)；审查者会检查符合生命周期的章节结构。

> Both languages are authoritative; English comes first (`<!-- EN -->`), Chinese follows (`<!-- 中文 -->`). Headings stay in English so anchors such as `#文件内格式` in the notes README remain untouched. / 两种语言同等有效；英文在前，中文在后；标题保留英文以不破坏 notes README 中的锚点。

## Keep an implemented Agent Note current with what actually shipped / 已实现笔记须与实际上线内容保持一致

<!-- EN -->
Keep paths, symbols, defaults, and mechanisms current in the same change that alters them. Rewrite stale facts in place; do not append change history. When a shipped note is unlikely to guide future work, archive it per the [README](../README.md#归档与删除) instead of maintaining it indefinitely.

<!-- 中文 -->
当代码改变路径、符号、默认值或机制时，须在同一改动里让笔记保持最新。就地改写陈旧事实，不要追加变更历史。若某条已发布笔记不太可能再指导未来工作，按 [README](../README.md#归档与删除) 归档，而不是无限期维护。

### This is not a license to rewrite the decision / 这不等于可以重写决策本身

<!-- EN -->
Update the factual realization in place. A reversal of the decision or its rationale requires a new Agent Note with a cross-link; a fully superseded old note may be deleted only through the consolidation rules in the [Agent Note rules](../README.md).

<!-- 中文 -->
就地更新事实层面的实现。若要推翻决策或其理由，须新建一条 Agent Note 并交叉链接；被完全取代的旧笔记只能按 [Agent Note 规范](../README.md)中的合并规则删除。

## Overlap with design-notes.md / 与 design-notes.md 的重叠

<!-- EN -->
`docs/design-notes.md` is the canonical home for the "why + alternative + what we gave up" of 31 shipped choices. Before writing a new implemented note, check whether the decision is already covered there; link to it instead of duplicating. An Agent Note here earns its place when a decision needs its own status lifecycle, an explicit alternatives ledger, or a negative guarantee that must survive across unrelated changes.

<!-- 中文 -->
`docs/design-notes.md` 是 31 条已落地选择的"为什么 + 备选方案 + 放弃了什么"的权威归属。写新的 implemented 笔记前，先检查该决策是否已被那里覆盖；用链接代替复制。只有当某决策需要独立的状态生命周期、显式的备选清单，或必须跨无关改动存续的负面保证时，这里的 Agent Note 才有存在价值。

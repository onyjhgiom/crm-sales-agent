# AGENTS.md — Implemented Agent Notes

These Agent Notes describe shipped decisions. Follow the [root instructions](../../../AGENTS.md) and the [Agent Note format](../README.md#文件内格式); a reviewer checks the lifecycle-specific structure.

## Keep an implemented Agent Note current with what actually shipped

Keep paths, symbols, defaults, and mechanisms current in the same change that alters them. Rewrite stale facts in place; do not append change history. When a shipped note is unlikely to guide future work, archive it per the [README](../README.md#归档与删除) instead of maintaining it indefinitely.

### This is not a license to rewrite the decision

Update the factual realization in place. A reversal of the decision or its rationale requires a new Agent Note with a cross-link; a fully superseded old note may be deleted only through the consolidation rules in the [Agent Note rules](../README.md).

## Overlap with design-notes.md

`docs/design-notes.md` is the canonical home for the "why + alternative + what we gave up" of 31 shipped choices. Before writing a new implemented note, check whether the decision is already covered there; link to it instead of duplicating. An Agent Note here earns its place when a decision needs its own status lifecycle, an explicit alternatives ledger, or a negative guarantee that must survive across unrelated changes.

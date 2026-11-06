---
name: kg-review
description: Review relevant principles, patterns, debug records, and research entries from this repository's local knowledge graph before design, refactor, or incident-analysis work.
---

# KG Review

Use this skill to quickly inspect what the local knowledge graph already says about a topic.

## When to use
- Before refactoring or cleanup
- Before architecture/design changes
- Before debugging a recurring issue
- Before writing docs or summaries that should reflect prior conclusions

## Workflow
1. Pick the review topic.
2. Run:

```bash
python integrations/omx/kg_skill.py review "<topic>" --top-k 8
```

3. Read the grouped results (`principle`, `pattern`, `debug`, `architecture`, `research`).
4. Reuse or cite prior conclusions instead of rediscovering them.

## Notes
- This skill is **read-only**.
- Use it as a pre-flight check before broad reasoning or implementation tasks.

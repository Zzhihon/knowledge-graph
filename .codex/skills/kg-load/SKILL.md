---
name: kg-load
description: Load concise context from this repository's local knowledge graph before planning, debugging, architecture review, or implementation work. Use when the task may benefit from previously captured principles, patterns, debug notes, or research entries.
---

# KG Load

Use this skill to pull a **small, read-only context pack** from the local knowledge graph.

## When to use
- Before architecture / debugging / planning work
- When the user asks about a technical topic likely already captured in the vault
- When you want project-local knowledge before using web search

## Workflow
1. Turn the task into a short topic string.
2. Run:

```bash
python integrations/omx/kg_skill.py load "<topic>" --top-k 5
```

3. Use the returned summary, not the full raw notes, unless you need to drill deeper.
4. If results are weak, retry once with narrower keywords or a `--domain`.

## Notes
- This skill is **read-only**.
- Prefer citing entry ids / file paths when you use the loaded context.
- If you need a synthesized answer instead of raw context, switch to `kg-ask`.

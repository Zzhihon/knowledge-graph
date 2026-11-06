---
name: kg-capture
description: Produce a proposal-only knowledge capture draft from the current task's stable conclusions, without writing anything into the knowledge graph. Use when a session yields reusable knowledge and you want a clean candidate before explicit ingest.
---

# KG Capture

Use this skill to turn a stable conclusion into a **proposal-only** knowledge draft.

## When to use
- After finishing debugging, design, or implementation work with a reusable lesson
- When the session produced a principle, pattern, or incident takeaway worth preserving
- When you want a draft before deciding whether it should enter the knowledge graph

## Workflow
1. Distill the conclusion into a short subject.
2. Add 1-5 lines of supporting notes.
3. Run:

```bash
python integrations/omx/kg_skill.py capture "<subject>" --notes "<notes>" --top-k 5
```

4. Review the proposal:
   - suggested domain
   - related entries
   - draft structure
5. Only if the proposal looks worthwhile, explicitly run the ingest/integrate path later.

## Guardrails
- This skill is **proposal-only**.
- It does **not** write Markdown, modify the vault, or trigger sync.
- Do not use it for noisy intermediate reasoning; use it only for stable conclusions.

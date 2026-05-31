---
name: kg-ask
description: Ask this repository's local knowledge graph a question and get a grounded answer from the local RAG pipeline. Use when you want an answer based on previously captured knowledge, without enabling fallback auto-ingest.
---

# KG Ask

Use this skill when you want a **knowledge-graph-grounded answer** instead of a generic model answer.

## Workflow
1. If the topic is broad, optionally run `kg-load` first.
2. Ask the local knowledge graph:

```bash
python integrations/omx/kg_skill.py ask "<question>" --top-k 5
```

3. Use the answer plus the emitted source list.

## Guardrails
- This skill is **read-only**.
- It explicitly disables fallback auto-ingest to avoid writing noisy session output into the knowledge base.
- If the graph has no answer, say so plainly instead of forcing a generic response through this skill.

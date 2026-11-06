---
name: kg-load
description: Load concise context from this repository's local knowledge graph before planning, debugging, architecture review, or implementation work. Use when the task may benefit from previously captured principles, patterns, debug notes, research entries, or the current state of a project.
---

# KG Load

Use this skill to pull a **small, read-only context pack** from the local knowledge graph.

## When to use
- Before architecture / debugging / planning work
- When the user asks about a technical topic likely already captured in the vault
- When resuming work on a project ("where did we stop?")
- When you want project-local knowledge before using web search

## Workflow (MCP first)

When the `kg` MCP server is connected (`kg mcp serve`, tools named `kg.*` / `mcp__kg__*`):

1. Turn the task into a short topic string.
2. Call `search_knowledge(query=<topic>, limit=5)`. Narrow with `board` (`项目` / `通识` / `经验` / `实验` / `排障`) when the task is clearly one kind.
3. Call `get_entry(id)` only for the 1–3 hits you really need in full.
4. If the topic is a project, also call `project_status(project)`; for an error or outage, call `similar_incidents(text)`.
5. If a result has `error`, report `error.message` and `error.hint` as they are (for example "index_stale: 运行 kg index update"). Do not treat it as "nothing found".

## Fallback (MCP not available)

If the `kg` tools are not listed, or a call fails because the server is not running, use the CLI with the repository's Python (`.venv/bin/python` in the main checkout):

```bash
python integrations/omx/kg_skill.py load "<topic>" --top-k 5
```

Then use the returned summary, not the full raw notes, unless you need to drill deeper. If results are weak, retry once with narrower keywords or a `--domain`.

## Notes
- This skill is **read-only**. The MCP tools are read-only too.
- Prefer citing entry ids / file paths when you use the loaded context.
- If you need a synthesized answer instead of raw context, switch to `kg-ask`.

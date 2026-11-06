---
name: kg-ask
description: Ask this repository's local knowledge graph a question and get a grounded answer. Use when you want an answer based on previously captured knowledge or on the local control plane (project progress, sessions, service and egress health, artifacts, past incidents), without enabling fallback auto-ingest.
---

# KG Ask

Use this skill when you want a **knowledge-graph-grounded answer** instead of a generic model answer.

## Workflow (MCP first)

When the `kg` MCP server is connected (`kg mcp serve`, tools named `kg.*` / `mcp__kg__*`), pick the tool by the question:

| Question | Tool |
|---|---|
| How far is project X / where did we stop / uncommitted work / open PRs | `project_status(project)` |
| What happened recently, which sessions wait for me | `recent_sessions(project?, hours?)` |
| How did we do / fix X before, background knowledge | `search_knowledge(query, board?)`, then `get_entry(id)` |
| Is the proxy egress / a local service up, any open incidents | `plane_health()` |
| Where is that plan page / report / share link | `find_artifact(query)` |
| Has this error or outage happened before | `similar_incidents(text)` |

Answer only from what the tools return, and cite entry ids or paths. If a result has `error`, report `error.message` and `error.hint` as they are. If it has `warning` (for example the board was not collected recently), say the data may be out of date.

## Fallback (MCP not available)

If the `kg` tools are not listed, or a call fails because the server is not running, ask through the CLI with the repository's Python (`.venv/bin/python` in the main checkout):

```bash
python integrations/omx/kg_skill.py ask "<question>" --top-k 5
```

Use the answer plus the emitted source list. For project progress without MCP, read the project page in `07-Projects/` and `GET /api/plane/projects` when `kg serve` is running.

## Guardrails
- This skill is **read-only**.
- The CLI path explicitly disables fallback auto-ingest to avoid writing noisy session output into the knowledge base.
- If the graph has no answer, say so plainly instead of forcing a generic response through this skill.

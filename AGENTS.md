# Knowledge Graph Project Guidance

This file applies to the repository root and everything beneath it.

## Knowledge-graph skill routing

- For architecture, debugging, planning, refactor, or incident-analysis tasks that may benefit from prior local knowledge, prefer using the project skills **`kg-load`** or **`kg-review`** before broader search.
- For direct questions about previously captured local knowledge, prefer **`kg-ask`** over generic answering when a grounded local answer is useful.
- When the **`kg` MCP server** is connected (`kg mcp serve`), call its read-only tools directly: `project_status` for "how far is project X / where did we stop", `recent_sessions` for recent work, `plane_health` for egress / local services / open incidents, `find_artifact` for plan pages and share links, `similar_incidents` for errors seen before, `search_knowledge` + `get_entry` for captured knowledge. `kg-load` / `kg-ask` use these tools first and fall back to the CLI.
- For knowledge capture, use **`kg-capture`** only to create a **proposal-only** draft from stable conclusions.
- Do **not** write new knowledge entries or trigger ingest/integrate automatically unless the user explicitly asks for that behavior.
- The one standing exception is the automatic capture pipeline the owner set up (`docs/auto-capture.md`): runs dispatched by agent-autoloop with the `kg-distill` or `kg-daily` skill write entries, project pages and daily briefs following `docs/knowledge-schema.md`. Interactive sessions stay proposal-only.

## Integration direction

- Treat OMX as the workflow/orchestration layer and this repository as the knowledge capability layer.
- Prefer **Skill → MCP → Hook** in that order when extending OMX integration.
- Keep automatic context loading concise; avoid injecting large note bodies into working context by default.

# Knowledge Graph Project Guidance

This file applies to the repository root and everything beneath it.

## Knowledge-graph skill routing

- For architecture, debugging, planning, refactor, or incident-analysis tasks that may benefit from prior local knowledge, prefer using the project skills **`kg-load`** or **`kg-review`** before broader search.
- For direct questions about previously captured local knowledge, prefer **`kg-ask`** over generic answering when a grounded local answer is useful.
- For knowledge capture, use **`kg-capture`** only to create a **proposal-only** draft from stable conclusions.
- Do **not** write new knowledge entries or trigger ingest/integrate automatically unless the user explicitly asks for that behavior.

## Integration direction

- Treat OMX as the workflow/orchestration layer and this repository as the knowledge capability layer.
- Prefer **Skill → MCP → Hook** in that order when extending OMX integration.
- Keep automatic context loading concise; avoid injecting large note bodies into working context by default.

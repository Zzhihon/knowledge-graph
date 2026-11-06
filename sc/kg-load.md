---
name: kg-load
description: "Load relevant knowledge context from the vault at session start based on topic or project"
category: knowledge
complexity: standard
mcp-servers: [kg]
personas: []
---

# /sc:kg-load - Knowledge Context Loading

## Triggers
- Starting a new session that relates to a previously explored topic
- Resuming work on a known project
- Needing prior knowledge context before diving into a task
- Onboarding into a topic area where vault entries exist

## Usage
```
/sc:kg-load <topic or project name>
```

## Behavioral Flow

1. **Parse Topic**: Extract the topic or project name from `$ARGUMENTS`.
   - If empty, prompt the user: "What topic or project should I load context for?"
   - Normalize keywords for search (e.g., "golang gc" -> search for "golang", "gc", "garbage collection")

2. **MCP First (`kg` server, `kg mcp serve`)**: When the `mcp__kg__*` tools are available, use them before anything else.
   - `mcp__kg__search_knowledge(query="$ARGUMENTS", limit=10)` — local retrieval (jieba BM25 + vectors, RRF). Add `board` (`项目` / `通识` / `经验` / `实验` / `排障`) to narrow; `rerank=true` only when ranking really matters (it takes 10+ seconds).
   - `mcp__kg__get_entry(id)` for the hits you read in full (step 3), instead of opening files.
   - If the topic names a project: `mcp__kg__project_status(project)` gives the same state as the 进行中 board (stage, last conclusion, live sessions, uncommitted changes, open PRs, project page summary). Use it for step 5.
   - If the topic is an error or outage: `mcp__kg__similar_incidents(text)`.
   - If a result carries `error` (`index_missing`, `index_stale`, `embedding_failed`, ...), show `error.message` and `error.hint` to the user as they are; do not present it as "no knowledge found".

   **Fallback when the MCP tools are not listed or the server does not start** — the previous path:
     ```bash
     cd <repo> && python integrations/omx/kg_skill.py load "$ARGUMENTS" --top-k 10
     ```
   - Equivalent CLI command when JSON is needed:
     ```bash
     cd <repo> && kg query "$ARGUMENTS" --top-k 10 --format json
     ```
   - Both go through the same local retrieval (`kg.retrieval`, #7) and fail with the same error codes.
   - If the command reports that the index is missing or stale, run `kg index update` (or `kg index rebuild`) or ask the user before continuing if it would be expensive.
   - Keep the returned ranking and scores visible in the briefing so gaps are auditable.

3. **Read Semantic Hits**: Read the Markdown files returned by semantic search using their `file_path` values.
   - Default: read up to 8-10 semantic hits when the task is planning, architecture, debugging, or project-context sensitive.
   - Do not cap at 3-5 for planning tasks; missing one architecture/debug entry can materially change the plan.
   - Deduplicate by entry ID/path before reading.

4. **Lexical Fallback and Supplement**: Use Grep/Glob only after semantic retrieval:
   - As a fallback if local retrieval fails (MCP and CLI both).
   - As a supplement for exact project names, acronyms, filenames, or identifiers that may not embed well.
   - Search paths:
     - `<repo>/01-Principles/`
     - `<repo>/02-Patterns/`
     - `<repo>/03-Debug/`
     - `<repo>/04-Architecture/`
     - `<repo>/05-Research/`
     - `<repo>/06-Team/`
     - `<repo>/08-Problems/`
     - `<repo>/09-Interview/`
   - Add lexical-only hits to the context only when they are clearly relevant.

5. **Search Project Tracking (07-Projects/)**: Skip this step when `mcp__kg__project_status` answered in step 2. Otherwise use Grep and Glob to search project notes:
   - Search path: `<repo>/07-Projects/`
   - Look for files matching the topic/project name.
   - Extract: current status, recent decisions, blockers, next steps, open questions.
   - Project notes are not part of the normal vector index, so this step is required even when semantic search succeeds.

6. **Present Context Briefing**: Output a structured summary with the following sections:

```markdown
## Knowledge Context: {topic}

### Related Knowledge
| Entry | Type | Domain | Depth | Confidence |
|-------|------|--------|-------|------------|
| {title} | {type} | {domain} | {depth} | {confidence} |

**Entry Summaries:**
- **{entry title}**: {1-2 sentence summary of the core insight from the Question and Key Insights sections}
- ...

### Project State
> {If project notes found in 07-Projects/}
- **Status**: {current project status}
- **Recent Decisions**: {key decisions from notes}
- **Blockers**: {active blockers or impediments}
- **Next Steps**: {planned next actions}

> {If no project notes found}
> No project tracking notes found for "{topic}" in 07-Projects/.

### Suggested Focus
- {Based on knowledge gaps: topics mentioned in entries but lacking their own entry}
- {Based on low-confidence entries: entries with confidence < 0.6 that need validation}
- {Based on project next steps: if project notes exist, highlight the most actionable next step}
- {Based on review dates: entries past their review_date that are relevant to this topic}
```

7. **Context Availability**: After presenting the briefing, this context remains available for the rest of the conversation. Reference loaded entries by their IDs or titles in subsequent work.

## Key Paths
- Vault root: `<repo>/`
- Knowledge dirs: `01-Principles/`, `02-Patterns/`, `03-Debug/`, `04-Architecture/`, `05-Research/`, `06-Team/`
- Project dir: `07-Projects/`

## Examples

### Load context for a specific technology
```
/sc:kg-load golang goroutine scheduling
# Searches all knowledge dirs for entries about goroutine scheduling
# Presents related principles, patterns, and debug entries
```

### Load context for a project
```
/sc:kg-load AutoBits
# Searches knowledge dirs for AutoBits-related entries
# Loads project tracking state from 07-Projects/
# Presents full context briefing with project status
```

### Load context for a broad domain
```
/sc:kg-load distributed-systems
# Finds all entries tagged with distributed-systems domain
# Summarizes coverage across principles, patterns, architecture
```

## Boundaries

**Will:**
- Search the vault through the `kg` MCP tools first, then the local hybrid retrieval CLI
- Use Grep/Glob as fallback and exact-match supplement, not as the primary ranking mechanism
- Read and summarize the most relevant entries for immediate context
- Present project state from 07-Projects/ when available
- Identify knowledge gaps and suggest focus areas
- Make loaded context available for the rest of the session

**Will Not:**
- Modify or update any vault entries (use `/sc:kg` or `/sc:kg-review` for that)
- Fabricate knowledge not present in the vault
- Rely only on lexical grep when semantic search is available
- Search outside the knowledge-graph vault directories

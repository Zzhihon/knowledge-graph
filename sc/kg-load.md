---
name: kg-load
description: "Load relevant knowledge context from the vault at session start based on topic or project"
category: knowledge
complexity: standard
mcp-servers: []
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

2. **Search Knowledge Entries (01-06)**: Use Grep to search across the knowledge vault directories for entries matching the topic keywords:
   - Search paths:
     - `/Users/bt1q/Github-Projects/knowledge-graph/01-Principles/`
     - `/Users/bt1q/Github-Projects/knowledge-graph/02-Patterns/`
     - `/Users/bt1q/Github-Projects/knowledge-graph/03-Debug/`
     - `/Users/bt1q/Github-Projects/knowledge-graph/04-Architecture/`
     - `/Users/bt1q/Github-Projects/knowledge-graph/05-Research/`
     - `/Users/bt1q/Github-Projects/knowledge-graph/06-Team/`
   - Search strategy:
     - First pass: Grep for topic keywords in file contents (titles, questions, tags, domain fields)
     - Second pass: Glob for filenames containing topic slugs
   - Rank results by relevance: entries where the keyword appears in `title`, `domain`, or `tags` frontmatter rank higher than body mentions
   - Select the top 3-5 most relevant entries

3. **Search Project Tracking (07-Projects/)**: Use Grep and Glob to search for project notes:
   - Search path: `/Users/bt1q/Github-Projects/knowledge-graph/07-Projects/`
   - Look for files matching the topic/project name
   - Extract: current status, recent decisions, blockers, next steps, open questions

4. **Read Matching Entries**: Read the top matching files from both searches using the Read tool.

5. **Present Context Briefing**: Output a structured summary with the following sections:

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

6. **Context Availability**: After presenting the briefing, this context remains available for the rest of the conversation. Reference loaded entries by their IDs or titles in subsequent work.

## Key Paths
- Vault root: `/Users/bt1q/Github-Projects/knowledge-graph/`
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
- Search the vault thoroughly using multiple search strategies (content grep, filename glob)
- Read and summarize the most relevant entries for immediate context
- Present project state from 07-Projects/ when available
- Identify knowledge gaps and suggest focus areas
- Make loaded context available for the rest of the session

**Will Not:**
- Modify or update any vault entries (use `/sc:kg` or `/sc:kg-review` for that)
- Fabricate knowledge not present in the vault
- Load more than 5 entries to avoid context overload
- Search outside the knowledge-graph vault directories

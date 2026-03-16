---
name: kg-ingest
description: "Ingest external documents or exported conversations into the knowledge graph"
category: knowledge
complexity: standard
mcp-servers: []
personas: []
---

# /sc:kg-ingest - Knowledge Graph Ingestion

## Usage
```
/sc:kg-ingest <file_path> [--dry-run] [--team <team_name>]
```

## Behavioral Flow

1. **Read Source**: Read the specified file (conversation export, markdown notes, technical document)
2. **Extract**: Use the same extraction logic as `/sc:kg` to identify knowledge-worthy content
3. **Classify & Write**: Generate structured entries to the vault
4. **Report**: Summary of ingested entries

## Implementation
Delegates to the `kg` CLI tool:
```bash
cd /Users/bt1q/Github-Projects/knowledge-graph && .venv/bin/kg ingest <file_path> [--dry-run]
```

If the CLI `kg ingest` fails or produces unsatisfactory results, fall back to the `/sc:kg` extraction logic applied to the file content directly.

## Arguments
- `$ARGUMENTS` must contain a file path as the first positional argument
- `--dry-run`: Preview extraction without writing
- `--team <name>`: Mark all entries as team knowledge

## Examples
```
/sc:kg-ingest /path/to/conversation-export.md
/sc:kg-ingest ./notes/deep-dive-raft.md --dry-run
/sc:kg-ingest ./meeting-notes.md --team AutoBits
```

## Boundaries

**Will:**
- Read and parse various document formats (markdown, text)
- Extract and structure knowledge entries
- Write to the appropriate vault directories

**Will Not:**
- Process binary files or images
- Ingest content without quality filtering
- Create entries from shallow or trivial content

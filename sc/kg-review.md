---
name: kg-review
description: "Run knowledge graph review cycle to detect outdated, low-confidence, and missing entries"
category: knowledge
complexity: standard
mcp-servers: []
personas: []
---

# /sc:kg-review - Knowledge Graph Review

## Usage
```
/sc:kg-review [--domain <domain>] [--fix] [--gaps]
```

## Behavioral Flow

1. **Scan**: Run the `kg review` CLI command to identify:
   - Entries updated > 180 days ago (potentially outdated)
   - Entries with confidence < 0.6 (needs validation)
   - Entries still in "draft" status (needs completion)

2. **Gap Analysis**: Compare existing entries against the domain definitions in `config.yaml`:
   - Which sub-domains have zero coverage?
   - Which domains are underdeveloped relative to the user's skill profile?
   - Suggest specific topics that should be documented

3. **Report**: Present findings as a structured review:
   - Outdated entries table with suggested actions
   - Low-confidence entries that need re-validation
   - Domain coverage heatmap
   - Priority recommendations for knowledge gaps

4. **Fix** (if `--fix` flag):
   - For outdated entries: read the entry, assess if content is still accurate given current knowledge, update `status` and `updated` fields
   - For draft entries: attempt to complete missing sections based on available context
   - Generate a review-note entry in the vault using the review-note template

## Key Paths
- Vault root: `/Users/bt1q/Github-Projects/knowledge-graph/`
- CLI: Run via `cd /Users/bt1q/Github-Projects/knowledge-graph && .venv/bin/kg review`
- Review notes: Write to vault root as `review-YYYY-MM-DD.md`

## Examples
```
/sc:kg-review                    # Full review
/sc:kg-review --domain golang    # Review only Golang entries
/sc:kg-review --gaps             # Focus on knowledge gap analysis
/sc:kg-review --fix              # Auto-fix outdated and draft entries
```

## Boundaries

**Will:**
- Identify knowledge health issues and coverage gaps
- Generate actionable review reports
- Update entry metadata (status, dates) when `--fix` is used

**Will Not:**
- Delete entries without explicit confirmation
- Fabricate content to fill knowledge gaps
- Change entry analysis content without user review

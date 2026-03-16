---
name: kg-export
description: "Export knowledge graph entries into formatted documents (blog posts, study guides, onboarding docs)"
category: knowledge
complexity: standard
mcp-servers: []
personas: []
---

# /sc:kg-export - Knowledge Graph Export

## Triggers
- Need to produce a shareable document from accumulated knowledge
- Blog post drafting from deep technical entries
- Study guide generation for learning paths
- Team onboarding documentation creation

## Usage
```
/sc:kg-export --format <blog|study-guide|onboarding> --domain <domain> [--depth <deep|intermediate|surface>] [--output <path>]
```

## Arguments
- `--format <blog|study-guide|onboarding>`: Required. The output document format.
- `--domain <domain>`: Required. Filter entries by domain (e.g., `golang`, `distributed-systems`, `databases`).
- `--depth <deep|intermediate|surface>`: Optional. Filter entries by depth level. If omitted, include all depths.
- `--output <path>`: Optional. File path to write the generated document. If omitted, display the document in the conversation.

## Behavioral Flow

1. **Load Entries**: Search and load all matching entries from the vault:
   - Use Grep to find entries where `domain` frontmatter matches `--domain`
   - If `--depth` is specified, further filter by `depth` frontmatter field
   - Search across:
     - `/Users/bt1q/Github-Projects/knowledge-graph/01-Principles/`
     - `/Users/bt1q/Github-Projects/knowledge-graph/02-Patterns/`
     - `/Users/bt1q/Github-Projects/knowledge-graph/03-Debug/`
     - `/Users/bt1q/Github-Projects/knowledge-graph/04-Architecture/`
     - `/Users/bt1q/Github-Projects/knowledge-graph/05-Research/`
     - `/Users/bt1q/Github-Projects/knowledge-graph/06-Team/` (only for `onboarding` format)
   - Read all matching entries fully

2. **Generate Document**: Based on `--format`, apply the corresponding generation strategy:

### Format: `blog`
Generate a technical blog post draft in Chinese.

**Process:**
- Select entries with depth `deep` or `intermediate` (prefer deep)
- Order by logical narrative flow: problem statement -> analysis -> insights
- Aggregate related entries into cohesive sections

**Output Structure:**
```markdown
# {Generated Title Based on Domain and Content}

## 引言
{Synthesized introduction explaining why this topic matters, drawn from entry Questions}

## {Section per major theme}
### {Sub-theme from entry title}
{Rewritten analysis from entry, adapted for blog audience}
{Code examples from Context sections where relevant}

### {Next sub-theme}
{Continue pattern}

## 核心洞察
{Aggregated Key Insights from all entries, deduplicated and organized}

## 权衡与思考
{Synthesized Trade-offs table combining perspectives from multiple entries}

## 总结
{Concluding synthesis tying insights together}

## 参考
{References from all entries}
```

### Format: `study-guide`
Generate a structured learning path sorted from prerequisites to advanced topics.

**Process:**
- Read the `prerequisites` frontmatter field from each entry
- Build a dependency graph: entries that are prerequisites come first
- Sort by depth: `surface` -> `intermediate` -> `deep`
- Within same depth, sort by confidence (lower confidence = needs more study, list earlier)
- Group into learning stages

**Output Structure:**
```markdown
# Study Guide: {Domain}

## Prerequisites
{List external prerequisites not covered in the vault}

## Stage 1: Foundations (Surface)
### {Entry title}
- **Core Question**: {Question from entry}
- **Key Takeaway**: {Primary insight from Key Insights}
- **Study Notes**: {Condensed analysis}
- **Confidence**: {current confidence} | **Review**: {review_date status}

## Stage 2: Intermediate
### {Entry title}
- **Builds On**: {prerequisites listed}
- **Core Question**: {Question}
- **Key Takeaway**: {Primary insight}
- **Study Notes**: {Condensed analysis}
- **Practice**: {Suggested exercise based on Context section}

## Stage 3: Advanced (Deep)
### {Entry title}
- **Builds On**: {prerequisites}
- **Core Question**: {Question}
- **Deep Dive**: {Full analysis summary}
- **Trade-offs**: {Trade-offs table from entry}
- **Challenge**: {Advanced exercise suggestion}

## Knowledge Map
| Entry | Depth | Confidence | Status | Prerequisite For |
|-------|-------|------------|--------|------------------|
| {title} | {depth} | {confidence} | {status} | {entries that list this as prereq} |

## Recommended Review Order
{Ordered list based on: overdue review_date first, then low confidence, then dependency order}
```

### Format: `onboarding`
Generate a team onboarding document from team entries and relevant technical entries.

**Process:**
- Primary source: entries from `06-Team/` matching the domain
- Secondary source: entries from `01-05` that are referenced by team entries or share domain/tags
- Organize by onboarding progression: context -> principles -> patterns -> common issues

**Output Structure:**
```markdown
# Team Onboarding: {Domain}

## Team Context
{Synthesized from team entries: what the team works on, key systems, responsibilities}

## Key Principles
{Principles from 01-Principles/ relevant to this team's domain}
- **{Principle title}**: {Summary with rationale}

## Common Patterns
{Patterns from 02-Patterns/ used by the team}
- **{Pattern title}**: {When to use, key implementation notes}

## Known Issues & Debug Guides
{From 03-Debug/ entries relevant to team's systems}
- **{Issue title}**: {Symptoms, root cause, resolution}

## Architecture Overview
{From 04-Architecture/ entries relevant to team's domain}
- **{Architecture entry}**: {Summary of key design decisions and trade-offs}

## Essential Reading
| Entry | Type | Why It Matters |
|-------|------|----------------|
| {title} | {type} | {1-line relevance explanation} |

## First Week Checklist
- [ ] Read through Key Principles section
- [ ] Review Common Patterns with a senior team member
- [ ] Study the Architecture Overview
- [ ] Walk through Known Issues debug guides
- [ ] {Additional items derived from team entries}
```

3. **Write Output**:
   - If `--output <path>` is specified, write the generated document to that file path using the Write tool
   - If no `--output`, display the full document in the conversation
   - Report summary: number of entries used, domains covered, total word count

## Key Paths
- Vault root: `/Users/bt1q/Github-Projects/knowledge-graph/`
- Knowledge dirs: `01-Principles/`, `02-Patterns/`, `03-Debug/`, `04-Architecture/`, `05-Research/`, `06-Team/`

## Examples

### Generate a Chinese blog post from deep Golang entries
```
/sc:kg-export --format blog --domain golang --depth deep
# Aggregates deep golang entries into a technical blog draft in Chinese
# Displays in conversation
```

### Generate a study guide for distributed systems
```
/sc:kg-export --format study-guide --domain distributed-systems
# Builds a prerequisite-ordered learning path
# Includes all depth levels
```

### Generate onboarding doc and save to file
```
/sc:kg-export --format onboarding --domain cloud-native --output /Users/bt1q/Github-Projects/knowledge-graph/exports/onboarding-cloud-native.md
# Creates team onboarding doc from team + technical entries
# Writes to specified output path
```

### Generate study guide filtered by depth
```
/sc:kg-export --format study-guide --domain golang --depth intermediate
# Only includes intermediate-depth entries in the study guide
```

## Boundaries

**Will:**
- Read and aggregate vault entries matching the specified filters
- Generate well-structured documents following the format templates
- Synthesize and rewrite content for the target audience (not just concatenate entries)
- Write to file when `--output` is specified
- Produce blog content in Chinese as specified

**Will Not:**
- Fabricate knowledge not present in the vault entries
- Modify the original vault entries during export
- Export entries from `07-Projects/` (project tracking is not exportable knowledge)
- Generate documents without at least 2 matching entries (report insufficient data instead)
- Include entries with `status: draft` unless fewer than 2 non-draft entries are available

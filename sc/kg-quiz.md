---
name: kg-quiz
description: "Spaced repetition quiz from knowledge graph entries to reinforce learning"
category: knowledge
complexity: standard
mcp-servers: []
personas: []
---

# /sc:kg-quiz - Spaced Repetition Quiz

## Triggers
- Scheduled review sessions for knowledge retention
- Learning reinforcement for specific domains
- Knowledge confidence calibration
- Daily/weekly knowledge maintenance routines

## Usage
```
/sc:kg-quiz [--domain <domain>] [--count <n>]
```

## Arguments
- `--domain <domain>`: Filter entries by domain (e.g., `golang`, `distributed-systems`, `databases`). If omitted, quiz from all domains.
- `--count <n>`: Number of questions to quiz (default: 3, max: 10).

## Behavioral Flow

1. **Scan Vault Entries**: Use Glob to find all `.md` files across knowledge directories:
   - `/Users/bt1q/Github-Projects/knowledge-graph/01-Principles/`
   - `/Users/bt1q/Github-Projects/knowledge-graph/02-Patterns/`
   - `/Users/bt1q/Github-Projects/knowledge-graph/03-Debug/`
   - `/Users/bt1q/Github-Projects/knowledge-graph/04-Architecture/`
   - `/Users/bt1q/Github-Projects/knowledge-graph/05-Research/`
   - `/Users/bt1q/Github-Projects/knowledge-graph/06-Team/`
   - If `--domain` is specified, use Grep to filter entries where the `domain` frontmatter field matches.

2. **Select Entries for Review**: Read entry frontmatter to identify candidates. Prioritize entries where:
   - `review_date` is today or in the past (highest priority -- overdue review)
   - `review_date` is empty AND the entry `created` date is older than 7 days (never reviewed)
   - Lower `confidence` values are weighted higher for selection (entries with confidence < 0.6 are prioritized)
   - If more candidates than `--count`, select using weighted random: lower confidence = higher selection probability
   - If fewer candidates than `--count`, use all available candidates

3. **Quiz Loop**: For each selected entry, execute one quiz round:

   **a. Present the Question**:
   - Show ONLY the `## Question` section and `## Context` section from the entry
   - Do NOT reveal the Analysis, Key Insights, or Trade-offs sections yet
   - Format:
   ```markdown
   ---
   ## Quiz Question {n}/{total}
   **Domain**: {domain} | **Type**: {type} | **Depth**: {depth}

   > {Question section content}

   **Context:**
   {Context section content}

   ---
   How well can you answer this?
   ```

   **b. Wait for User Response**:
   - Present three options for the user to select:
     - **Confident**: "I can fully explain this with depth"
     - **Partially remember**: "I recall the basics but not the details"
     - **Forgot**: "I cannot recall this well enough"
   - Wait for the user to respond before proceeding

   **c. Update Entry Frontmatter**: Based on the user's response, calculate new values:
   - **Confident**:
     - `confidence`: increase by 0.05, capped at 1.0
     - `review_date`: set to today + 30 days (format: YYYY-MM-DD)
   - **Partially remember**:
     - `confidence`: unchanged
     - `review_date`: set to today + 7 days (format: YYYY-MM-DD)
   - **Forgot**:
     - `confidence`: decrease by 0.1, floored at 0.3
     - `review_date`: set to today + 1 day (format: YYYY-MM-DD)
   - Also update `updated` field to today's date
   - Use the Edit tool to modify the frontmatter in the actual entry file

   **d. Reveal Full Answer**:
   - After the user answers, show the full entry content:
     - `## Analysis` section (all subsections)
     - `## Key Insights` section
     - `## Trade-offs` table (if present)
   - Format:
   ```markdown
   ### Answer Reveal

   **Analysis:**
   {Full analysis content}

   **Key Insights:**
   {Key insights content}

   **Trade-offs:**
   {Trade-offs table if present}

   ---
   Confidence: {old} -> {new} | Next review: {review_date}
   ```

4. **Quiz Summary**: After all questions are completed, present a summary:
```markdown
## Quiz Complete

| # | Entry | Response | Confidence | Next Review |
|---|-------|----------|------------|-------------|
| 1 | {title} | {response} | {old} -> {new} | {date} |
| 2 | {title} | {response} | {old} -> {new} | {date} |

**Session Stats:**
- Confident: {n} | Partial: {n} | Forgot: {n}
- Average confidence: {avg}
- Entries needing attention: {list entries with confidence < 0.6}
```

## Key Paths
- Vault root: `/Users/bt1q/Github-Projects/knowledge-graph/`
- Knowledge dirs: `01-Principles/`, `02-Patterns/`, `03-Debug/`, `04-Architecture/`, `05-Research/`, `06-Team/`

## Examples

### Default quiz (3 questions, all domains)
```
/sc:kg-quiz
# Selects 3 entries due for review or with low confidence
# Presents Question + Context, waits for response, reveals answer
```

### Domain-specific quiz
```
/sc:kg-quiz --domain golang
# Only quizzes on entries in the golang domain
```

### Extended quiz session
```
/sc:kg-quiz --count 5 --domain distributed-systems
# 5 questions focused on distributed systems knowledge
```

## Boundaries

**Will:**
- Select entries intelligently based on review dates and confidence levels
- Present questions without revealing answers until user responds
- Update frontmatter (confidence, review_date, updated) based on user responses
- Provide a summary of quiz performance after completion

**Will Not:**
- Modify entry content (Analysis, Key Insights, etc.) -- only frontmatter metadata
- Delete or create entries during quiz
- Skip the user response step -- always wait for explicit user input
- Quiz on entries from 07-Projects/ (project tracking notes are not quizzable)
- Show answers before the user has responded

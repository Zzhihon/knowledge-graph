# Knowledge Graph Vault

## Project Context
Personal knowledge graph vault (Obsidian). Vault root: `/Users/bt1q/Github-Projects/knowledge-graph/`.

## Algorithm Study Hook

A `UserPromptSubmit` hook (`.claude/hooks/kg-algo-auto-load.sh`) auto-detects algorithm study intent. When it fires, you will receive a `<user-prompt-submit-hook>` tag with instructions. **Follow those instructions exactly** — read the specified pattern template, scan the listed problems, and present the structured briefing.

### Hook Output Scenarios

1. **Specific pattern detected** (e.g. "复习滑动窗口"):
   - Hook provides: pattern template path + related problem file names
   - You do: Read pattern template → Read problem frontmatter → Present study panel

2. **Pending pattern** (e.g. "学习动态规划"):
   - Hook says: pattern NOT created yet
   - You do: Inform user → Offer to create it (pattern + 5 problems)

3. **General algorithm** (e.g. "刷几道 leetcode"):
   - Hook says: general algorithm intent
   - You do: Read MOC → Scan for overdue reviews → Present overview

4. **No hook output** (non-algorithm prompts):
   - Normal operation, no special handling

## Vault Structure
```
00-Index/         — MOC files (algorithm.md, golang.md, etc.)
01-Principles/    — Core principles and theory
02-Patterns/      — Algorithm pattern templates (6 active)
03-Debug/         — Debugging records
04-Architecture/  — Architecture decisions
05-Research/      — Research explorations
06-Team/          — Team knowledge
07-Projects/      — Project tracking
08-Problems/      — Algorithm problems (30 entries)
Templates/        — Entry templates
```

## Available Algorithm Patterns (6)
- Sliding Window (滑动窗口) — 5 problems
- Two Pointers (双指针) — 5 problems
- Binary Search (二分查找) — 5 problems
- Binary Tree Traversal (二叉树遍历) — 5 problems
- DFS (深度优先搜索) — 5 problems
- BFS (广度优先搜索) — 5 problems

## Key Commands
- `/sc:kg` — Capture knowledge from conversation
- `/sc:kg-quiz --domain algorithm` — Algorithm quiz
- `/sc:kg-load <topic>` — Load topic context
- `/sc:kg-export --format study-guide --domain algorithm` — Export study guide
- `/sc:kg-review --domain algorithm` — Review knowledge health

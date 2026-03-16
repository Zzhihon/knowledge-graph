#!/bin/bash
# kg-algo-auto-load.sh
# UserPromptSubmit hook: detect algorithm study/review intent → semantic search → inject context
#
# Uses kg query (Qdrant vector search) for topic detection and entry retrieval.
# Compatible with bash 3.x (macOS default).
# Exit 0 + stdout = context injected into Claude's prompt.
# Exit 0 + no stdout = silent pass-through.

set -euo pipefail

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty' 2>/dev/null)

[ -z "$PROMPT" ] && exit 0

# ── Phase 1: Detect study/review INTENT (lightweight grep) ───────────
# This detects the user's INTENT (study/review), not the topic.
INTENT=""
if echo "$PROMPT" | grep -iqE '复习|学习|学一下|看看|了解一下|刷题|刷几|练习|做题|做几|练一下|review|study|learn|practice|drill|quiz|复盘'; then
  INTENT="study"
elif echo "$PROMPT" | grep -iqE '算法面试|面试准备|interview prep'; then
  INTENT="interview"
fi

[ -z "$INTENT" ] && exit 0

# ── Phase 2: Detect algorithm context ────────────────────────────────
# Quick check: does the prompt mention anything algorithm-related?
if ! echo "$PROMPT" | grep -iqE '算法|algorithm|leetcode|lc-|力扣|滑动窗口|sliding.window|双指针|two.pointer|二分|binary|二叉树|binary.tree|dfs|bfs|深度优先|广度优先|动态规划|dp|回溯|backtrack|贪心|greedy|排序|sort|链表|linked.list|哈希|hash|栈|队列|stack|queue|堆|heap|图|graph|单调栈|monoton'; then
  exit 0
fi

# ── Phase 3: Semantic search via kg query (Qdrant) ───────────────────
VAULT="${CLAUDE_PROJECT_DIR:-.}"

# Resolve the venv Python and kg CLI
VENV_PYTHON="$VAULT/.venv/bin/python3"
KG_CMD=""

if [ -x "$VENV_PYTHON" ]; then
  KG_CMD="$VENV_PYTHON -m agents.cli"
elif [ -x "$VAULT/.venv/bin/kg" ]; then
  KG_CMD="$VAULT/.venv/bin/kg"
elif command -v kg >/dev/null 2>&1; then
  KG_CMD="kg"
fi

# Remove stale Qdrant lock (local storage mode)
rm -f "$VAULT/indexes/qdrant/.lock" 2>/dev/null

PATTERN_RESULTS="[]"
PROBLEM_RESULTS="[]"

if [ -n "$KG_CMD" ]; then
  # Search for related patterns (run from vault root so agents/ resolves correctly)
  PATTERN_RESULTS=$(cd "$VAULT" 2>/dev/null && \
    $KG_CMD query "$PROMPT" --domain algorithm --type pattern --top-k 3 --format json 2>/dev/null || echo "[]")

  # Remove lock between queries (local Qdrant single-process constraint)
  rm -f "$VAULT/indexes/qdrant/.lock" 2>/dev/null

  # Search for related problems
  PROBLEM_RESULTS=$(cd "$VAULT" 2>/dev/null && \
    $KG_CMD query "$PROMPT" --domain algorithm --type problem --top-k 10 --format json 2>/dev/null || echo "[]")
fi

# ── Phase 4: Build context output ────────────────────────────────────
PATTERN_COUNT=$(echo "$PATTERN_RESULTS" | jq 'length' 2>/dev/null || echo "0")
PROBLEM_COUNT=$(echo "$PROBLEM_RESULTS" | jq 'length' 2>/dev/null || echo "0")
TOTAL_PROBLEMS=$(find "$VAULT/08-Problems" -name "*.md" 2>/dev/null | wc -l | tr -d ' ')

# Ensure counts are numeric (default to 0)
case "$PATTERN_COUNT" in ''|*[!0-9]*) PATTERN_COUNT=0 ;; esac
case "$PROBLEM_COUNT" in ''|*[!0-9]*) PROBLEM_COUNT=0 ;; esac

# Format pattern results for context
PATTERN_LIST=""
if [ "$PATTERN_COUNT" -gt 0 ]; then
  PATTERN_LIST=$(echo "$PATTERN_RESULTS" | jq -r '.[] | "  - [\(.score)] \(.title) → \(.file_path)"' 2>/dev/null || true)
fi

# Format problem results for context
PROBLEM_LIST=""
if [ "$PROBLEM_COUNT" -gt 0 ]; then
  PROBLEM_LIST=$(echo "$PROBLEM_RESULTS" | jq -r '.[] | "  - [\(.score)] \(.title) (type=\(.type), id=\(.id)) → \(.file_path)"' 2>/dev/null || true)
fi

# Determine if we got meaningful results from vector search
HAS_SEMANTIC_RESULTS="false"
if [ "$PATTERN_COUNT" -gt 0 ] || [ "$PROBLEM_COUNT" -gt 0 ]; then
  HAS_SEMANTIC_RESULTS="true"
fi

if [ "$HAS_SEMANTIC_RESULTS" = "true" ]; then
  # ── Semantic search found relevant entries ──
  cat <<EOF
<user-prompt-submit-hook>
[Algorithm Study Auto-Trigger — Semantic Search Results]

User wants to ${INTENT} algorithms. Vector search (Qdrant) found the following relevant entries:

## Related Patterns (top ${PATTERN_COUNT}):
${PATTERN_LIST:-  (none found)}

## Related Problems (top ${PROBLEM_COUNT}):
${PROBLEM_LIST:-  (none found)}

## Instructions:
1. READ the top-scoring pattern template file(s) listed above
   - Extract: identification signals, variant comparison, generic templates
2. READ each related problem file
   - Extract frontmatter: difficulty, confidence, review_date
   - Sort: easy → medium → hard
3. CHECK review status:
   - Overdue: review_date <= today AND review_date != ""
   - Weak: confidence < 0.6
4. PRESENT a structured study briefing:

   ### 模式概要
   [Key identification signals + variant summary from pattern template]

   ### 题目列表
   | # | 题目 | 难度 | 置信度 | 复习状态 |
   [Sorted by difficulty, include all matched problems]

   ### 建议
   - Overdue → "有 N 道题需要复习，是否开始 quiz？"
   - Low confidence → "建议重点复习这些题目"
   - First time → "建议先阅读模式模板，再按 easy→hard 刷题"
   - All confident → "掌握良好！建议进入下一个模式"

5. WAIT for user to choose: quiz / read pattern / pick specific problem

Vault: $VAULT | Total problems: $TOTAL_PROBLEMS | Intent: $INTENT
Search powered by: Qdrant vector search (all-MiniLM-L6-v2)
</user-prompt-submit-hook>
EOF

else
  # ── Fallback: vector search unavailable or no results ──
  cat <<EOF
<user-prompt-submit-hook>
[Algorithm Study Auto-Trigger — General]

User wants to ${INTENT} algorithms but semantic search returned no results.
(Vector index may need rebuilding: run \`kg sync\`)

Fallback instructions:
1. Read the algorithm MOC: $VAULT/00-Index/algorithm.md
2. Run semantic search manually: kg query "<user's topic>" --domain algorithm --top-k 10
3. Scan 08-Problems/ for overdue review_date entries (review_date <= today)
4. Scan for low-confidence entries (confidence < 0.6)
5. Present a structured briefing:
   - Available patterns with problem counts
   - Overdue reviews → suggest /sc:kg-quiz --domain algorithm
   - Low confidence entries → suggest targeted practice
6. Ask user which pattern to focus on, or start a quiz

Vault: $VAULT | Total problems: $TOTAL_PROBLEMS | Intent: $INTENT
</user-prompt-submit-hook>
EOF
fi

exit 0

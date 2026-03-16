#!/usr/bin/env bash
# Claude Code PostToolUse hook: auto-discover links after writing knowledge entries
# Triggered when Write/Edit tool touches files in knowledge-graph/0[1-6]-* directories

set -euo pipefail

VAULT_ROOT="/Users/bt1q/Github-Projects/knowledge-graph"
VENV_PYTHON="${VAULT_ROOT}/.venv/bin/python3"
LOG_FILE="${VAULT_ROOT}/scripts/.hook.log"

# Read JSON input from stdin
INPUT=$(cat)

# Extract file path from tool_input
FILE_PATH=$(echo "$INPUT" | /usr/bin/python3 -c "
import sys, json
data = json.load(sys.stdin)
print(data.get('tool_input', {}).get('file_path', ''))
" 2>/dev/null || echo "")

# Only process knowledge entry writes (01-06 directories)
if [[ ! "$FILE_PATH" =~ ${VAULT_ROOT}/0[1-6]- ]]; then
    exit 0
fi

# Skip if venv doesn't exist
if [[ ! -f "$VENV_PYTHON" ]]; then
    exit 0
fi

# Run link discovery (non-blocking, timeout 30s)
SUGGESTIONS=$(cd "$VAULT_ROOT" && timeout 30 "$VENV_PYTHON" -c "
from agents.link import find_links
suggestions = find_links(top_n=5, threshold=0.7)
if suggestions:
    print(f'发现 {len(suggestions)} 条潜在关联:')
    for s in suggestions[:3]:
        src = s['source_title']
        tgt = s['target_title']
        score = s['similarity']
        print(f'  {src} ↔ {tgt} ({score:.0%})')
" 2>/dev/null || echo "")

if [[ -n "$SUGGESTIONS" ]]; then
    # Log the suggestions
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $FILE_PATH → $SUGGESTIONS" >> "$LOG_FILE"
    # Output to transcript view
    echo "$SUGGESTIONS"
fi

exit 0

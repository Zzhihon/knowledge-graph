#!/usr/bin/env bash
# Claude Code UserPromptSubmit hook: semantic search + auto-sync if index is stale

VAULT_ROOT="/Users/bt1q/Github-Projects/knowledge-graph"
MARKER_DIR="/tmp/.claude-kg-sessions"
VENV_PYTHON="${VAULT_ROOT}/.venv/bin/python3"
QDRANT_DIR="${VAULT_ROOT}/indexes/qdrant"

mkdir -p "$MARKER_DIR" 2>/dev/null || true
find "$MARKER_DIR" -name "session-*" -mmin +720 -delete 2>/dev/null || true

# Session-once guard
SESSION_FILE="${MARKER_DIR}/session-${PPID:-0}"
if [[ -f "$SESSION_FILE" ]]; then
    exit 0
fi
touch "$SESSION_FILE"

# Check venv exists
if [[ ! -f "$VENV_PYTHON" ]]; then
    exit 0
fi

# Check if index needs sync (markdown files newer than Qdrant index)
NEEDS_SYNC=false
if [[ -d "$QDRANT_DIR" ]]; then
    # Find newest markdown file in vault
    NEWEST_MD=$(find "${VAULT_ROOT}"/0[1-6]-*/ -name "*.md" -type f -print0 2>/dev/null | xargs -0 stat -f "%m %N" 2>/dev/null | sort -rn | head -1 | cut -d' ' -f1)
    # Find Qdrant index modification time
    QDRANT_MTIME=$(stat -f "%m" "$QDRANT_DIR" 2>/dev/null || echo "0")

    if [[ -n "$NEWEST_MD" ]] && [[ "$NEWEST_MD" -gt "$QDRANT_MTIME" ]]; then
        NEEDS_SYNC=true
    fi
fi

# If sync needed, trigger background sync
if [[ "$NEEDS_SYNC" == "true" ]]; then
    echo "🔄 检测到新知识条目，正在后台更新索引..."
    (cd "$VAULT_ROOT" && "$VENV_PYTHON" -m agents.cli sync >/dev/null 2>&1 &)
fi

INPUT=$(cat)

# Use Qdrant semantic search
OUTPUT=$(cd "$VAULT_ROOT" && echo "$INPUT" | "$VENV_PYTHON" -c "
import sys, json, os
os.environ.setdefault('HF_HUB_OFFLINE', '1')
os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)

prompt = data.get('prompt', data.get('user_prompt', ''))
if not prompt or len(prompt) < 2:
    sys.exit(0)

try:
    from agents.embeddings import embed_single
    from agents.vector_store import get_vector_store
    from agents.config import load_config

    config = load_config()
    vec = embed_single(prompt)
    store = get_vector_store(config)
    results = store.search(vec, top_k=3)
    store.close()

    relevant = [r for r in results if r.get('score', 0) > 0.25]
    if relevant:
        titles = ', '.join(r['title'] for r in relevant)
        print(f'Knowledge vault has {len(relevant)} relevant entries: {titles}')
except Exception:
    sys.exit(0)
" 2>/dev/null) || true

if [[ -n "${OUTPUT:-}" ]]; then
    echo "$OUTPUT"
fi

exit 0

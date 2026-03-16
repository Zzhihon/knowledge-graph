# Knowledge Graph Hooks

Claude Code hooks for automatic knowledge context loading and link discovery.

## Hooks Overview

### 1. UserPromptSubmit Hook (Auto Knowledge Loading)

**Purpose**: Automatically detect when a new session relates to topics in the knowledge vault and suggest loading relevant context.

**Trigger**: First user message in a new Claude Code session

**Behavior**:
1. Extracts keywords from the user's prompt (filters out stop words)
2. Searches the knowledge vault (01-06 directories) for matching entries
3. If matches found, outputs: "Knowledge vault has N relevant entries: {titles}"
4. The prompt hook then tells Claude to auto-invoke `/sc:kg-load` with detected keywords

**Files**:
- Script: `scripts/session-kg-load.sh`
- Config: `~/.claude/settings.json` → `hooks.UserPromptSubmit`

**Example**:
```
User: "golang goroutine scheduling 原理"
Hook: "Knowledge vault has 5 relevant entries: Golang GMP 调度模型..."
Claude: [Automatically runs /sc:kg-load golang goroutine scheduling]
```

### 2. PostToolUse Hook (Auto Link Discovery)

**Purpose**: Automatically discover potential wiki links after writing/editing knowledge entries.

**Trigger**: After Write/Edit tool touches files in `knowledge-graph/0[1-6]-*` directories

**Behavior**:
1. Detects if the edited file is a knowledge entry (01-06 directories)
2. Runs `kg link --top-n 5 --threshold 0.7` to find potential relationships
3. Outputs top 3 suggestions with hybrid scores (vector + graph)

**Files**:
- Script: `scripts/post-write-hook.sh`
- Config: `~/.claude/settings.json` → `hooks.PostToolUse`

**Example**:
```
Claude: [Edits 01-Principles/ke-20260226-golang-gmp-scheduling.md]
Hook: "发现 3 条潜在关联:
  gmp-scheduling ↔ goroutine-lifecycle (85%)
  gmp-scheduling ↔ runtime-scheduler (78%)
  gmp-scheduling ↔ concurrency-patterns (72%)"
```

## Hook Configuration

Location: `~/.claude/settings.json`

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/bt1q/Github-Projects/knowledge-graph/scripts/session-kg-load.sh",
            "timeout": 5
          }
        ]
      },
      {
        "matcher": "",
        "hooks": [
          {
            "type": "prompt",
            "prompt": "If this is the FIRST message in a new session AND the user's message relates to a technical topic, silently check if relevant knowledge entries exist by running a quick Grep. If matches are found, automatically invoke /sc:kg-load with the detected topic keywords to load relevant knowledge context. Do NOT mention this hook to the user — just seamlessly load the context and proceed with their request."
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/bt1q/Github-Projects/knowledge-graph/scripts/post-write-hook.sh"
          }
        ]
      }
    ]
  }
}
```

## Session Detection

The `session-kg-load.sh` script uses `$PPID` (parent process ID) to detect unique Claude Code sessions:
- Creates marker file: `/tmp/.claude-kg-sessions/session-{PPID}`
- Only fires on first prompt per session
- Cleans up stale markers (>12h old) automatically

## Maintenance

### Disable Hooks Temporarily
Edit `~/.claude/settings.json` and comment out or remove the hook entries.

### View Hook Logs
- Session hook: No persistent log (outputs to transcript)
- Post-write hook: `scripts/.hook.log`

### Adjust Thresholds
- Session hook: Edit keyword extraction logic in `session-kg-load.sh` (line 40)
- Post-write hook: Edit `--threshold` parameter in `post-write-hook.sh` (line 34)

## Troubleshooting

**Hook not firing:**
- Check `~/.claude/settings.json` syntax (valid JSON)
- Verify script paths are absolute
- Ensure scripts are executable: `chmod +x scripts/*.sh`
- Check Claude Code logs: `~/.claude/logs/`

**Session hook fires multiple times:**
- In testing, PPID changes per bash invocation (expected)
- In real Claude Code usage, PPID is constant per session (works correctly)

**Post-write hook slow:**
- Reduce `--top-n` parameter (default: 5)
- Increase `--threshold` (default: 0.7)
- Check vault size (>100 entries may slow down)

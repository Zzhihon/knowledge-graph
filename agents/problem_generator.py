"""Generate algorithm pattern templates and problem entries via Claude API.

Uses hardcoded LeetCode problem anchors per pattern to prevent hallucination.
Writes files in the same frontmatter schema as existing 02-Patterns/ and
08-Problems/ entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import frontmatter

from agents.config import ProjectConfig, load_config
from agents.json_utils import parse_json_robust, strip_code_fence
from agents.utils import generate_id, slugify

# ---------------------------------------------------------------------------
# Hardcoded LeetCode anchors — real problem IDs to ground Claude's output
# ---------------------------------------------------------------------------
_PATTERN_PROBLEM_ANCHORS: dict[str, list[int]] = {
    "dynamic-programming": [70, 322, 300, 1143, 72],
    "backtracking": [46, 78, 39, 51, 37],
    "greedy": [55, 435, 452, 134, 621],
    "monotonic-stack": [84, 739, 496, 503, 42],
    "heap": [215, 347, 295, 23, 378],
}

_PATTERN_CHINESE_NAMES: dict[str, str] = {
    "dynamic-programming": "动态规划",
    "backtracking": "回溯",
    "greedy": "贪心",
    "monotonic-stack": "单调栈",
    "heap": "堆",
}

_DIFFICULTY_MAP: dict[int, str] = {
    # dynamic-programming
    70: "easy", 322: "medium", 300: "medium", 1143: "medium", 72: "hard",
    # backtracking
    46: "medium", 78: "medium", 39: "medium", 51: "hard", 37: "hard",
    # greedy
    55: "medium", 435: "medium", 452: "medium", 134: "medium", 621: "medium",
    # monotonic-stack
    84: "hard", 739: "medium", 496: "easy", 503: "medium", 42: "hard",
    # heap
    215: "medium", 347: "medium", 295: "hard", 23: "hard", 378: "medium",
}

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class GeneratedProblem:
    entry_id: str
    title: str
    leetcode_id: int
    difficulty: str
    file_path: str


@dataclass
class PatternBatchResult:
    pattern_name: str
    pattern_file: str
    problems: list[GeneratedProblem] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_PATTERN_TEMPLATE_PROMPT = """\
你是一个算法面试教练。请为「{chinese_name}」({pattern_name}) 生成一个详细的算法模式模板。

请以 JSON 格式返回，包含以下字段：
- title: 中文标题，格式为 "{chinese_name}模式模板"
- recognition_signals: 识别信号列表（关键词、数据特征、反面信号）
- variants: 变体列表，每个变体包含 name, description, typical_problems, cpp_template, go_template
- key_insights: 5 条核心洞察
- tradeoffs: 优劣势对比列表
- related_patterns: 相关模式列表

模板代码使用 C++ 和 Go 双语言，代码中用注释标记关键决策点。
确保所有代码可编译运行。只返回 JSON，不要有其他文字。
"""

_PROBLEM_PROMPT = """\
你是一个算法面试教练。请为 LeetCode {leetcode_id} 号题目生成详细的题解条目。

这是一道「{pattern_name}」({chinese_name}) 模式的题目。

请以 JSON 格式返回，包含以下字段：
- title: 中文标题，格式为 "LC-{leetcode_id} <中文题名>"
- english_title: 英文题名
- description: 题目描述（中文）
- constraints: 约束条件列表
- examples: 示例列表，每个包含 input, output, explanation
- pattern_analysis: 模式分析（识别信号 + 核心思路）
- solution_cpp: 完整的 C++ 解法代码
- solution_go: 完整的 Go 解法代码
- time_complexity: 时间复杂度
- space_complexity: 空间复杂度
- key_insights: 3-5 条关键洞察
- edge_cases: 边界情况列表
- companies: 常考该题的公司列表

确保题目信息准确（这是真实的 LeetCode 题目 #{leetcode_id}）。
代码必须能通过 LeetCode 测试。只返回 JSON，不要有其他文字。
"""


# ---------------------------------------------------------------------------
# Core generation logic
# ---------------------------------------------------------------------------

def generate_pattern_batch(
    pattern_name: str,
    chinese_name: str | None = None,
    problem_count: int = 5,
    difficulty_mix: dict[str, int] | None = None,
    dry_run: bool = False,
    config: ProjectConfig | None = None,
) -> PatternBatchResult:
    """Generate a pattern template + N problem entries and write to vault.

    Args:
        pattern_name: Slug like 'dynamic-programming'.
        chinese_name: Display name like '动态规划'. Auto-detected if None.
        problem_count: How many problems to generate (max = anchors available).
        difficulty_mix: Ignored for now; uses hardcoded anchor difficulties.
        dry_run: If True, return result without writing files.
        config: Project config. Auto-loaded if None.

    Returns:
        PatternBatchResult with paths of created files.
    """
    if config is None:
        config = load_config()

    if chinese_name is None:
        chinese_name = _PATTERN_CHINESE_NAMES.get(pattern_name, pattern_name)

    anchors = _PATTERN_PROBLEM_ANCHORS.get(pattern_name)
    if anchors is None:
        raise ValueError(
            f"未知模式: '{pattern_name}'. "
            f"支持的模式: {', '.join(sorted(_PATTERN_PROBLEM_ANCHORS))}"
        )

    # Limit to available anchors
    selected_anchors = anchors[:problem_count]

    client = anthropic.Anthropic()
    result = PatternBatchResult(pattern_name=pattern_name, pattern_file="")

    # --- Step 1: Generate pattern template ---
    try:
        pattern_data = _call_claude(
            client,
            _PATTERN_TEMPLATE_PROMPT.format(
                chinese_name=chinese_name,
                pattern_name=pattern_name,
            ),
            config.agent.model,
        )
        pattern_file = _write_pattern_template(
            pattern_name, chinese_name, pattern_data,
            selected_anchors, config, dry_run,
        )
        result.pattern_file = pattern_file
    except Exception as exc:
        result.errors.append(f"模式模板生成失败: {exc}")
        return result

    # --- Step 2: Generate each problem ---
    for lc_id in selected_anchors:
        try:
            problem_data = _call_claude(
                client,
                _PROBLEM_PROMPT.format(
                    leetcode_id=lc_id,
                    pattern_name=pattern_name,
                    chinese_name=chinese_name,
                ),
                config.agent.model,
            )
            difficulty = _DIFFICULTY_MAP.get(lc_id, "medium")
            problem_file = _write_problem_entry(
                lc_id, difficulty, pattern_name, chinese_name,
                problem_data, result.pattern_file, config, dry_run,
            )
            result.problems.append(GeneratedProblem(
                entry_id=Path(problem_file).stem,
                title=problem_data.get("title", f"LC-{lc_id}"),
                leetcode_id=lc_id,
                difficulty=difficulty,
                file_path=problem_file,
            ))
        except Exception as exc:
            result.errors.append(f"LC-{lc_id} 生成失败: {exc}")

    return result


def get_available_patterns() -> dict[str, dict[str, Any]]:
    """Return info about all known patterns and their generation status."""
    config = load_config()
    patterns_dir = config.vault_path / "02-Patterns"

    all_patterns: dict[str, dict[str, Any]] = {}

    # Active patterns (already exist)
    _ACTIVE_PATTERNS = [
        "sliding-window", "two-pointers", "binary-search",
        "binary-tree-traversal", "dfs", "bfs",
    ]
    for name in _ACTIVE_PATTERNS:
        # Check if template file exists
        exists = any(
            name in f.stem
            for f in patterns_dir.glob("*.md")
        ) if patterns_dir.is_dir() else False
        all_patterns[name] = {
            "status": "active" if exists else "active",
            "chinese_name": _get_chinese_name(name),
            "anchors": [],
        }

    # Pending patterns
    for name, anchors in _PATTERN_PROBLEM_ANCHORS.items():
        exists = any(
            name in f.stem
            for f in patterns_dir.glob("*.md")
        ) if patterns_dir.is_dir() else False
        all_patterns[name] = {
            "status": "active" if exists else "pending",
            "chinese_name": _PATTERN_CHINESE_NAMES.get(name, name),
            "anchors": anchors,
        }

    return all_patterns


def _get_chinese_name(pattern: str) -> str:
    """Map pattern slug to Chinese name."""
    names = {
        "sliding-window": "滑动窗口",
        "two-pointers": "双指针",
        "binary-search": "二分查找",
        "binary-tree-traversal": "二叉树遍历",
        "dfs": "深度优先搜索",
        "bfs": "广度优先搜索",
        **_PATTERN_CHINESE_NAMES,
    }
    return names.get(pattern, pattern)


# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

def _call_claude(
    client: anthropic.Anthropic,
    prompt: str,
    model: str,
) -> dict[str, Any]:
    """Call Claude and parse JSON response."""
    message = client.messages.create(
        model=model,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = ""
    for block in message.content:
        if block.type == "text":
            response_text += block.text

    response_text = strip_code_fence(response_text)
    data = parse_json_robust(response_text)

    if isinstance(data, list):
        data = data[0] if data else {}

    return data


# ---------------------------------------------------------------------------
# File writers
# ---------------------------------------------------------------------------

def _write_pattern_template(
    pattern_name: str,
    chinese_name: str,
    data: dict[str, Any],
    problem_anchors: list[int],
    config: ProjectConfig,
    dry_run: bool,
) -> str:
    """Write a pattern template markdown file to 02-Patterns/."""
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    date_compact = datetime.now(tz=timezone.utc).strftime("%Y%m%d")
    entry_id = f"ke-{date_compact}-{pattern_name}-pattern-template"

    # Build related problem IDs
    related = [
        f"ke-{date_compact}-lc{lc_id}-{slugify(f'lc{lc_id}', max_length=40)}"
        for lc_id in problem_anchors
    ]

    metadata: dict[str, Any] = {
        "id": entry_id,
        "title": f"{chinese_name}模式模板",
        "domain": ["algorithm"],
        "tags": [pattern_name, "template"],
        "type": "pattern",
        "depth": "deep",
        "status": "validated",
        "scope": "personal",
        "source": {
            "type": "claude-generation",
            "date": now_str,
            "context": f"{chinese_name}模式自动生成",
        },
        "related": related,
        "prerequisites": [],
        "supersedes": None,
        "confidence": 0.8,
        "ease_factor": 2.5,
        "review_interval": 14,
        "code_refs": [],
        "created": now_str,
        "updated": now_str,
        "review_date": now_str,
    }

    # Build body from Claude's structured output
    body_parts: list[str] = [f"# {chinese_name}模式模板\n"]

    body_parts.append("## Question\n")
    body_parts.append(f"> [!question] 核心问题")
    body_parts.append(f"> 什么时候使用{chinese_name}？{chinese_name}有哪些变体？如何根据题目特征选择正确的模板？\n")

    # Recognition signals
    signals = data.get("recognition_signals", [])
    if signals:
        body_parts.append("## 识别信号\n")
        for sig in signals:
            if isinstance(sig, str):
                body_parts.append(f"- {sig}")
            elif isinstance(sig, dict):
                body_parts.append(f"- **{sig.get('category', '')}**: {sig.get('detail', str(sig))}")
        body_parts.append("")

    # Variants
    variants = data.get("variants", [])
    if variants:
        body_parts.append("## Analysis\n")
        body_parts.append("### 变体详解\n")
        for v in variants:
            name = v.get("name", "变体")
            desc = v.get("description", "")
            body_parts.append(f"#### {name}\n")
            if desc:
                body_parts.append(f"{desc}\n")
            cpp = v.get("cpp_template", "")
            if cpp:
                body_parts.append(f"**C++ 模板**:\n\n```cpp\n{cpp}\n```\n")
            go = v.get("go_template", "")
            if go:
                body_parts.append(f"**Go 模板**:\n\n```go\n{go}\n```\n")

    # Key insights
    insights = data.get("key_insights", [])
    if insights:
        body_parts.append("## Key Insights\n")
        body_parts.append("> [!tip] 核心洞察")
        for ins in insights:
            body_parts.append(f"> - {ins}")
        body_parts.append("")

    # Tradeoffs
    tradeoffs = data.get("tradeoffs", [])
    if tradeoffs:
        body_parts.append("## Trade-offs\n")
        body_parts.append("| 维度 | 优势 | 劣势 |")
        body_parts.append("|------|------|------|")
        for t in tradeoffs:
            if isinstance(t, dict):
                body_parts.append(f"| {t.get('dimension', '')} | {t.get('advantage', '')} | {t.get('disadvantage', '')} |")
        body_parts.append("")

    # Related
    body_parts.append("## Related\n")
    for lc_id in problem_anchors:
        body_parts.append(f"- [[ke-{date_compact}-lc{lc_id}|LC-{lc_id}]]")
    body_parts.append("")

    body = "\n".join(body_parts)
    post = frontmatter.Post(body, **metadata)
    content = frontmatter.dumps(post)

    target_dir = config.vault_path / "02-Patterns"
    target_file = target_dir / f"{entry_id}.md"

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")

    return str(target_file)


def _write_problem_entry(
    leetcode_id: int,
    difficulty: str,
    pattern_name: str,
    chinese_name: str,
    data: dict[str, Any],
    pattern_file: str,
    config: ProjectConfig,
    dry_run: bool,
) -> str:
    """Write a problem entry markdown file to 08-Problems/."""
    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    date_compact = datetime.now(tz=timezone.utc).strftime("%Y%m%d")

    title = data.get("title", f"LC-{leetcode_id}")
    english_title = data.get("english_title", "")
    slug = slugify(english_title or f"lc{leetcode_id}", max_length=50)
    entry_id = f"ke-{date_compact}-lc{leetcode_id}-{slug}"

    pattern_entry_id = Path(pattern_file).stem if pattern_file else ""

    metadata: dict[str, Any] = {
        "id": entry_id,
        "title": title,
        "domain": ["algorithm"],
        "tags": [pattern_name] + data.get("extra_tags", []),
        "type": "problem",
        "difficulty": difficulty,
        "frequency": "high",
        "companies": data.get("companies", []),
        "leetcode_id": leetcode_id,
        "leetcode_url": f"https://leetcode.cn/problems/{slug}/",
        "pattern": [pattern_name],
        "depth": "deep",
        "status": "validated",
        "scope": "personal",
        "source": {
            "type": "claude-generation",
            "date": now_str,
            "context": f"{chinese_name}模式练习",
        },
        "related": [pattern_entry_id] if pattern_entry_id else [],
        "prerequisites": [pattern_entry_id] if pattern_entry_id else [],
        "supersedes": None,
        "confidence": 0.7,
        "ease_factor": 2.5,
        "review_interval": 7,
        "created": now_str,
        "updated": now_str,
        "review_date": now_str,
    }

    # Build body
    body_parts: list[str] = []

    # Problem Description
    desc = data.get("description", "")
    body_parts.append("## Problem Description\n")
    body_parts.append(desc + "\n")

    constraints = data.get("constraints", [])
    if constraints:
        body_parts.append("**约束**:")
        for c in constraints:
            body_parts.append(f"- {c}")
        body_parts.append("")

    # Examples
    examples = data.get("examples", [])
    if examples:
        body_parts.append("## Examples\n")
        for i, ex in enumerate(examples, 1):
            inp = ex.get("input", "")
            out = ex.get("output", "")
            exp = ex.get("explanation", "")
            body_parts.append(f"{i}. {inp} -> {out}")
            if exp:
                body_parts.append(f"   说明: {exp}")
        body_parts.append("")

    # Pattern Analysis
    analysis = data.get("pattern_analysis", "")
    if analysis:
        body_parts.append("## Pattern Analysis\n")
        body_parts.append(f"{analysis}\n")

    # C++ Solution
    cpp_code = data.get("solution_cpp", "")
    if cpp_code:
        body_parts.append("## Solution - C++\n")
        body_parts.append(f"```cpp\n{cpp_code}\n```\n")
        tc = data.get("time_complexity", "")
        sc = data.get("space_complexity", "")
        if tc or sc:
            body_parts.append(f"Time: {tc}, Space: {sc}\n")

    # Go Solution
    go_code = data.get("solution_go", "")
    if go_code:
        body_parts.append("## Solution - Go\n")
        body_parts.append(f"```go\n{go_code}\n```\n")
        tc = data.get("time_complexity", "")
        sc = data.get("space_complexity", "")
        if tc or sc:
            body_parts.append(f"Time: {tc}, Space: {sc}\n")

    # Key Insights
    insights = data.get("key_insights", [])
    if insights:
        body_parts.append("## Key Insights\n")
        for i, ins in enumerate(insights, 1):
            body_parts.append(f"{i}. {ins}")
        body_parts.append("")

    # Edge Cases
    edge_cases = data.get("edge_cases", [])
    if edge_cases:
        body_parts.append("## Edge Cases\n")
        for ec in edge_cases:
            body_parts.append(f"- {ec}")
        body_parts.append("")

    # References
    body_parts.append("## References\n")
    body_parts.append(f"- [LeetCode {leetcode_id}](https://leetcode.cn/problems/{slug}/)\n")

    body = "\n".join(body_parts)
    post = frontmatter.Post(body, **metadata)
    content = frontmatter.dumps(post)

    target_dir = config.vault_path / "08-Problems"
    target_file = target_dir / f"{entry_id}.md"

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")

    return str(target_file)

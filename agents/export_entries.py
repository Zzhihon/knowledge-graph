"""Knowledge export module for generating formatted documents.

Transforms knowledge entries into blog posts, study guides, and
onboarding documents with domain-aware organization and learning
progression support.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console

from agents.config import load_config
from agents.utils import load_entries

console = Console()

# Depth ordering for study guide progression
_DEPTH_ORDER: dict[str, int] = {
    "surface": 0,
    "intermediate": 1,
    "deep": 2,
}


def _extract_section(content: str, header: str) -> str:
    """Extract a markdown section by header name, returning empty string if absent."""
    pattern = re.compile(
        rf"^##\s+{re.escape(header)}\s*\n(.*?)(?=\n##\s|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(content)
    return match.group(1).strip() if match else ""


def _filter_by_domain(
    entries: list[dict[str, Any]],
    domain: str | None,
) -> list[dict[str, Any]]:
    """Filter entries by domain key if specified."""
    if domain is None:
        return entries
    domain_lower = domain.lower()
    return [
        e for e in entries
        if _entry_domain_matches(e, domain_lower)
    ]


def _entry_domain_matches(entry: dict[str, Any], domain_lower: str) -> bool:
    """Check if an entry belongs to a domain (case-insensitive)."""
    val = entry["metadata"].get("domain", "")
    if isinstance(val, list):
        return any(str(d).lower() == domain_lower for d in val)
    return str(val).lower() == domain_lower


def _get_depth_rank(entry: dict[str, Any]) -> int:
    """Return numeric depth rank for sorting (lower = shallower)."""
    depth = str(entry["metadata"].get("depth", "surface")).lower()
    return _DEPTH_ORDER.get(depth, 0)


def export_blog(
    entries: list[dict[str, Any]],
    domain: str | None = None,
) -> str:
    """Generate a Chinese technical blog post from knowledge entries.

    Prefers deep and validated entries. Synthesizes Key Insights and
    Analysis sections into a cohesive article with aggregated references.

    Args:
        entries: All loaded entries.
        domain: Optional domain key to filter.

    Returns:
        Markdown string of the generated blog post.
    """
    filtered = _filter_by_domain(entries, domain)
    if not filtered:
        return "<!-- 无匹配条目可导出 -->"

    # Prefer deep + validated entries
    filtered.sort(
        key=lambda e: (
            -_get_depth_rank(e),
            0 if e["metadata"].get("status") == "validated" else 1,
        )
    )

    config = load_config()
    domain_label = domain or "综合知识"
    if domain:
        domain_cfg = config.get_domain(domain)
        if domain_cfg:
            domain_label = f"{domain_cfg.icon} {domain_cfg.label}"

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    lines: list[str] = [
        f"# {domain_label} - 技术总结",
        "",
        f"> 生成日期: {date_str}  ",
        f"> 条目数量: {len(filtered)}",
        "",
        "## 概述",
        "",
        f"本文整理了 {domain_label} 领域的核心知识点，涵盖 {len(filtered)} 个知识条目的要点分析。",
        "",
    ]

    all_references: list[str] = []

    for entry in filtered:
        meta = entry["metadata"]
        title = meta.get("title", "未知标题")
        depth = meta.get("depth", "")
        content = entry["content"]

        lines.append(f"## {title}")
        lines.append("")
        if depth:
            lines.append(f"**深度**: {depth}")
            lines.append("")

        # Key Insights
        insights = _extract_section(content, "Key Insights")
        if insights:
            lines.append("### 核心要点")
            lines.append("")
            lines.append(insights)
            lines.append("")

        # Analysis
        analysis = _extract_section(content, "Analysis")
        if analysis:
            lines.append("### 分析")
            lines.append("")
            lines.append(analysis)
            lines.append("")

        # Collect references
        refs = _extract_section(content, "References")
        if refs:
            all_references.append(refs)

        lines.append("---")
        lines.append("")

    # Aggregated references
    if all_references:
        lines.append("## 参考资料")
        lines.append("")
        for ref_block in all_references:
            lines.append(ref_block)
            lines.append("")

    return "\n".join(lines)


def export_study_guide(
    entries: list[dict[str, Any]],
    domain: str | None = None,
) -> str:
    """Generate a study guide with learning progression.

    Sorts entries from surface to deep, groups by sub-domain tags,
    and includes questions for self-testing.

    Args:
        entries: All loaded entries.
        domain: Optional domain key to filter.

    Returns:
        Markdown string of the study guide.
    """
    filtered = _filter_by_domain(entries, domain)
    if not filtered:
        return "<!-- 无匹配条目可导出 -->"

    # Sort by depth (surface -> intermediate -> deep)
    filtered.sort(key=_get_depth_rank)

    # Group by sub_domain or first tag
    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in filtered:
        meta = entry["metadata"]
        sub = meta.get("sub_domain", "")
        if not sub:
            tags = meta.get("tags", [])
            if isinstance(tags, list) and tags:
                sub = str(tags[0])
            elif isinstance(tags, str) and tags:
                sub = tags.split(",")[0].strip()
            else:
                sub = "通用"
        groups.setdefault(sub, []).append(entry)

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    lines: list[str] = [
        "# 学习指南",
        "",
        f"> 生成日期: {date_str}  ",
        f"> 条目总数: {len(filtered)}",
        "",
        "## 学习路径",
        "",
        "按深度从浅到深排列，建议按顺序学习。",
        "",
    ]

    for group_name, group_entries in sorted(groups.items()):
        lines.append(f"### {group_name}")
        lines.append("")

        for entry in group_entries:
            meta = entry["metadata"]
            title = meta.get("title", "未知标题")
            depth = meta.get("depth", "surface")
            content = entry["content"]

            depth_marker = {"surface": "[基础]", "intermediate": "[进阶]", "deep": "[深入]"}
            marker = depth_marker.get(str(depth).lower(), f"[{depth}]")

            lines.append(f"#### {marker} {title}")
            lines.append("")

            # Key Insights for learning
            insights = _extract_section(content, "Key Insights")
            if insights:
                lines.append("**要点:**")
                lines.append("")
                lines.append(insights)
                lines.append("")

            # Questions for self-testing
            question = _extract_section(content, "Question")
            if question:
                lines.append("**自测问题:**")
                lines.append("")
                lines.append(f"> {question}")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def export_onboarding(
    entries: list[dict[str, Any]],
    team: str | None = None,
) -> str:
    """Generate an onboarding document from team-scoped entries.

    Filters for entries with scope=team, groups by domain, and
    organizes into architecture decisions, patterns, and debug knowledge.

    Args:
        entries: All loaded entries.
        team: Optional team scope filter value.

    Returns:
        Markdown string of the onboarding document.
    """
    # Filter for team-scoped entries
    if team:
        filtered = [
            e for e in entries
            if str(e["metadata"].get("scope", "")).lower() == team.lower()
        ]
    else:
        filtered = [
            e for e in entries
            if str(e["metadata"].get("scope", "")).lower() == "team"
        ]

    if not filtered:
        return "<!-- 无匹配的团队条目可导出 -->"

    # Group by domain
    domain_groups: dict[str, list[dict[str, Any]]] = {}
    for entry in filtered:
        domain_val = entry["metadata"].get("domain", "general")
        if isinstance(domain_val, list):
            domain_val = domain_val[0] if domain_val else "general"
        domain_groups.setdefault(str(domain_val), []).append(entry)

    date_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    lines: list[str] = [
        "# 团队知识 Onboarding 文档",
        "",
        f"> 生成日期: {date_str}  ",
        f"> 条目数量: {len(filtered)}",
        "",
        "## 目录",
        "",
    ]

    # Type-based sections within each domain
    type_sections = {
        "architecture": "架构决策",
        "pattern": "开发模式",
        "debug": "调试知识",
        "principle": "核心原则",
        "research": "技术研究",
        "team": "团队规范",
        "problem": "算法题目",
    }

    for domain_key, domain_entries in sorted(domain_groups.items()):
        config = load_config()
        domain_cfg = config.get_domain(domain_key)
        domain_label = f"{domain_cfg.icon} {domain_cfg.label}" if domain_cfg else domain_key

        lines.append(f"## {domain_label}")
        lines.append("")

        # Sub-group by entry type
        type_groups: dict[str, list[dict[str, Any]]] = {}
        for entry in domain_entries:
            entry_type = str(entry["metadata"].get("type", "general"))
            type_groups.setdefault(entry_type, []).append(entry)

        for type_key, type_entries in sorted(type_groups.items()):
            section_title = type_sections.get(type_key, type_key)
            lines.append(f"### {section_title}")
            lines.append("")

            for entry in type_entries:
                meta = entry["metadata"]
                title = meta.get("title", "未知标题")
                content = entry["content"]

                lines.append(f"#### {title}")
                lines.append("")

                # Include Key Insights and Analysis
                insights = _extract_section(content, "Key Insights")
                if insights:
                    lines.append(insights)
                    lines.append("")

                analysis = _extract_section(content, "Analysis")
                if analysis:
                    lines.append(analysis)
                    lines.append("")

            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def write_export(
    content: str,
    output_path: Path | None = None,
    format_name: str = "export",
) -> Path:
    """Write exported content to a file.

    If no output_path is given, generates a default path in the
    vault root under an ``exports/`` directory.

    Args:
        content: The markdown content to write.
        output_path: Explicit output file path. If None, auto-generated.
        format_name: Label used in the default filename (e.g. "blog", "guide").

    Returns:
        The Path where the file was written.
    """
    if output_path is None:
        config = load_config()
        export_dir = config.vault_path / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        output_path = export_dir / f"{format_name}-{date_str}.md"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return output_path

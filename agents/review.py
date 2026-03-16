"""Review agent for knowledge base health assessment.

Scans entries for review triggers (staleness, low confidence, draft
status) and performs domain gap analysis against the config definitions.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agents.config import ProjectConfig, load_config
from agents.utils import load_entries

console = Console()


def _parse_date(value: Any) -> datetime | None:
    """Parse a date value from frontmatter into a timezone-aware datetime.

    Handles ISO format strings and datetime objects. Returns None
    if the value cannot be parsed.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(value, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue
    return None


def scan_for_review(
    config: ProjectConfig | None = None,
    domain_filter: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Scan all entries and flag those needing review.

    Review triggers:
    - Updated > auto_flag_outdated_days ago (default 180 days)
    - Confidence < confidence_threshold (default 0.7)
    - Status is still 'draft'

    Args:
        config: Project configuration. Auto-loaded if None.
        domain_filter: If set, only review entries in this domain.

    Returns:
        Dict with keys 'outdated', 'low_confidence', 'drafts',
        each containing a list of flagged entry dicts.
    """
    if config is None:
        config = load_config()

    filters: dict[str, Any] | None = None
    if domain_filter:
        filters = {"domain": domain_filter}

    entries = load_entries(config.vault_path, filters=filters)
    now = datetime.now(tz=timezone.utc)

    outdated_threshold_days = config.review.auto_flag_outdated_days
    confidence_threshold = config.agent.confidence_threshold

    flagged: dict[str, list[dict[str, Any]]] = {
        "outdated": [],
        "low_confidence": [],
        "drafts": [],
    }

    for entry in entries:
        meta = entry["metadata"]
        file_path: Path = entry["path"]

        entry_info: dict[str, Any] = {
            "title": meta.get("title", file_path.stem),
            "domain": meta.get("domain", "unknown"),
            "type": meta.get("type", "unknown"),
            "path": str(file_path),
        }

        # Check staleness based on updated date
        updated_date = _parse_date(meta.get("updated"))
        if updated_date:
            days_since_update = (now - updated_date).days
            if days_since_update > outdated_threshold_days:
                flagged["outdated"].append({
                    **entry_info,
                    "reason": f"距上次更新已 {days_since_update} 天 (阈值: {outdated_threshold_days})",
                    "updated": updated_date.strftime("%Y-%m-%d"),
                    "days_stale": days_since_update,
                })

        # Check confidence level
        confidence = meta.get("confidence")
        if confidence is not None:
            try:
                conf_value = float(confidence)
                if conf_value < confidence_threshold:
                    flagged["low_confidence"].append({
                        **entry_info,
                        "reason": f"置信度 {conf_value:.2f} < 阈值 {confidence_threshold}",
                        "confidence": conf_value,
                    })
            except (ValueError, TypeError):
                pass

        # Check draft status
        status = meta.get("status", "")
        if isinstance(status, str) and status.lower() == "draft":
            created_date = _parse_date(meta.get("created"))
            age_info = ""
            if created_date:
                age_days = (now - created_date).days
                age_info = f" (创建于 {age_days} 天前)"
            flagged["drafts"].append({
                **entry_info,
                "reason": f"状态仍为 draft{age_info}",
            })

    return flagged


def domain_gap_analysis(
    config: ProjectConfig | None = None,
) -> dict[str, dict[str, Any]]:
    """Analyze coverage gaps across configured domains and sub-domains.

    Compares existing entries against the domain/sub-domain definitions
    in config.yaml to identify undercovered areas.

    Args:
        config: Project configuration. Auto-loaded if None.

    Returns:
        Dict keyed by domain, each containing entry counts,
        covered sub-domains, and missing sub-domains.
    """
    if config is None:
        config = load_config()

    entries = load_entries(config.vault_path)

    # Build coverage map: domain -> set of observed sub-domains
    coverage: dict[str, set[str]] = {}
    domain_counts: dict[str, int] = {}

    for entry in entries:
        meta = entry["metadata"]
        domain = meta.get("domain", "")
        sub_domain = meta.get("sub_domain", "")

        if domain:
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
            if sub_domain:
                coverage.setdefault(domain, set()).add(sub_domain)

    # Compare against config definitions
    analysis: dict[str, dict[str, Any]] = {}
    for domain_key, domain_cfg in config.domains.items():
        defined_subs = set(domain_cfg.sub_domains)
        covered_subs = coverage.get(domain_key, set())
        missing_subs = defined_subs - covered_subs

        entry_count = domain_counts.get(domain_key, 0)
        coverage_pct = (
            len(covered_subs) / len(defined_subs) * 100.0
            if defined_subs
            else 100.0
        )

        analysis[domain_key] = {
            "label": domain_cfg.label,
            "icon": domain_cfg.icon,
            "entry_count": entry_count,
            "defined_sub_domains": sorted(defined_subs),
            "covered_sub_domains": sorted(covered_subs),
            "missing_sub_domains": sorted(missing_subs),
            "coverage_percent": round(coverage_pct, 1),
        }

    return analysis


def generate_review_report(
    config: ProjectConfig | None = None,
    domain_filter: str | None = None,
    output_path: Path | None = None,
) -> str:
    """Generate a comprehensive review report as markdown.

    Args:
        config: Project configuration. Auto-loaded if None.
        domain_filter: If set, only review entries in this domain.
        output_path: If set, write the report to this file.

    Returns:
        The markdown report string.
    """
    if config is None:
        config = load_config()

    flagged = scan_for_review(config=config, domain_filter=domain_filter)
    gaps = domain_gap_analysis(config=config)

    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = [
        f"# 知识库审查报告",
        f"",
        f"生成时间: {now_str}",
        f"",
    ]

    if domain_filter:
        lines.append(f"筛选域: **{domain_filter}**\n")

    # Section: Outdated entries
    outdated = flagged["outdated"]
    lines.append(f"## 过期条目 ({len(outdated)} 条)\n")
    if outdated:
        lines.append("| 标题 | 域 | 上次更新 | 过期天数 |")
        lines.append("|------|-----|----------|----------|")
        for item in sorted(outdated, key=lambda x: x.get("days_stale", 0), reverse=True):
            lines.append(
                f"| {item['title']} | {item['domain']} | "
                f"{item.get('updated', 'N/A')} | {item.get('days_stale', '?')} |"
            )
    else:
        lines.append("无过期条目。\n")

    # Section: Low confidence entries
    low_conf = flagged["low_confidence"]
    lines.append(f"\n## 低置信度条目 ({len(low_conf)} 条)\n")
    if low_conf:
        lines.append("| 标题 | 域 | 置信度 |")
        lines.append("|------|-----|--------|")
        for item in sorted(low_conf, key=lambda x: x.get("confidence", 0)):
            lines.append(
                f"| {item['title']} | {item['domain']} | "
                f"{item.get('confidence', 0):.2f} |"
            )
    else:
        lines.append("所有条目置信度均达标。\n")

    # Section: Draft entries
    drafts = flagged["drafts"]
    lines.append(f"\n## 草稿条目 ({len(drafts)} 条)\n")
    if drafts:
        lines.append("| 标题 | 域 | 类型 | 备注 |")
        lines.append("|------|-----|------|------|")
        for item in drafts:
            lines.append(
                f"| {item['title']} | {item['domain']} | "
                f"{item['type']} | {item['reason']} |"
            )
    else:
        lines.append("无草稿条目。\n")

    # Section: Domain coverage gaps
    lines.append(f"\n## 域覆盖分析\n")
    lines.append("| 域 | 条目数 | 覆盖率 | 缺失子域 |")
    lines.append("|-----|--------|--------|----------|")
    for domain_key in config.review.domains_priority:
        if domain_key not in gaps:
            continue
        gap = gaps[domain_key]
        missing = ", ".join(gap["missing_sub_domains"]) if gap["missing_sub_domains"] else "-"
        lines.append(
            f"| {gap['icon']} {gap['label']} | {gap['entry_count']} | "
            f"{gap['coverage_percent']}% | {missing} |"
        )
    # Include domains not in priority list
    for domain_key, gap in gaps.items():
        if domain_key in config.review.domains_priority:
            continue
        missing = ", ".join(gap["missing_sub_domains"]) if gap["missing_sub_domains"] else "-"
        lines.append(
            f"| {gap['icon']} {gap['label']} | {gap['entry_count']} | "
            f"{gap['coverage_percent']}% | {missing} |"
        )

    # Summary
    total_issues = len(outdated) + len(low_conf) + len(drafts)
    lines.append(f"\n## 总结\n")
    lines.append(f"- 待处理问题总数: **{total_issues}**")
    lines.append(f"- 过期条目: {len(outdated)}")
    lines.append(f"- 低置信度: {len(low_conf)}")
    lines.append(f"- 草稿状态: {len(drafts)}")

    report = "\n".join(lines)

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        console.print(f"[green]报告已写入: {output_path}[/]")

    return report


def print_review_summary(
    config: ProjectConfig | None = None,
    domain_filter: str | None = None,
) -> None:
    """Print a rich-formatted review summary to the console.

    Args:
        config: Project configuration. Auto-loaded if None.
        domain_filter: If set, only review entries in this domain.
    """
    if config is None:
        config = load_config()

    flagged = scan_for_review(config=config, domain_filter=domain_filter)

    # Summary panel
    total = sum(len(v) for v in flagged.values())
    summary_text = (
        f"[bold]待审查条目总数: {total}[/]\n"
        f"  过期: [yellow]{len(flagged['outdated'])}[/]  "
        f"低置信: [red]{len(flagged['low_confidence'])}[/]  "
        f"草稿: [cyan]{len(flagged['drafts'])}[/]"
    )
    console.print(Panel(summary_text, title="知识库审查摘要", border_style="blue"))

    # Outdated entries table
    if flagged["outdated"]:
        table = Table(title="过期条目", show_lines=True)
        table.add_column("标题", style="bold")
        table.add_column("域", style="green")
        table.add_column("过期天数", style="yellow", justify="right")
        for item in sorted(
            flagged["outdated"],
            key=lambda x: x.get("days_stale", 0),
            reverse=True,
        ):
            table.add_row(
                item["title"],
                item["domain"],
                str(item.get("days_stale", "?")),
            )
        console.print(table)

    # Low confidence entries table
    if flagged["low_confidence"]:
        table = Table(title="低置信度条目", show_lines=True)
        table.add_column("标题", style="bold")
        table.add_column("域", style="green")
        table.add_column("置信度", style="red", justify="right")
        for item in sorted(
            flagged["low_confidence"],
            key=lambda x: x.get("confidence", 0),
        ):
            table.add_row(
                item["title"],
                item["domain"],
                f"{item.get('confidence', 0):.2f}",
            )
        console.print(table)

    # Draft entries table
    if flagged["drafts"]:
        table = Table(title="草稿条目", show_lines=True)
        table.add_column("标题", style="bold")
        table.add_column("域", style="green")
        table.add_column("类型", style="magenta")
        table.add_column("备注", style="dim")
        for item in flagged["drafts"]:
            table.add_row(
                item["title"],
                item["domain"],
                item["type"],
                item["reason"],
            )
        console.print(table)

    # Gap analysis
    gaps = domain_gap_analysis(config=config)
    gap_table = Table(title="域覆盖分析", show_lines=True)
    gap_table.add_column("域", style="bold")
    gap_table.add_column("条目数", justify="right")
    gap_table.add_column("覆盖率", justify="right")
    gap_table.add_column("缺失子域", style="yellow")

    for domain_key in config.review.domains_priority:
        if domain_key not in gaps:
            continue
        gap = gaps[domain_key]
        missing = ", ".join(gap["missing_sub_domains"]) or "-"
        pct = gap["coverage_percent"]
        pct_style = "green" if pct >= 80 else "yellow" if pct >= 50 else "red"
        gap_table.add_row(
            f"{gap['icon']} {gap['label']}",
            str(gap["entry_count"]),
            f"[{pct_style}]{pct}%[/]",
            missing,
        )

    console.print(gap_table)

"""`kg stats` command handler extracted from the legacy CLI module."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.table import Table

from kg.infrastructure.config.loader import load_config

if TYPE_CHECKING:
    from rich.console import Console


def run_stats_command(*, console: Console) -> None:
    """Execute the `kg stats` CLI behavior without Click wiring."""
    from agents.utils import load_entries

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)

    entries = load_entries(config.vault_path)

    if not entries:
        console.print(Panel(
            "[yellow]知识库为空。[/]\n"
            "使用 `kg ingest <file>` 开始添加知识条目。",
            title="知识库统计",
            border_style="yellow",
        ))
        return

    domain_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    depth_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}

    for entry in entries:
        meta = entry["metadata"]
        d = meta.get("domain", "unknown")
        t = meta.get("type", "unknown")
        dp = meta.get("depth", "unknown")
        s = meta.get("status", "unknown")

        domains = d if isinstance(d, list) else [d]
        for dk in domains:
            domain_counts[dk] = domain_counts.get(dk, 0) + 1
        type_counts[t] = type_counts.get(t, 0) + 1
        depth_counts[dp] = depth_counts.get(dp, 0) + 1
        status_counts[s] = status_counts.get(s, 0) + 1

    console.print(Panel(
        f"[bold]总条目数: {len(entries)}[/]\n"
        f"知识域数: {len(domain_counts)}  "
        f"类型数: {len(type_counts)}  "
        f"深度层级: {len(depth_counts)}",
        title="知识库统计",
        border_style="blue",
    ))

    domain_table = Table(title="知识域分布", show_lines=True)
    domain_table.add_column("域", style="bold cyan")
    domain_table.add_column("条目数", justify="right", style="green")
    domain_table.add_column("占比", justify="right")

    for domain_key, count in sorted(
        domain_counts.items(), key=lambda x: x[1], reverse=True,
    ):
        domain_cfg = config.get_domain(domain_key)
        label = f"{domain_cfg.icon} {domain_cfg.label}" if domain_cfg else domain_key
        pct = count / len(entries) * 100
        domain_table.add_row(label, str(count), f"{pct:.1f}%")
    console.print(domain_table)

    type_table = Table(title="条目类型分布", show_lines=True)
    type_table.add_column("类型", style="bold magenta")
    type_table.add_column("条目数", justify="right", style="green")

    for type_key, count in sorted(
        type_counts.items(), key=lambda x: x[1], reverse=True,
    ):
        type_cfg = config.get_entry_type(type_key)
        label = type_cfg.label if type_cfg else type_key
        type_table.add_row(label, str(count))
    console.print(type_table)

    depth_table = Table(title="深度层级分布", show_lines=True)
    depth_table.add_column("层级", style="bold yellow")
    depth_table.add_column("条目数", justify="right", style="green")

    for depth_key, count in sorted(
        depth_counts.items(), key=lambda x: x[1], reverse=True,
    ):
        label = config.depth_levels.get(depth_key, depth_key)
        depth_table.add_row(label, str(count))
    console.print(depth_table)

    status_table = Table(title="状态分布", show_lines=True)
    status_table.add_column("状态", style="bold")
    status_table.add_column("条目数", justify="right", style="green")

    for status_key, count in sorted(
        status_counts.items(), key=lambda x: x[1], reverse=True,
    ):
        style = "green" if status_key == "validated" else (
            "yellow" if status_key == "draft" else "white"
        )
        status_table.add_row(f"[{style}]{status_key}[/]", str(count))
    console.print(status_table)

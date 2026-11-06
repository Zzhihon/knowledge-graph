"""`kg diff` command handler extracted from the legacy CLI module."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.syntax import Syntax

from kg.infrastructure.config.loader import load_config

if TYPE_CHECKING:
    from rich.console import Console


def run_diff_command(
    *,
    entry_id: str,
    limit: int,
    stats_only: bool,
    console: Console,
) -> None:
    """Execute the `kg diff` CLI behavior without Click wiring."""
    from agents.diff_store import DiffStore
    from agents.graph_store import get_graph_store

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)

    try:
        graph_store = get_graph_store(config)
        graph_store.connect()
    except Exception as exc:
        console.print(f"[red]无法连接图数据库: {exc}[/]")
        console.print("[dim]提示: 请先运行 `kg sync` 构建图数据库。[/]")
        sys.exit(1)

    try:
        diff_store = DiffStore(graph_store)

        if stats_only:
            stats = diff_store.get_stats(entry_id)
            if stats["total_changes"] == 0:
                console.print(f"[yellow]条目 {entry_id} 无变更记录。[/]")
                return

            console.print(Panel(
                f"[bold]总变更次数:[/] {stats['total_changes']}\n"
                f"[bold]最近修改:[/] {stats['last_modified']}\n"
                f"[bold]总新增行:[/] [green]+{stats['total_additions']}[/]\n"
                f"[bold]总删除行:[/] [red]-{stats['total_deletions']}[/]",
                title=f"条目演化统计: {entry_id}",
                border_style="blue",
            ))
            return

        history = diff_store.get_history(entry_id, limit=limit)

        if not history:
            console.print(f"[yellow]条目 {entry_id} 无变更记录。[/]")
            console.print("[dim]提示: 变更记录在 `kg sync` 时自动生成。[/]")
            return

        console.print(Panel(
            f"[bold]共 {len(history)} 条变更记录[/]",
            title=f"条目演化历史: {entry_id}",
            border_style="blue",
        ))

        type_style = {"created": "green", "modified": "yellow", "deleted": "red"}

        for record in history:
            change_type = record.get("change_type", "unknown")
            timestamp = record.get("timestamp", "?")
            stats = record.get("stats", {})
            if not isinstance(stats, dict):
                stats = {}
            additions = stats.get("additions", 0)
            deletions = stats.get("deletions", 0)
            style = type_style.get(change_type, "white")

            header = (
                f"[{style}]{change_type}[/{style}]  "
                f"{timestamp}  "
                f"[green]+{additions}[/] [red]-{deletions}[/]"
            )
            console.print(f"\n{header}")

            diff_text = record.get("diff_text", "")
            if diff_text:
                console.print(Syntax(diff_text, "diff", theme="monokai", line_numbers=False))
            elif change_type == "created":
                console.print("[dim]  (首次创建，无差异对比)[/]")
    finally:
        graph_store.close()

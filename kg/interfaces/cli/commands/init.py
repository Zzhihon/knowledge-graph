"""`kg init` command handler extracted from the legacy CLI module."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.table import Table

from kg.infrastructure.config.loader import load_config

if TYPE_CHECKING:
    from rich.console import Console


def run_init_command(*, console: Console) -> None:
    """Execute the `kg init` CLI behavior without Click wiring."""
    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)

    vault = config.vault_path
    console.print(f"[bold blue]知识库根目录:[/] {vault}\n")

    required_dirs = [
        "00-Index",
        "01-Principles",
        "02-Patterns",
        "03-Debug",
        "04-Architecture",
        "05-Research",
        "06-Team",
        "07-Projects",
        "08-Problems",
        "agents",
        "Assets",
        "Templates",
    ]

    system_dirs = [
        "indexes",
        "indexes/weaviate",
        "indexes/surrealdb",
    ]

    all_dirs = required_dirs + system_dirs
    created = 0
    verified = 0

    table = Table(show_header=True, show_lines=False)
    table.add_column("目录", style="cyan")
    table.add_column("状态", width=12)

    for dir_name in all_dirs:
        dir_path = vault / dir_name
        if dir_path.is_dir():
            table.add_row(dir_name, "[green]已存在[/]")
            verified += 1
        else:
            dir_path.mkdir(parents=True, exist_ok=True)
            table.add_row(dir_name, "[yellow]已创建[/]")
            created += 1

    console.print(table)

    config_file = vault / "config.yaml"
    if config_file.is_file():
        console.print("\n[green]config.yaml 已验证[/]")
    else:
        console.print("\n[red]config.yaml 缺失![/]")

    console.print(Panel(
        f"已验证: {verified} 个目录\n"
        f"已创建: {created} 个目录\n"
        f"知识库名称: {config.name}\n"
        f"作者: {config.author}",
        title="初始化完成",
        border_style="green",
    ))

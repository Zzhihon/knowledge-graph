"""`kg graph` and `kg backlinks` command handlers extracted from the legacy CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from kg.infrastructure.config.loader import load_config

if TYPE_CHECKING:
    from rich.console import Console


def run_graph_command(
    *,
    entry_id: str,
    depth: int,
    fmt: str,
    output: str | None,
    console: Console,
) -> None:
    """Execute the `kg graph` CLI behavior without Click wiring."""
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
        entry = graph_store.get_entry(entry_id)
        if entry is None:
            console.print(f"[red]未找到条目: {entry_id}[/]")
            console.print("[dim]提示: 请先运行 `kg sync` 同步图数据库。[/]")
            graph_store.close()
            sys.exit(1)

        if fmt in ("mermaid", "canvas"):
            from agents.graph_viz import build_graph_data, to_canvas, to_mermaid

            graph_data = build_graph_data(graph_store, entry_id, depth)

            if fmt == "mermaid":
                mermaid_str = to_mermaid(graph_data)
                if output:
                    Path(output).parent.mkdir(parents=True, exist_ok=True)
                    Path(output).write_text(mermaid_str)
                    console.print(f"[green]Mermaid 已写入: {output}[/]")
                else:
                    console.print(mermaid_str)
            else:
                canvas_data = to_canvas(graph_data, config.vault_path)
                out_path = output or str(
                    config.vault_path / "graphs" / f"graph-{entry_id}.canvas"
                )
                Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                Path(out_path).write_text(
                    json.dumps(canvas_data, ensure_ascii=False, indent=2)
                )
                console.print(f"[green]Canvas 已写入: {out_path}[/]")
            return

        title = entry.get("title", entry_id)
        console.print(Panel(
            f"[bold]{title}[/]\n"
            f"类型: {entry.get('entry_type', '?')}  "
            f"深度: {entry.get('depth', '?')}  "
            f"置信度: {entry.get('confidence', 0):.0%}",
            title=f"条目: {entry_id}",
            border_style="blue",
        ))

        relations = graph_store.get_relations(entry_id, direction="both")

        if relations:
            tree = Tree(f"[bold cyan]{entry_id}[/] 关系网络")
            outbound = [relation for relation in relations if relation["direction"] == "out"]
            inbound = [relation for relation in relations if relation["direction"] == "in"]

            if outbound:
                out_branch = tree.add("[bold green]出边 (→)[/]")
                for relation in outbound:
                    out_branch.add(
                        f"[green]--{relation['rel_type']}-->[/] "
                        f"[cyan]{relation['target_id']}[/]"
                    )

            if inbound:
                in_branch = tree.add("[bold yellow]入边 (←)[/]")
                for relation in inbound:
                    in_branch.add(
                        f"[yellow]<--{relation['rel_type']}--[/] "
                        f"[cyan]{relation['target_id']}[/]"
                    )

            console.print(tree)
        else:
            console.print("[dim]无关系边。[/]")

        if depth >= 2:
            neighborhood = graph_store.neighborhood(entry_id, depth=depth)
            nodes = neighborhood.get("nodes", [])
            if nodes:
                console.print(f"\n[bold]{depth}-跳邻居 ({len(nodes)} 个节点):[/]")
                nb_table = Table(show_lines=False)
                nb_table.add_column("ID", style="cyan", max_width=45)
                nb_table.add_column("标题", style="white", max_width=35)
                nb_table.add_column("类型", style="magenta", width=12)

                for node in nodes[:20]:
                    node_id = node.get("id", "")
                    if isinstance(node_id, dict):
                        node_id = str(node_id)
                    if str(node_id).startswith("entry:"):
                        node_id = str(node_id)[6:].strip("`").strip("⟨").strip("⟩")
                    nb_table.add_row(
                        str(node_id),
                        node.get("title", ""),
                        node.get("entry_type", ""),
                    )

                console.print(nb_table)
                if len(nodes) > 20:
                    console.print(f"[dim]  ... 还有 {len(nodes) - 20} 个节点[/]")
    finally:
        graph_store.close()


def run_backlinks_command(*, entry_id: str, console: Console) -> None:
    """Execute the `kg backlinks` CLI behavior without Click wiring."""
    from agents.backlinks import find_backlinks, print_backlinks

    results = find_backlinks(entry_id)
    print_backlinks(entry_id, results)

    if results:
        console.print(f"\n[dim]共 {len(results)} 个条目引用了 {entry_id}。[/]")

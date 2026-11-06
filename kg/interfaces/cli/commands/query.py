"""`kg query` command handler extracted from the legacy CLI module."""

from __future__ import annotations

import json
import sys

from rich.console import Console


def run_query_command(
    *,
    question: str | None,
    build: bool,
    domain: str | None,
    entry_type: str | None,
    depth: str | None,
    status: str | None,
    top_k: int,
    output_format: str,
    console: Console,
) -> None:
    """Execute the `kg query` CLI behavior without Click wiring."""
    if build:
        from kg.application.services.search_service import build_index

        count = build_index()
        console.print(f"[green]索引构建完成，共 {count} 个条目。[/]")
        console.print("[dim]提示: 推荐使用 `kg sync` 同时构建向量索引和图数据库。[/]")
        return

    from kg.application.services.search_service import print_results, search

    if output_format == "json":
        import agents.query as query_module

        query_module.console = Console(stderr=True)

    if not question:
        console.print("[red]请提供查询问题，或使用 --build 构建索引。[/]")
        console.print("用法: kg query '你的问题' [选项]")
        console.print("[dim]提示: 首次使用前请先运行 `kg sync` 构建索引。[/]")
        sys.exit(1)

    filters: dict[str, str] = {}
    if domain:
        filters["domain"] = domain
    if entry_type:
        filters["type"] = entry_type
    if depth:
        filters["depth"] = depth
    if status:
        filters["status"] = status

    results = search(
        query=question,
        filters=filters if filters else None,
        top_k=top_k,
    )

    if output_format == "json":
        print(json.dumps(results, ensure_ascii=False))
    else:
        print_results(results, question)

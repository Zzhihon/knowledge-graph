"""`kg ask` command handler extracted from the legacy CLI module."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rich.console import Console


def run_ask_command(
    *,
    question: str,
    top_k: int,
    domain: str | None,
    no_graph: bool,
    no_fallback: bool,
    console: Console,
) -> None:
    """Execute the `kg ask` CLI behavior without Click wiring."""
    from kg.application.services.ask_service import ask as do_ask

    try:
        do_ask(
            question=question,
            top_k=top_k,
            domain=domain,
            use_graph=not no_graph,
            enable_fallback=not no_fallback,
        )
    except RuntimeError as exc:
        console.print(f"[red]问答错误: {exc}[/]")
        sys.exit(1)

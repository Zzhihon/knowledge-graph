"""`kg review` command handler extracted from the legacy CLI module."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console


def run_review_command(
    *,
    domain: str | None,
    output: Path | None,
    report: bool,
    console: Console,
) -> None:
    """Execute the `kg review` CLI behavior without Click wiring."""
    from kg.application.services.review_service import (
        generate_review_report,
        print_review_summary,
    )

    if report or output:
        report_text = generate_review_report(
            domain_filter=domain,
            output_path=output,
        )
        if not output:
            console.print(report_text)
    else:
        print_review_summary(domain_filter=domain)

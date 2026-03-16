#!/usr/bin/env python3
"""Daily review reminder — checks for overdue knowledge entries and sends macOS notification."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

# Ensure the project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.config import load_config
from agents.utils import load_entries


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                from datetime import datetime
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def check_overdue() -> list[dict]:
    """Find entries with overdue review_date or never-reviewed old entries."""
    config = load_config()
    entries = load_entries(config.vault_path)
    today = date.today()
    overdue = []

    for entry in entries:
        meta = entry["metadata"]
        review_date = _parse_date(meta.get("review_date"))
        created_date = _parse_date(meta.get("created"))

        if review_date is not None and review_date <= today:
            overdue.append(entry)
        elif review_date is None and created_date is not None:
            days_old = (today - created_date).days
            if days_old > 7:
                overdue.append(entry)

    return overdue


def send_notification(title: str, message: str) -> None:
    """Send macOS notification via osascript."""
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        timeout=5,
    )


def main() -> None:
    overdue = check_overdue()
    log_file = PROJECT_ROOT / "scripts" / ".reminder.log"

    if not overdue:
        log_file.write_text(
            f"[{date.today()}] No overdue entries.\n",
            encoding="utf-8",
        )
        return

    # Group by domain
    domain_counts: dict[str, int] = {}
    for entry in overdue:
        domains = entry["metadata"].get("domain", [])
        if isinstance(domains, str):
            domains = [domains]
        for d in domains:
            domain_counts[d] = domain_counts.get(d, 0) + 1

    # Build notification message
    domain_summary = ", ".join(f"{d}({c})" for d, c in sorted(domain_counts.items()))
    message = f"{len(overdue)} 条待复习: {domain_summary}"

    send_notification("Knowledge Graph 复习提醒", message)

    # Log
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{date.today()}] {message}\n")


if __name__ == "__main__":
    main()

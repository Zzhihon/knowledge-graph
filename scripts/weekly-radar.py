#!/usr/bin/env python3
"""Weekly knowledge radar — computes domain strengths and alerts on weak areas."""

from __future__ import annotations

import subprocess
import sys
from datetime import date
from pathlib import Path

# Ensure the project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agents.config import load_config
from agents.radar import compute_all_strengths


def send_notification(title: str, message: str) -> None:
    """Send macOS notification via osascript."""
    script = f'display notification "{message}" with title "{title}"'
    subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        timeout=5,
    )


def main() -> None:
    config = load_config()
    strengths = compute_all_strengths(config)
    log_file = PROJECT_ROOT / "scripts" / ".radar.log"

    if not strengths:
        return

    # Identify weak domains (overall < 0.4) and strong domains (>= 0.7)
    weak = []
    strong = []
    for domain_key, metrics in strengths.items():
        overall = (
            metrics["coverage"] * 0.3
            + metrics["depth_score"] * 0.3
            + metrics["freshness"] * 0.2
            + metrics["avg_confidence"] * 0.2
        )
        domain_cfg = config.domains.get(domain_key)
        domain_name = f"{domain_cfg.icon} {domain_cfg.label}" if domain_cfg else domain_key
        if overall < 0.4:
            weak.append((domain_name, overall))
        elif overall >= 0.7:
            strong.append((domain_name, overall))

    # Build notification
    parts = []
    if weak:
        weak_names = ", ".join(f"{n}({v:.0%})" for n, v in weak)
        parts.append(f"薄弱: {weak_names}")
    if strong:
        strong_names = ", ".join(f"{n}({v:.0%})" for n, v in strong)
        parts.append(f"强势: {strong_names}")

    total_entries = sum(m.get("count", 0) for m in strengths.values())
    summary = f"共 {total_entries} 条目 | {len(strong)} 域强势, {len(weak)} 域薄弱"

    if parts:
        message = " | ".join(parts)
    else:
        message = summary

    send_notification("Knowledge Graph 周报", message)

    # Log
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{date.today()}] {summary}\n")
        for domain_key, metrics in strengths.items():
            overall = (
                metrics["coverage"] * 0.3
                + metrics["depth_score"] * 0.3
                + metrics["freshness"] * 0.2
                + metrics["avg_confidence"] * 0.2
            )
            f.write(f"  {domain_key}: {overall:.0%} (cov={metrics['coverage']:.0%} "
                    f"dep={metrics['depth_score']:.0%} fresh={metrics['freshness']:.0%} "
                    f"conf={metrics['avg_confidence']:.0%})\n")


if __name__ == "__main__":
    main()

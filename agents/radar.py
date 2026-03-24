"""Knowledge strength scoring and radar visualization.

Computes per-domain knowledge metrics including coverage, depth,
freshness, and confidence to produce a strength assessment of the
entire knowledge base.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from rich.console import Console
from rich.table import Table

from agents.config import ProjectConfig, load_config
from agents.utils import load_entries

console = Console()

# Freshness window in days
_FRESHNESS_DAYS = 90

# Weight configuration for overall score
_WEIGHTS: dict[str, float] = {
    "coverage": 0.3,
    "depth_score": 0.3,
    "freshness": 0.2,
    "avg_confidence": 0.2,
}


def _parse_date_loose(value: Any) -> date | None:
    """Parse a date from various frontmatter representations."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


def compute_domain_strength(
    domain_key: str,
    entries: list[dict[str, Any]],
    config: ProjectConfig,
) -> dict[str, float | int]:
    """Compute knowledge strength metrics for a single domain.

    Metrics (all scores 0.0 - 1.0 unless noted):
        - coverage: proportion of sub-domains with at least one entry
        - depth_score: weighted depth (deep*3 + intermediate*2 + surface*1) / (total*3)
        - freshness: proportion of entries updated within the last 90 days
        - avg_confidence: mean confidence across domain entries
        - total_entries: raw count (integer, not normalized)

    Args:
        domain_key: The domain key to evaluate.
        entries: All loaded entries (pre-filtering happens here).
        config: Project configuration for sub-domain definitions.

    Returns:
        Dict of metric name to value.
    """
    domain_cfg = config.get_domain(domain_key)
    sub_domains = domain_cfg.sub_domains if domain_cfg else []

    # Filter entries belonging to this domain
    domain_entries = [
        e for e in entries
        if _entry_matches_domain(e, domain_key)
    ]

    total = len(domain_entries)
    if total == 0:
        return {
            "coverage": 0.0,
            "depth_score": 0.0,
            "freshness": 0.0,
            "avg_confidence": 0.0,
            "total_entries": 0,
        }

    # Coverage: sub-domains with at least 1 entry / total sub-domains
    if sub_domains:
        covered_subs = set()
        for entry in domain_entries:
            meta = entry["metadata"]
            sub = meta.get("sub_domain", "")
            tags = meta.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]
            # Check sub_domain field and tags for sub-domain matches
            for sd in sub_domains:
                sd_lower = sd.lower()
                if (isinstance(sub, str) and sub.lower() == sd_lower) or sd_lower in [
                    str(t).lower() for t in tags
                ]:
                    covered_subs.add(sd_lower)
        coverage = len(covered_subs) / len(sub_domains)
    else:
        # No sub-domains defined: coverage is 1.0 if any entries exist
        coverage = 1.0

    # Depth score: weighted sum
    depth_map = {"deep": 3, "intermediate": 2, "surface": 1}
    depth_sum = 0
    for entry in domain_entries:
        depth = entry["metadata"].get("depth", "surface")
        depth_sum += depth_map.get(str(depth).lower(), 1)
    depth_score = depth_sum / (total * 3)

    # Freshness: entries updated in last N days
    today = date.today()
    cutoff = today - timedelta(days=_FRESHNESS_DAYS)
    fresh_count = 0
    for entry in domain_entries:
        updated = _parse_date_loose(entry["metadata"].get("updated"))
        if updated is None:
            updated = _parse_date_loose(entry["metadata"].get("created"))
        if updated is not None and updated >= cutoff:
            fresh_count += 1
    freshness = fresh_count / total

    # Average confidence
    confidence_sum = 0.0
    for entry in domain_entries:
        confidence = entry["metadata"].get("confidence", 0.5)
        try:
            confidence_sum += float(confidence if confidence is not None else 0.5)
        except (TypeError, ValueError):
            confidence_sum += 0.5
    avg_confidence = confidence_sum / total

    return {
        "coverage": round(coverage, 4),
        "depth_score": round(depth_score, 4),
        "freshness": round(freshness, 4),
        "avg_confidence": round(avg_confidence, 4),
        "total_entries": total,
    }


def _entry_matches_domain(entry: dict[str, Any], domain_key: str) -> bool:
    """Check if an entry belongs to the given domain."""
    meta = entry["metadata"]
    domain_val = meta.get("domain", "")
    if isinstance(domain_val, list):
        return any(str(d).lower() == domain_key.lower() for d in domain_val)
    return str(domain_val).lower() == domain_key.lower()


def compute_all_strengths(
    config: ProjectConfig | None = None,
) -> dict[str, dict[str, float | int]]:
    """Compute strength metrics for every configured domain.

    Args:
        config: Project configuration. Auto-loaded if None.

    Returns:
        Dict mapping domain_key to its strength metrics dict.
    """
    if config is None:
        config = load_config()

    entries = load_entries(config.vault_path)
    strengths: dict[str, dict[str, float | int]] = {}

    for domain_key in config.domain_keys:
        strengths[domain_key] = compute_domain_strength(domain_key, entries, config)

    return strengths


def _score_style(value: float) -> str:
    """Return a Rich style string based on score thresholds."""
    if value >= 0.7:
        return "green"
    if value >= 0.4:
        return "yellow"
    return "red"


def _format_pct(value: float) -> str:
    """Format a 0.0-1.0 float as a percentage string."""
    return f"{value:.0%}"


def print_radar(
    strengths: dict[str, dict[str, float | int]],
    config: ProjectConfig | None = None,
) -> None:
    """Display knowledge strength scores as a Rich table.

    Each domain row shows coverage, depth, freshness, confidence,
    and a weighted overall score. Values are color-coded:
    green >= 0.7, yellow 0.4-0.7, red < 0.4.

    Args:
        strengths: Output from compute_all_strengths().
        config: Project configuration for domain labels/icons.
    """
    if config is None:
        config = load_config()

    if not strengths:
        console.print("[yellow]无域强度数据可显示。[/]")
        return

    table = Table(
        title="知识域强度雷达",
        show_lines=True,
    )
    table.add_column("域", style="bold", min_width=20)
    table.add_column("条目", justify="right", width=6)
    table.add_column("覆盖率", justify="right", width=8)
    table.add_column("深度", justify="right", width=8)
    table.add_column("新鲜度", justify="right", width=8)
    table.add_column("置信度", justify="right", width=8)
    table.add_column("综合", justify="right", width=8, style="bold")

    for domain_key, metrics in sorted(strengths.items()):
        domain_cfg = config.get_domain(domain_key)
        if domain_cfg:
            label = f"{domain_cfg.icon} {domain_cfg.label}"
        else:
            label = domain_key

        coverage = float(metrics["coverage"])
        depth = float(metrics["depth_score"])
        freshness = float(metrics["freshness"])
        confidence = float(metrics["avg_confidence"])
        total = int(metrics["total_entries"])

        overall = (
            _WEIGHTS["coverage"] * coverage
            + _WEIGHTS["depth_score"] * depth
            + _WEIGHTS["freshness"] * freshness
            + _WEIGHTS["avg_confidence"] * confidence
        )

        table.add_row(
            label,
            str(total),
            f"[{_score_style(coverage)}]{_format_pct(coverage)}[/]",
            f"[{_score_style(depth)}]{_format_pct(depth)}[/]",
            f"[{_score_style(freshness)}]{_format_pct(freshness)}[/]",
            f"[{_score_style(confidence)}]{_format_pct(confidence)}[/]",
            f"[{_score_style(overall)}]{_format_pct(overall)}[/]",
        )

    console.print(table)

    # Legend
    console.print(
        "\n[dim]综合 = 覆盖率(0.3) + 深度(0.3) + 新鲜度(0.2) + 置信度(0.2)[/]"
    )
    console.print(
        "[dim]色标: [green]>=70%[/] | [yellow]40%-70%[/] | [red]<40%[/][/]"
    )

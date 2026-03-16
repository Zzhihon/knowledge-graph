"""Backlink discovery for knowledge graph entries.

Combines two sources to find which entries reference a given entry:
1. SurrealDB graph relations (direction="in")
2. Markdown [[wiki links]] scanning across all entries
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from rich.console import Console
from rich.table import Table

from agents.config import ProjectConfig, load_config
from agents.utils import load_entries

console = Console()

_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


@dataclass
class Backlink:
    """A single backlink pointing to the target entry."""

    source_id: str
    source_title: str
    source_domain: str
    link_type: str  # "graph_relation" | "wiki_link"
    rel_type: str   # "references" | "prerequisites" | "supersedes" | "wiki"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def find_backlinks(
    entry_id: str,
    config: ProjectConfig | None = None,
) -> list[Backlink]:
    """Find all entries that reference the given entry_id.

    Merges results from SurrealDB graph inbound edges and
    markdown wiki-link scanning. Deduplicates by source_id.

    Args:
        entry_id: Target entry ID to find backlinks for.
        config: Project configuration. Auto-loaded if None.

    Returns:
        List of Backlink objects sorted by source_id.
    """
    if config is None:
        config = load_config()

    graph_results = _graph_backlinks(entry_id, config)
    wiki_results = _wiki_backlinks(entry_id, config)

    # Merge: graph results take priority over wiki for same source_id
    seen: dict[str, Backlink] = {}
    for bl in graph_results:
        seen[bl.source_id] = bl
    for bl in wiki_results:
        if bl.source_id not in seen:
            seen[bl.source_id] = bl

    return sorted(seen.values(), key=lambda b: b.source_id)


def _graph_backlinks(entry_id: str, config: ProjectConfig) -> list[Backlink]:
    """Find backlinks via SurrealDB inbound graph relations."""
    try:
        from agents.graph_store import get_graph_store

        gs = get_graph_store(config)
        gs.connect()
    except Exception:
        return []

    backlinks: list[Backlink] = []
    try:
        relations = gs.get_relations(entry_id, direction="in")
        for rel in relations:
            source_id = rel["target_id"]  # "in" direction: target_id is the source
            # Look up source entry metadata
            source_entry = gs.get_entry(source_id)
            source_title = ""
            source_domain = ""
            if source_entry:
                source_title = source_entry.get("title", "")
                domain = source_entry.get("domain", "")
                if isinstance(domain, list):
                    source_domain = domain[0] if domain else ""
                else:
                    source_domain = str(domain)

            backlinks.append(Backlink(
                source_id=source_id,
                source_title=source_title or source_id,
                source_domain=source_domain,
                link_type="graph_relation",
                rel_type=rel.get("rel_type", "references"),
            ))
    except Exception:
        pass
    finally:
        gs.close()

    return backlinks


def _wiki_backlinks(entry_id: str, config: ProjectConfig) -> list[Backlink]:
    """Find backlinks by scanning all entries for [[wiki links]].

    Matches against both the entry_id and the entry title.
    """
    entries = load_entries(config.vault_path)
    if not entries:
        return []

    # Find the target entry's title for matching
    target_title = ""
    for entry in entries:
        if entry["metadata"].get("id", "") == entry_id:
            target_title = entry["metadata"].get("title", "")
            break

    # Build match targets (lowercased)
    match_targets: set[str] = {entry_id.lower()}
    if target_title:
        match_targets.add(target_title.strip().lower())

    backlinks: list[Backlink] = []
    for entry in entries:
        source_id = entry["metadata"].get("id", "")
        if not source_id or source_id == entry_id:
            continue

        # Extract all wiki links from content and frontmatter related field
        wiki_targets: set[str] = set()

        # Body wiki links
        for m in _WIKI_LINK_RE.finditer(entry.get("content", "")):
            wiki_targets.add(m.group(1).strip().lower())

        # Frontmatter related field
        for link_raw in entry["metadata"].get("related", []) or []:
            if isinstance(link_raw, str):
                m = _WIKI_LINK_RE.search(link_raw)
                target = m.group(1) if m else link_raw.strip()
                wiki_targets.add(target.lower())

        # Check if any wiki link matches our target
        if match_targets & wiki_targets:
            domain = entry["metadata"].get("domain", "")
            if isinstance(domain, list):
                source_domain = domain[0] if domain else ""
            else:
                source_domain = str(domain)

            backlinks.append(Backlink(
                source_id=source_id,
                source_title=entry["metadata"].get("title", source_id),
                source_domain=source_domain,
                link_type="wiki_link",
                rel_type="wiki",
            ))

    return backlinks


def print_backlinks(entry_id: str, backlinks: list[Backlink]) -> None:
    """Display backlinks as a Rich table."""
    if not backlinks:
        console.print(f"[yellow]条目 {entry_id} 没有被其他条目引用。[/]")
        return

    table = Table(
        title=f"反向链接: {entry_id} ({len(backlinks)} 条引用)",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("来源条目 ID", style="cyan", max_width=45)
    table.add_column("标题", style="white", max_width=30)
    table.add_column("域", style="magenta", width=12)
    table.add_column("链接类型", style="green", width=14)
    table.add_column("关系", style="dim", width=14)

    for idx, bl in enumerate(backlinks, 1):
        type_style = "blue" if bl.link_type == "graph_relation" else "yellow"
        table.add_row(
            str(idx),
            bl.source_id,
            bl.source_title,
            bl.source_domain,
            f"[{type_style}]{bl.link_type}[/]",
            bl.rel_type,
        )

    console.print(table)

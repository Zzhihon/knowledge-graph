"""Evolution tracking for knowledge graph entries.

Builds and displays supersedes chains using SurrealDB graph
traversal. Falls back to markdown-based walking if the graph
store is unavailable.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from agents.config import load_config
from agents.utils import load_entries

console = Console()


def _parse_date_for_display(value: Any) -> str:
    """Parse a date value into a display string."""
    if value is None:
        return "未知日期"
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value).strip()


def _find_entry_by_id(
    entry_id: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find an entry by its ID field in metadata."""
    entry_id_lower = entry_id.lower()
    for entry in entries:
        meta_id = entry["metadata"].get("id", "")
        if str(meta_id).lower() == entry_id_lower:
            return entry
    return None


def build_supersedes_chain(
    entry_id: str,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the full supersedes chain for an entry.

    Tries SurrealDB graph traversal first (following supersedes edges),
    then falls back to markdown-based chain walking.

    Args:
        entry_id: The knowledge entry ID to trace.
        entries: All loaded entries.

    Returns:
        Ordered list of entries from oldest to newest in the chain.
        Empty list if the entry is not found.
    """
    start = _find_entry_by_id(entry_id, entries)
    if start is None:
        return []

    # Try SurrealDB graph traversal first
    chain = _build_chain_from_graph(entry_id, entries)
    if chain:
        return chain

    # Fallback: walk markdown frontmatter
    return _build_chain_from_markdown(entry_id, entries, start)


def _build_chain_from_graph(
    entry_id: str,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build supersedes chain via SurrealDB graph traversal."""
    try:
        from agents.graph_store import get_graph_store
        gs = get_graph_store()
        gs.connect()
    except Exception:
        return []

    try:
        stats = gs.get_stats()
        if stats.get("entries", 0) == 0:
            gs.close()
            return []

        # Build entry lookup
        entry_by_id: dict[str, dict[str, Any]] = {}
        for entry in entries:
            eid = str(entry["metadata"].get("id", ""))
            if eid:
                entry_by_id[eid.lower()] = entry

        # Walk backward: follow supersedes edges (out direction = this entry supersedes something)
        backward: list[dict[str, Any]] = []
        visited: set[str] = {entry_id.lower()}
        current_id = entry_id

        while True:
            rels = gs.get_relations(current_id, rel_type="supersedes", direction="out")
            if not rels:
                break
            # Follow the first supersedes target (the entry that current supersedes)
            target = rels[0]["target_id"]
            target_lower = target.lower()
            if target_lower in visited:
                break
            visited.add(target_lower)
            entry = entry_by_id.get(target_lower)
            if entry is None:
                break
            backward.append(entry)
            current_id = target

        backward.reverse()

        # Walk forward: find entries that supersede this entry (in direction)
        forward: list[dict[str, Any]] = []
        current_id = entry_id
        visited_fwd: set[str] = {entry_id.lower()}

        while True:
            rels = gs.get_relations(current_id, rel_type="supersedes", direction="in")
            if not rels:
                break
            target = rels[0]["target_id"]
            target_lower = target.lower()
            if target_lower in visited_fwd:
                break
            visited_fwd.add(target_lower)
            entry = entry_by_id.get(target_lower)
            if entry is None:
                break
            forward.append(entry)
            current_id = target

        start_entry = entry_by_id.get(entry_id.lower())
        if start_entry is None:
            gs.close()
            return []

        gs.close()
        return backward + [start_entry] + forward

    except Exception:
        gs.close()
        return []


def _build_chain_from_markdown(
    entry_id: str,
    entries: list[dict[str, Any]],
    start: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build supersedes chain by walking markdown frontmatter."""
    # Build index: id -> entry for fast lookup
    id_index: dict[str, dict[str, Any]] = {}
    for entry in entries:
        eid = str(entry["metadata"].get("id", ""))
        if eid:
            id_index[eid.lower()] = entry

    # Build reverse index: superseded_id -> list of entries that supersede it
    superseded_by: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        sup = entry["metadata"].get("supersedes", "")
        if sup:
            sup_ids = [sup] if isinstance(sup, str) else list(sup)
            for sid in sup_ids:
                sid_lower = str(sid).strip().lower()
                if sid_lower:
                    superseded_by.setdefault(sid_lower, []).append(entry)

    # Walk backward: follow supersedes field to find predecessors
    chain_backward: list[dict[str, Any]] = []
    visited: set[str] = set()
    current = start

    while current is not None:
        current_id = str(current["metadata"].get("id", "")).lower()
        if current_id in visited:
            break
        visited.add(current_id)

        sup = current["metadata"].get("supersedes", "")
        if not sup:
            break

        sup_ids = [sup] if isinstance(sup, str) else list(sup)
        prev_id = str(sup_ids[0]).strip().lower()
        prev_entry = id_index.get(prev_id)
        if prev_entry is None:
            break
        chain_backward.append(prev_entry)
        current = prev_entry

    chain_backward.reverse()

    # Walk forward: find entries that supersede the current one
    chain_forward: list[dict[str, Any]] = []
    current_id = str(start["metadata"].get("id", "")).lower()
    visited_forward: set[str] = {current_id}

    while True:
        successors = superseded_by.get(current_id, [])
        if not successors:
            break
        next_entry = successors[0]
        next_id = str(next_entry["metadata"].get("id", "")).lower()
        if next_id in visited_forward:
            break
        visited_forward.add(next_id)
        chain_forward.append(next_entry)
        current_id = next_id

    return chain_backward + [start] + chain_forward


def find_related_evolution(
    entry: dict[str, Any],
    all_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find entries related to this one via graph or domain overlap.

    Tries SurrealDB graph relations first, then falls back to
    domain/tag matching.

    Args:
        entry: The reference entry.
        all_entries: All loaded entries.

    Returns:
        List of potentially related entries sorted by creation date.
    """
    entry_id = str(entry["metadata"].get("id", ""))

    # Try graph-based discovery
    if entry_id:
        graph_results = _find_related_via_graph(entry_id, all_entries)
        if graph_results:
            return graph_results

    # Fallback: domain/tag matching
    return _find_related_via_tags(entry, all_entries)


def _find_related_via_graph(
    entry_id: str,
    all_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find related entries via SurrealDB graph relations."""
    try:
        from agents.graph_store import get_graph_store
        gs = get_graph_store()
        gs.connect()
    except Exception:
        return []

    try:
        rels = gs.get_relations(entry_id, direction="both")
        gs.close()

        if not rels:
            return []

        related_ids = {r["target_id"].lower() for r in rels}

        candidates: list[dict[str, Any]] = []
        for e in all_entries:
            eid = str(e["metadata"].get("id", "")).lower()
            if eid in related_ids and eid != entry_id.lower():
                candidates.append(e)

        candidates.sort(
            key=lambda e: str(e["metadata"].get("created", "0000-00-00"))
        )
        return candidates
    except Exception:
        gs.close()
        return []


def _find_related_via_tags(
    entry: dict[str, Any],
    all_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find related entries via domain and tag overlap."""
    meta = entry["metadata"]
    entry_id = str(meta.get("id", ""))
    domain = meta.get("domain", "")
    tags = meta.get("tags", [])

    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    tag_set = {str(t).lower() for t in tags if t}

    if not domain and not tag_set:
        return []

    candidates: list[dict[str, Any]] = []
    for other in all_entries:
        other_meta = other["metadata"]
        other_id = str(other_meta.get("id", ""))

        if other_id == entry_id:
            continue

        other_domain = other_meta.get("domain", "")
        domain_match = False
        if isinstance(domain, list):
            domain_match = any(
                str(d).lower() == str(other_domain).lower()
                for d in domain
            )
        else:
            domain_match = str(domain).lower() == str(other_domain).lower()

        if not domain_match:
            continue

        other_tags = other_meta.get("tags", [])
        if isinstance(other_tags, str):
            other_tags = [t.strip() for t in other_tags.split(",")]
        other_tag_set = {str(t).lower() for t in other_tags if t}

        if tag_set & other_tag_set:
            candidates.append(other)

    candidates.sort(
        key=lambda e: str(e["metadata"].get("created", "0000-00-00"))
    )
    return candidates


def print_history(chain: list[dict[str, Any]], highlight_id: str | None = None) -> None:
    """Display an evolution chain as a Rich formatted timeline.

    Shows date, title, depth, and confidence for each entry in the
    chain with arrows between entries indicating evolution direction.
    The current/highlighted entry is visually emphasized.

    Args:
        chain: Ordered list of entries from oldest to newest.
        highlight_id: Entry ID to highlight as "current". If None,
                      the last entry in the chain is highlighted.
    """
    if not chain:
        console.print("[yellow]未找到演进历史。[/]")
        return

    if highlight_id is None and chain:
        highlight_id = str(chain[-1]["metadata"].get("id", ""))

    console.print(
        Panel(
            f"共 {len(chain)} 个版本",
            title="知识演进历史",
            border_style="blue",
        )
    )

    for idx, entry in enumerate(chain):
        meta = entry["metadata"]
        entry_id = str(meta.get("id", ""))
        title = meta.get("title", "未知标题")
        depth = meta.get("depth", "未知")
        confidence = float(meta.get("confidence", 0.0))
        created = _parse_date_for_display(meta.get("created"))

        is_current = entry_id.lower() == str(highlight_id).lower()

        # Build entry display
        entry_text = Text()
        if is_current:
            entry_text.append(">>> ", style="bold yellow")
        else:
            entry_text.append("    ", style="dim")

        entry_text.append(f"{created}", style="bold cyan" if is_current else "cyan")
        entry_text.append(f"  {title}", style="bold white" if is_current else "white")
        entry_text.append(f"\n    ", style="dim")
        entry_text.append(f"深度: {depth}", style="dim")
        entry_text.append(f"  置信度: {confidence:.0%}", style="dim")
        entry_text.append(f"  ID: {entry_id}", style="dim")

        border = "yellow" if is_current else "dim"
        console.print(Panel(entry_text, border_style=border, padding=(0, 1)))

        # Arrow between entries
        if idx < len(chain) - 1:
            sup_info = chain[idx + 1]["metadata"].get("supersedes", "")
            if sup_info:
                console.print("        [dim]|[/]")
                console.print("        [dim]v  supersedes[/]")
                console.print("        [dim]|[/]")
            else:
                console.print("        [dim]|[/]")
                console.print("        [dim]v  (related)[/]")
                console.print("        [dim]|[/]")

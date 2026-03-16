"""Auto-linking agent for knowledge graph entries.

Hybrid discovery: Qdrant (vector similarity) + SurrealDB (graph
friends-of-friends). Suggests new wiki-links where semantic overlap
or graph proximity is high but no explicit link exists.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from agents.config import ProjectConfig, load_config
from agents.utils import load_entries

console = Console()

# Pattern to match [[wiki links]] in markdown content
_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _extract_existing_links(content: str) -> set[str]:
    """Extract all wiki-link targets from markdown content.

    Returns a set of lowercased link targets for comparison.
    """
    return {match.group(1).strip().lower() for match in _WIKI_LINK_RE.finditer(content)}


def find_links(
    top_n: int = 10,
    threshold: float = 0.75,
    config: ProjectConfig | None = None,
) -> list[dict[str, Any]]:
    """Find potential connections between entries using hybrid search.

    Combines Qdrant vector similarity with SurrealDB graph traversal
    to discover links. For each entry:
    1. Get top-N similar via Qdrant vector search
    2. Get 2-hop neighbors via SurrealDB graph traversal
    3. Merge both sets, exclude existing wiki links
    4. Score: vector_similarity * 0.6 + graph_proximity * 0.4

    Args:
        top_n: Maximum number of link suggestions to return.
        threshold: Minimum combined score (0.0-1.0).
        config: Project configuration. Auto-loaded if None.

    Returns:
        List of suggestion dicts with keys: source_path, target_path,
        source_title, target_title, similarity, source.
    """
    if config is None:
        config = load_config()

    entries = load_entries(config.vault_path)
    if not entries:
        return []

    # Build entry lookup by ID
    entry_by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        eid = entry["metadata"].get("id", "")
        if eid:
            entry_by_id[eid] = entry

    # Try Qdrant vector search
    vector_suggestions = _find_vector_links(entries, entry_by_id, config)

    # Try SurrealDB graph traversal
    graph_suggestions = _find_graph_links(entries, entry_by_id, config)

    # Merge suggestions with weighted scoring
    merged = _merge_suggestions(vector_suggestions, graph_suggestions, entries)

    # Filter by threshold
    merged = [s for s in merged if s["similarity"] >= threshold]

    # Sort by score descending, take top_n
    merged.sort(key=lambda s: s["similarity"], reverse=True)
    return merged[:top_n]


def _find_vector_links(
    entries: list[dict[str, Any]],
    entry_by_id: dict[str, dict[str, Any]],
    config: ProjectConfig,
) -> dict[tuple[str, str], float]:
    """Find links via Qdrant vector similarity.

    Returns dict mapping (source_id, target_id) -> similarity score.
    """
    try:
        from agents.vector_store import get_vector_store
        store = get_vector_store(config)
        stats = store.get_stats()
        if not stats.get("exists") or stats.get("points_count", 0) == 0:
            store.close()
            return {}
    except Exception:
        return {}

    suggestions: dict[tuple[str, str], float] = {}

    try:
        for entry in entries:
            source_id = entry["metadata"].get("id", "")
            if not source_id:
                continue

            existing_links = _extract_existing_links(entry["content"])

            results = store.search_similar_to(source_id, top_k=5)
            for result in results:
                target_id = result.get("entry_id", "")
                if not target_id or target_id == source_id:
                    continue

                target_entry = entry_by_id.get(target_id)
                if target_entry:
                    target_title = target_entry["metadata"].get("title", "")
                    if target_title.strip().lower() in existing_links:
                        continue

                score = result.get("score", 0.0)
                pair_key = tuple(sorted([source_id, target_id]))
                existing = suggestions.get(pair_key, 0.0)
                suggestions[pair_key] = max(existing, score)
    except Exception:
        pass
    finally:
        store.close()

    return suggestions


def _find_graph_links(
    entries: list[dict[str, Any]],
    entry_by_id: dict[str, dict[str, Any]],
    config: ProjectConfig,
) -> dict[tuple[str, str], float]:
    """Find links via SurrealDB 2-hop graph traversal.

    Returns dict mapping (source_id, target_id) -> proximity score.
    Graph proximity: 1-hop = 1.0, 2-hop = 0.5
    """
    try:
        from agents.graph_store import get_graph_store
        gs = get_graph_store(config)
        gs.connect()
        gs_stats = gs.get_stats()
        if gs_stats.get("entries", 0) == 0:
            gs.close()
            return {}
    except Exception:
        return {}

    suggestions: dict[tuple[str, str], float] = {}

    try:
        for entry in entries:
            source_id = entry["metadata"].get("id", "")
            if not source_id:
                continue

            existing_links = _extract_existing_links(entry["content"])

            # 1-hop neighbors: score 1.0
            one_hop = gs.traverse(source_id, depth=1, direction="out")
            one_hop_ids = set()
            for node in one_hop:
                nid = node.get("_entry_id", "")
                if nid:
                    one_hop_ids.add(nid)

            # 2-hop neighbors: score 0.5
            two_hop = gs.traverse(source_id, depth=2, direction="out")
            for node in two_hop:
                target_id = node.get("_entry_id", "")
                if not target_id or target_id == source_id:
                    continue

                target_entry = entry_by_id.get(target_id)
                if target_entry:
                    target_title = target_entry["metadata"].get("title", "")
                    if target_title.strip().lower() in existing_links:
                        continue

                proximity = 1.0 if target_id in one_hop_ids else 0.5
                pair_key = tuple(sorted([source_id, target_id]))
                existing = suggestions.get(pair_key, 0.0)
                suggestions[pair_key] = max(existing, proximity)
    except Exception:
        pass
    finally:
        gs.close()

    return suggestions


def _merge_suggestions(
    vector_scores: dict[tuple[str, str], float],
    graph_scores: dict[tuple[str, str], float],
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge vector and graph suggestions with weighted scoring.

    Combined score = vector_similarity * 0.6 + graph_proximity * 0.4
    """
    entry_by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        eid = entry["metadata"].get("id", "")
        if eid:
            entry_by_id[eid] = entry

    all_pairs = set(vector_scores.keys()) | set(graph_scores.keys())
    seen_pairs: set[tuple[str, str]] = set()
    suggestions: list[dict[str, Any]] = []

    for pair in all_pairs:
        pair_key = tuple(sorted(pair))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        vec_score = vector_scores.get(pair_key, 0.0)
        graph_score = graph_scores.get(pair_key, 0.0)

        combined = vec_score * 0.6 + graph_score * 0.4

        source_id, target_id = pair_key
        source_entry = entry_by_id.get(source_id, {})
        target_entry = entry_by_id.get(target_id, {})

        source_title = source_entry.get("metadata", {}).get("title", source_id)
        target_title = target_entry.get("metadata", {}).get("title", target_id)
        source_path = str(source_entry.get("path", ""))
        target_path = str(target_entry.get("path", ""))

        source_info = []
        if vec_score > 0:
            source_info.append(f"vector:{vec_score:.2f}")
        if graph_score > 0:
            source_info.append(f"graph:{graph_score:.2f}")

        suggestions.append({
            "source_path": source_path,
            "target_path": target_path,
            "source_title": source_title,
            "target_title": target_title,
            "similarity": round(combined, 4),
            "source": " + ".join(source_info),
        })

    return suggestions


def print_suggestions(suggestions: list[dict[str, Any]]) -> None:
    """Display link suggestions as a Rich table.

    Args:
        suggestions: List of suggestion dicts from find_links().
    """
    if not suggestions:
        console.print("[yellow]未发现新的潜在链接。[/]")
        return

    table = Table(
        title=f"潜在链接建议 ({len(suggestions)} 条)",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("源条目", style="bold cyan", max_width=30)
    table.add_column("", style="dim", width=3, justify="center")
    table.add_column("目标条目", style="bold green", max_width=30)
    table.add_column("分数", style="yellow", width=8, justify="right")
    table.add_column("来源", style="dim", max_width=25)

    for idx, suggestion in enumerate(suggestions, 1):
        score = suggestion["similarity"]
        score_style = "green" if score >= 0.85 else ("yellow" if score >= 0.75 else "red")
        table.add_row(
            str(idx),
            suggestion["source_title"],
            "->",
            suggestion["target_title"],
            f"[{score_style}]{score:.2%}[/]",
            suggestion.get("source", ""),
        )

    console.print(table)


def apply_links(
    suggestions: list[dict[str, Any]],
    auto: bool = False,
) -> int:
    """Append wiki-links to source entries for each suggestion.

    For each suggestion, appends ``- [[target_title]]`` to the Related
    section of the source entry's markdown file.

    Args:
        suggestions: List of suggestion dicts from find_links().
        auto: If True, apply all links without confirmation. If False,
              print each suggestion and skip (user decides in Obsidian).

    Returns:
        Count of links actually applied.
    """
    if not auto:
        console.print(
            "[dim]自动应用已关闭。请在 Obsidian 中手动添加链接，"
            "或使用 --auto 标志自动写入。[/]"
        )
        return 0

    applied = 0
    for suggestion in suggestions:
        source_path = Path(suggestion["source_path"])
        target_title = suggestion["target_title"]

        if not source_path.is_file():
            continue

        content = source_path.read_text(encoding="utf-8")
        link_line = f"\n- [[{target_title}]]"

        # Try to append under an existing "## Related" section
        related_pattern = re.compile(r"(## Related.*?)(\n## |\Z)", re.DOTALL)
        match = related_pattern.search(content)

        if match:
            insert_pos = match.end(1)
            content = content[:insert_pos] + link_line + content[insert_pos:]
        else:
            # Append a new Related section at the end
            content = content.rstrip() + f"\n\n## Related{link_line}\n"

        source_path.write_text(content, encoding="utf-8")
        applied += 1

    return applied

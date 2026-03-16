"""Graph exploration and evolution tracking endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(tags=["graph"])


@router.get("/graph/links")
def get_graph_links(
    top_n: int = Query(10),
    threshold: float = Query(0.75),
) -> list[dict[str, Any]]:
    """Discover potential links between knowledge entries."""
    from agents.link import find_links

    suggestions = find_links(top_n=top_n, threshold=threshold)
    # Ensure Path objects are serialized
    return [
        {
            "source_title": s.get("source_title", ""),
            "target_title": s.get("target_title", ""),
            "similarity": s.get("similarity", 0),
            "source": s.get("source", ""),
        }
        for s in suggestions
    ]


@router.get("/graph/history/{entry_id}")
def get_graph_history(entry_id: str) -> list[dict[str, Any]]:
    """Build the supersedes chain for an entry."""
    from agents.config import load_config
    from agents.history import build_supersedes_chain, find_related_evolution
    from agents.utils import load_entries

    config = load_config()
    entries = load_entries(config.vault_path)

    chain = build_supersedes_chain(entry_id, entries)
    if chain:
        return _serialize_chain(chain, entry_id)

    # Try related evolution
    target = None
    for e in entries:
        if str(e["metadata"].get("id", "")).lower() == entry_id.lower():
            target = e
            break

    if target is None:
        return []

    related = find_related_evolution(target, entries)
    return _serialize_chain([target] + related, entry_id) if related else []


def _serialize_chain(
    chain: list[dict[str, Any]], highlight_id: str
) -> list[dict[str, Any]]:
    """Convert entry chain to JSON-safe list."""
    result = []
    for entry in chain:
        meta = entry.get("metadata", {})
        result.append({
            "id": meta.get("id", ""),
            "title": meta.get("title", ""),
            "created": meta.get("created", ""),
            "confidence": meta.get("confidence"),
            "status": meta.get("status", ""),
            "is_current": meta.get("id", "").lower() == highlight_id.lower(),
        })
    return result


@router.get("/graph/cross-domain")
def get_cross_domain(
    min_similarity: float = Query(0.6),
    max_insights: int = Query(20),
    describe: bool = Query(True),
) -> list[dict[str, Any]]:
    """Discover cross-domain knowledge connections."""
    from agents.cross_domain import discover_cross_domain

    insights = discover_cross_domain(
        min_similarity=min_similarity,
        max_insights=max_insights,
        describe=describe,
    )
    return [
        {
            "domain_a": i.domain_a,
            "domain_b": i.domain_b,
            "entry_a_id": i.entry_a_id,
            "entry_a_title": i.entry_a_title,
            "entry_b_id": i.entry_b_id,
            "entry_b_title": i.entry_b_title,
            "similarity": i.similarity,
            "description": i.description,
        }
        for i in insights
    ]


@router.get("/graph/network")
def get_graph_network() -> dict[str, Any]:
    """Return full knowledge network: all nodes + edges for D3 visualization."""
    from agents.config import load_config
    from agents.graph_store import GraphStore, get_graph_store, _extract_rows, _extract_id_from_record

    config = load_config()
    gs = get_graph_store(config)
    gs.connect()
    try:
        # Nodes
        entries = gs.list_entries()
        nodes = []
        for e in entries:
            eid = _extract_id_from_record(e.get("id", ""))
            domain = e.get("domain", [])
            if isinstance(domain, str):
                domain = [domain]
            nodes.append({
                "id": eid,
                "title": e.get("title", ""),
                "domain": domain,
                "type": e.get("entry_type", ""),
                "depth": e.get("depth", ""),
                "status": e.get("status", ""),
                "confidence": e.get("confidence"),
                "tags": e.get("tags", []),
            })

        # Edges — query each relation type
        edges = []
        rel_types = ("references", "prerequisites", "supersedes")
        for rt in rel_types:
            result = gs._db.query(f"SELECT * FROM {rt};")
            rows = _extract_rows(result)
            for row in rows:
                src = _extract_id_from_record(row.get("in", ""))
                tgt = _extract_id_from_record(row.get("out", ""))
                if src and tgt:
                    edges.append({
                        "source": src,
                        "target": tgt,
                        "type": rt,
                    })

        # Collect unique domains
        domains: set[str] = set()
        for n in nodes:
            for d in n["domain"]:
                domains.add(d)

        return {
            "nodes": nodes,
            "edges": edges,
            "meta": {
                "domains": sorted(domains),
                "edge_types": list(rel_types),
                "node_count": len(nodes),
                "edge_count": len(edges),
            },
        }
    finally:
        gs.close()


@router.get("/graph/backlinks/{entry_id}")
def get_backlinks(entry_id: str) -> list[dict[str, str]]:
    """Get all entries that reference the given entry (backlinks)."""
    from agents.backlinks import find_backlinks

    backlinks = find_backlinks(entry_id)
    return [bl.to_dict() for bl in backlinks]


@router.get("/graph/diff/{entry_id}")
def get_graph_diff(
    entry_id: str,
    limit: int = Query(10),
) -> list[dict[str, Any]]:
    """Get content evolution diff records for an entry."""
    from agents.config import load_config
    from agents.diff_store import DiffStore
    from agents.graph_store import get_graph_store

    config = load_config()
    try:
        gs = get_graph_store(config)
        gs.connect()
        try:
            ds = DiffStore(gs)
            return ds.get_history(entry_id, limit=limit)
        finally:
            gs.close()
    except Exception:
        return []

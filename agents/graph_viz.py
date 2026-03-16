"""Graph visualization: Mermaid and Obsidian Canvas output.

Converts knowledge graph neighborhood data into visual formats:
- Mermaid graph definitions (renders inside Obsidian markdown)
- JSON Canvas v1.0 files (opens natively in Obsidian)
"""

from __future__ import annotations

import json
import math
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.graph_store import GraphStore

# ── Domain → Canvas color mapping ────────────────────────────────────
# Canvas preset colors: "1" red, "2" orange, "3" yellow,
#                       "4" green, "5" cyan, "6" purple

DOMAIN_COLORS: dict[str, str] = {
    "golang": "4",
    "cloud-native": "5",
    "distributed-systems": "6",
    "databases": "2",
    "networking": "1",
    "frontend": "3",
    "ai-agent": "6",
    "ai-infra": "5",
    "algorithm": "2",
}

_CENTER_COLOR = "1"  # red — highlight center node
_DEFAULT_COLOR = "0"  # no color preset

# ── Layout constants ─────────────────────────────────────────────────

_HOP1_RADIUS = 400
_HOP2_RADIUS = 800
_NODE_W = 250
_NODE_H = 100
_CENTER_W = 300
_CENTER_H = 120

# Mermaid node-id sanitizer: keep only alphanumerics and underscores
_MERMAID_ID_RE = re.compile(r"[^a-zA-Z0-9_]")


# ── Graph data builder ───────────────────────────────────────────────


def build_graph_data(gs: GraphStore, entry_id: str, depth: int = 2) -> dict:
    """Build directed graph data with full (from, to) edges.

    Returns::

        {
            "center_id": str,
            "nodes": {entry_id: {title, domain, entry_type, file_path, ...}},
            "edges": [{"from": str, "to": str, "rel_type": str}],
            "hop_map": {entry_id: 0|1|2},  # hop distance from center
        }
    """
    nodes: dict[str, dict[str, Any]] = {}
    edge_set: set[tuple[str, str, str]] = set()
    hop_map: dict[str, int] = {entry_id: 0}

    # Center node
    center = gs.get_entry(entry_id)
    if center:
        nodes[entry_id] = _node_info(center, entry_id)

    # Hop 1
    hop1_ids: set[str] = set()
    _collect_neighbors(gs, entry_id, nodes, edge_set, hop1_ids, hop_map, hop=1)

    # Hop 2
    if depth >= 2:
        hop1_snapshot = list(hop1_ids)
        for nid in hop1_snapshot:
            _collect_neighbors(
                gs, nid, nodes, edge_set, set(), hop_map, hop=2,
                exclude={entry_id},
            )

    edges = [{"from": f, "to": t, "rel_type": r} for f, t, r in edge_set]

    return {
        "center_id": entry_id,
        "nodes": nodes,
        "edges": edges,
        "hop_map": hop_map,
    }


def _collect_neighbors(
    gs: GraphStore,
    source_id: str,
    nodes: dict[str, dict],
    edge_set: set[tuple[str, str, str]],
    discovered: set[str],
    hop_map: dict[str, int],
    hop: int,
    exclude: set[str] | None = None,
) -> None:
    """Collect neighbors of *source_id* and record directed edges."""
    exclude = exclude or set()
    relations = gs.get_relations(source_id, direction="both")
    for rel in relations:
        tid = rel["target_id"]
        if tid in exclude:
            continue

        # Determine directed edge
        if rel["direction"] == "out":
            edge_key = (source_id, tid, rel["rel_type"])
        else:
            edge_key = (tid, source_id, rel["rel_type"])
        edge_set.add(edge_key)
        discovered.add(tid)

        if tid not in hop_map:
            hop_map[tid] = hop

        if tid not in nodes:
            entry = gs.get_entry(tid)
            if entry:
                nodes[tid] = _node_info(entry, tid)
            else:
                nodes[tid] = {"title": tid, "domain": [], "entry_type": "", "file_path": ""}


def _node_info(entry: dict, entry_id: str) -> dict:
    """Extract display-relevant fields from a graph entry."""
    domain = entry.get("domain", [])
    if isinstance(domain, str):
        domain = [domain]
    return {
        "title": entry.get("title", entry_id),
        "domain": domain,
        "entry_type": entry.get("entry_type", ""),
        "file_path": entry.get("file_path", ""),
    }


# ── Mermaid output ───────────────────────────────────────────────────


def to_mermaid(graph_data: dict) -> str:
    """Generate a Mermaid graph definition from graph data."""
    nodes = graph_data["nodes"]
    edges = graph_data["edges"]

    if not nodes:
        return "graph LR\n"

    lines = ["graph LR"]

    # Node declarations
    for nid, info in nodes.items():
        safe_id = _sanitize_mermaid_id(nid)
        title = info.get("title", nid).replace('"', "'")
        lines.append(f'    {safe_id}["{title}"]')

    # Edge declarations
    for edge in edges:
        src = _sanitize_mermaid_id(edge["from"])
        dst = _sanitize_mermaid_id(edge["to"])
        label = edge["rel_type"]
        lines.append(f"    {src} -->|{label}| {dst}")

    return "\n".join(lines) + "\n"


def _sanitize_mermaid_id(entry_id: str) -> str:
    """Make an entry ID safe for Mermaid node identifiers."""
    return _MERMAID_ID_RE.sub("_", entry_id)


# ── Canvas output ────────────────────────────────────────────────────


def to_canvas(graph_data: dict, vault_path: Path) -> dict:
    """Generate an Obsidian JSON Canvas v1.0 structure.

    Nodes with ``file_path`` become ``"type": "file"`` (clickable);
    others become ``"type": "text"``.
    """
    nodes_data = graph_data["nodes"]
    edges_data = graph_data["edges"]
    center_id = graph_data["center_id"]
    hop_map = graph_data.get("hop_map", {})

    # Classify hops
    hop1_ids = [nid for nid, h in hop_map.items() if h == 1]
    hop2_ids = [nid for nid, h in hop_map.items() if h >= 2]
    positions = _radial_layout(center_id, hop1_ids, hop2_ids)

    # Stable ID mapping for canvas node IDs
    id_map: dict[str, str] = {}
    canvas_nodes: list[dict] = []

    for nid, info in nodes_data.items():
        x, y = positions.get(nid, (0, 0))
        is_center = nid == center_id
        w = _CENTER_W if is_center else _NODE_W
        h = _CENTER_H if is_center else _NODE_H

        canvas_id = uuid.uuid5(uuid.NAMESPACE_DNS, nid).hex[:16]
        id_map[nid] = canvas_id

        node: dict[str, Any] = {
            "id": canvas_id,
            "x": int(x - w / 2),
            "y": int(y - h / 2),
            "width": w,
            "height": h,
        }

        # Color
        color = _CENTER_COLOR if is_center else _domain_color(info.get("domain", []))
        if color != _DEFAULT_COLOR:
            node["color"] = color

        # Type: file (clickable) or text
        fp = info.get("file_path", "")
        if fp:
            rel = _vault_relative(fp, vault_path)
            node["type"] = "file"
            node["file"] = rel
        else:
            node["type"] = "text"
            node["text"] = info.get("title", nid)

        canvas_nodes.append(node)

    # Edges
    canvas_edges: list[dict] = []
    for edge in edges_data:
        from_cid = id_map.get(edge["from"])
        to_cid = id_map.get(edge["to"])
        if not from_cid or not to_cid:
            continue
        edge_key = f"{edge['from']}-{edge['to']}-{edge['rel_type']}"
        canvas_edges.append({
            "id": uuid.uuid5(uuid.NAMESPACE_DNS, edge_key).hex[:16],
            "fromNode": from_cid,
            "toNode": to_cid,
            "label": edge["rel_type"],
            "toEnd": "arrow",
        })

    return {"nodes": canvas_nodes, "edges": canvas_edges}


def _domain_color(domains: list[str]) -> str:
    """Pick a Canvas color preset from the first matching domain."""
    for d in domains:
        if d in DOMAIN_COLORS:
            return DOMAIN_COLORS[d]
    return _DEFAULT_COLOR


def _vault_relative(file_path: str, vault_path: Path) -> str:
    """Convert an absolute path to vault-relative."""
    try:
        return str(Path(file_path).relative_to(vault_path))
    except ValueError:
        return file_path


# ── Layout ───────────────────────────────────────────────────────────


def _radial_layout(
    center_id: str,
    hop1_ids: list[str],
    hop2_ids: list[str],
) -> dict[str, tuple[float, float]]:
    """Concentric-circle layout. Returns ``{node_id: (x, y)}``."""
    positions: dict[str, tuple[float, float]] = {center_id: (0.0, 0.0)}

    for i, nid in enumerate(hop1_ids):
        angle = 2 * math.pi * i / max(len(hop1_ids), 1)
        positions[nid] = (
            _HOP1_RADIUS * math.cos(angle),
            _HOP1_RADIUS * math.sin(angle),
        )

    for i, nid in enumerate(hop2_ids):
        angle = 2 * math.pi * i / max(len(hop2_ids), 1)
        positions[nid] = (
            _HOP2_RADIUS * math.cos(angle),
            _HOP2_RADIUS * math.sin(angle),
        )

    return positions


# ── Convenience: write canvas file ───────────────────────────────────


def write_canvas(canvas_data: dict, out_path: Path) -> None:
    """Write canvas dict to a .canvas file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(canvas_data, ensure_ascii=False, indent=2))

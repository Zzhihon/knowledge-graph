"""SurrealDB graph store for knowledge entry relationships.

Provides graph-native storage and traversal for knowledge entries.
Entry metadata is stored as nodes; relationships (references,
prerequisites, supersedes) are stored as typed edges via RELATE.
Vectors are NOT stored here — Qdrant handles that.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agents.config import ProjectConfig, load_config

_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

_REL_TYPES = ("references", "prerequisites", "supersedes")


def _entry_rid(entry_id: str) -> str:
    """Convert an entry ID to a SurrealDB record identifier."""
    return f"entry:`{entry_id}`"


def _parse_relations(entry: dict) -> list[tuple[str, str, str, dict]]:
    """Extract relations from an entry's frontmatter and body.

    Returns list of (from_id, to_id, rel_type, meta) tuples.
    """
    meta = entry["metadata"]
    entry_id = meta.get("id", "")
    if not entry_id:
        return []

    relations: list[tuple[str, str, str, dict]] = []
    seen: set[tuple[str, str]] = set()

    # related field → references
    for link_raw in meta.get("related", []) or []:
        if not isinstance(link_raw, str):
            continue
        m = _WIKI_LINK_RE.search(link_raw)
        target = m.group(1) if m else link_raw.strip()
        if target and (target, "references") not in seen:
            relations.append((entry_id, target, "references", {"source": "frontmatter"}))
            seen.add((target, "references"))

    # prerequisites field → prerequisites
    for link_raw in meta.get("prerequisites", []) or []:
        if not isinstance(link_raw, str):
            continue
        m = _WIKI_LINK_RE.search(link_raw)
        target = m.group(1) if m else link_raw.strip()
        if target and (target, "prerequisites") not in seen:
            relations.append((entry_id, target, "prerequisites", {"source": "frontmatter"}))
            seen.add((target, "prerequisites"))

    # supersedes field → supersedes
    sup = meta.get("supersedes")
    if sup and isinstance(sup, str):
        m = _WIKI_LINK_RE.search(sup)
        target = m.group(1) if m else sup.strip()
        if target:
            relations.append((entry_id, target, "supersedes", {"source": "frontmatter"}))
            seen.add((target, "supersedes"))

    # Inline [[wiki links]] in body → references
    content = entry.get("content", "")
    for m in _WIKI_LINK_RE.finditer(content):
        target = m.group(1)
        if target and (target, "references") not in seen:
            relations.append((entry_id, target, "references", {"source": "body"}))
            seen.add((target, "references"))

    return relations


class GraphStore:
    """SurrealDB-backed graph store for knowledge relationships."""

    def __init__(
        self,
        db_path: str,
        namespace: str = "knowledge_graph",
        database: str = "main",
    ) -> None:
        self._db_path = db_path
        self._namespace = namespace
        self._database = database
        self._db: Any = None

    def connect(self) -> None:
        """Open connection and select namespace/database."""
        from surrealdb import Surreal

        db_dir = Path(self._db_path)
        db_dir.mkdir(parents=True, exist_ok=True)
        self._db = Surreal(f"surrealkv://{self._db_path}")
        self._db.__enter__()
        self._db.use(self._namespace, self._database)

    def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            try:
                self._db.__exit__(None, None, None)
            except Exception:
                pass
            self._db = None

    def __enter__(self) -> GraphStore:
        self.connect()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    # ── Schema ──────────────────────────────────────────────────────

    def init_schema(self) -> None:
        """Create tables and indexes."""
        self._db.query("""
            DEFINE TABLE entry SCHEMALESS;
            DEFINE INDEX entry_type ON entry FIELDS type;
        """)
        for rel in _REL_TYPES:
            self._db.query(f"DEFINE TABLE {rel} TYPE RELATION SCHEMALESS;")
        # Diff tracking table for knowledge evolution
        self._db.query("""
            DEFINE TABLE entry_diff SCHEMALESS;
            DEFINE INDEX idx_diff_entry ON entry_diff FIELDS entry_id;
        """)

    # ── Entry CRUD ──────────────────────────────────────────────────

    def upsert_entry(self, entry_id: str, metadata: dict) -> None:
        """Upsert an entry node with metadata (no embeddings)."""
        rid = _entry_rid(entry_id)
        fields = {
            "title": metadata.get("title", ""),
            "domain": metadata.get("domain", []),
            "tags": metadata.get("tags", []),
            "entry_type": metadata.get("type", ""),
            "depth": metadata.get("depth", ""),
            "status": metadata.get("status", ""),
            "confidence": float(metadata.get("confidence", 0.5)),
            "file_path": metadata.get("file_path", ""),
            "created": metadata.get("created", ""),
            "updated": metadata.get("updated", ""),
        }
        set_clauses = ", ".join(f"{k} = ${k}" for k in fields)
        self._db.query(f"UPSERT {rid} SET {set_clauses};", fields)

    def get_entry(self, entry_id: str) -> dict | None:
        """Get a single entry by ID."""
        rid = _entry_rid(entry_id)
        result = self._db.query(f"SELECT * FROM {rid};")
        rows = _extract_rows(result)
        return rows[0] if rows else None

    def list_entries(self, filters: dict | None = None) -> list[dict]:
        """List entries with optional filters."""
        where_parts: list[str] = []
        params: dict[str, Any] = {}
        if filters:
            for k, v in filters.items():
                param_name = f"f_{k}"
                if k == "domain":
                    where_parts.append(f"domain CONTAINS ${param_name}")
                else:
                    field = "entry_type" if k == "type" else k
                    where_parts.append(f"{field} = ${param_name}")
                params[param_name] = v
        where = " AND ".join(where_parts) if where_parts else "true"
        result = self._db.query(f"SELECT * FROM entry WHERE {where};", params)
        return _extract_rows(result)

    # ── Relationships ───────────────────────────────────────────────

    def add_relation(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        metadata: dict | None = None,
    ) -> None:
        """Create a typed edge between two entries."""
        if rel_type not in _REL_TYPES:
            raise ValueError(f"Unknown relation type: {rel_type}. Valid: {_REL_TYPES}")
        fr = _entry_rid(from_id)
        to = _entry_rid(to_id)
        meta = metadata or {}
        set_parts = ", ".join(f"{k} = ${k}" for k in meta) if meta else ""
        set_clause = f" SET {set_parts}" if set_parts else ""
        self._db.query(f"RELATE {fr}->{rel_type}->{to}{set_clause};", meta)

    def remove_relation(self, from_id: str, to_id: str, rel_type: str) -> None:
        """Delete a specific edge."""
        fr = _entry_rid(from_id)
        to = _entry_rid(to_id)
        self._db.query(
            f"DELETE {rel_type} WHERE in = {fr} AND out = {to};"
        )

    def get_relations(
        self,
        entry_id: str,
        rel_type: str | None = None,
        direction: str = "both",
    ) -> list[dict]:
        """Get relations for an entry.

        Args:
            entry_id: The entry ID.
            rel_type: Filter by relation type, or None for all.
            direction: 'out', 'in', or 'both'.

        Returns:
            List of dicts with target_id, rel_type, direction, and metadata.
        """
        rid = _entry_rid(entry_id)
        types = [rel_type] if rel_type else list(_REL_TYPES)
        relations: list[dict] = []

        for rt in types:
            if direction in ("out", "both"):
                result = self._db.query(
                    f"SELECT *, out as target FROM {rt} WHERE in = {rid};"
                )
                for row in _extract_rows(result):
                    target = _extract_id_from_record(row.get("target", row.get("out", "")))
                    relations.append({
                        "target_id": target,
                        "rel_type": rt,
                        "direction": "out",
                        "metadata": {k: v for k, v in row.items()
                                     if k not in ("id", "in", "out", "target")},
                    })

            if direction in ("in", "both"):
                result = self._db.query(
                    f"SELECT *, in as source FROM {rt} WHERE out = {rid};"
                )
                for row in _extract_rows(result):
                    source = _extract_id_from_record(row.get("source", row.get("in", "")))
                    relations.append({
                        "target_id": source,
                        "rel_type": rt,
                        "direction": "in",
                        "metadata": {k: v for k, v in row.items()
                                     if k not in ("id", "in", "out", "source")},
                    })

        return relations

    # ── Traversal ───────────────────────────────────────────────────

    def traverse(
        self,
        entry_id: str,
        rel_type: str | None = None,
        depth: int = 1,
        direction: str = "out",
    ) -> list[dict]:
        """Multi-hop graph traversal.

        Args:
            entry_id: Starting entry.
            rel_type: Relation type to follow, or None for all.
            depth: Number of hops.
            direction: 'out' or 'in'.

        Returns:
            List of entry dicts reachable within *depth* hops.
        """
        rid = _entry_rid(entry_id)
        arrow = "->" if direction == "out" else "<-"
        types = [rel_type] if rel_type else list(_REL_TYPES)
        seen: set[str] = {entry_id}
        all_results: list[dict] = []

        for rt in types:
            chain = f"{arrow}{rt}{arrow}entry" * depth
            q = f"SELECT {chain}.* AS nodes FROM {rid};"
            result = self._db.query(q)
            for row in _extract_rows(result):
                nodes = row.get("nodes", [])
                if isinstance(nodes, dict):
                    nodes = [nodes]
                if not isinstance(nodes, list):
                    continue
                # Flatten nested lists
                flat = _flatten(nodes)
                for node in flat:
                    if isinstance(node, dict):
                        nid = _extract_id_from_record(node.get("id", ""))
                        if nid and nid not in seen:
                            seen.add(nid)
                            node["_entry_id"] = nid
                            all_results.append(node)

        return all_results

    def neighborhood(self, entry_id: str, depth: int = 2) -> dict:
        """Get full neighborhood around an entry.

        Returns dict with 'center', 'nodes', and 'edges' keys.
        """
        center = self.get_entry(entry_id)
        relations = self.get_relations(entry_id, direction="both")

        neighbor_ids: set[str] = set()
        edges: list[dict] = []
        for rel in relations:
            neighbor_ids.add(rel["target_id"])
            edges.append(rel)

        # Depth 2: get relations of neighbors too
        if depth >= 2:
            for nid in list(neighbor_ids):
                sub_rels = self.get_relations(nid, direction="both")
                for sr in sub_rels:
                    tid = sr["target_id"]
                    if tid != entry_id:
                        neighbor_ids.add(tid)
                        edges.append(sr)

        nodes: list[dict] = []
        for nid in neighbor_ids:
            node = self.get_entry(nid)
            if node:
                nodes.append(node)

        return {"center": center, "nodes": nodes, "edges": edges}

    def find_path(self, from_id: str, to_id: str) -> list[str] | None:
        """Find a path between two entries via BFS over relations.

        Returns list of entry_ids forming the path, or None.
        """
        from collections import deque

        visited: set[str] = {from_id}
        queue: deque[list[str]] = deque([[from_id]])

        while queue:
            path = queue.popleft()
            current = path[-1]
            if current == to_id:
                return path
            if len(path) > 6:
                continue
            rels = self.get_relations(current, direction="both")
            for rel in rels:
                nid = rel["target_id"]
                if nid not in visited:
                    visited.add(nid)
                    queue.append(path + [nid])

        return None

    # ── Incremental helpers ────────────────────────────────────────

    def delete_entry(self, entry_id: str) -> None:
        """删除单个条目节点."""
        rid = _entry_rid(entry_id)
        self._db.query(f"DELETE {rid};")

    def delete_entry_edges(self, entry_id: str) -> None:
        """删除某条目的所有出入边."""
        rid = _entry_rid(entry_id)
        for rt in _REL_TYPES:
            self._db.query(f"DELETE {rt} WHERE in = {rid} OR out = {rid};")

    def sync_partial(
        self,
        changed_entries: list[dict],
        deleted_ids: list[str],
        all_known_ids: set[str],
    ) -> dict:
        """增量同步：只处理变更/删除的条目和边.

        Args:
            changed_entries: 新增或修改的条目列表.
            deleted_ids: 已从磁盘删除的条目 ID.
            all_known_ids: 当前磁盘上所有有效条目 ID 集合.

        Returns:
            摘要 dict: entries_upserted, entries_deleted, edges_created, edges_removed.
        """
        entries_upserted = 0
        entries_deleted = 0
        edges_created = 0
        edges_removed = 0

        # 删除已移除的条目及其边
        for eid in deleted_ids:
            self.delete_entry_edges(eid)
            self.delete_entry(eid)
            entries_deleted += 1

        # Upsert 变更条目
        for entry in changed_entries:
            eid = entry["metadata"].get("id", "")
            if not eid:
                continue
            fpath = str(entry.get("path", ""))
            meta = dict(entry["metadata"])
            meta["file_path"] = fpath
            self.upsert_entry(eid, meta)
            entries_upserted += 1

        # 重建变更条目的边
        changed_ids = {e["metadata"]["id"] for e in changed_entries if e["metadata"].get("id")}
        for eid in changed_ids:
            # 先删旧边
            self.delete_entry_edges(eid)

        desired_edges: set[tuple[str, str, str]] = set()
        for entry in changed_entries:
            rels = _parse_relations(entry)
            for from_id, to_id, rel_type, meta in rels:
                if to_id not in all_known_ids:
                    continue
                desired_edges.add((from_id, to_id, rel_type))
                self.add_relation(from_id, to_id, rel_type, meta)
                edges_created += 1

        return {
            "entries_upserted": entries_upserted,
            "entries_deleted": entries_deleted,
            "edges_created": edges_created,
            "edges_removed": edges_removed,
        }

    # ── Sync ────────────────────────────────────────────────────────

    def sync_entries_and_relations(self, entries: list[dict]) -> dict:
        """Full sync from markdown entries.

        Upserts all entry nodes, parses relationships from frontmatter
        and body, and creates edges. Removes stale edges.

        Returns summary dict.
        """
        entries_synced = 0
        edges_created = 0
        edges_removed = 0

        # Build ID set for validation
        known_ids: set[str] = set()
        for entry in entries:
            eid = entry["metadata"].get("id", "")
            if eid:
                known_ids.add(eid)

        # Upsert entries
        for entry in entries:
            eid = entry["metadata"].get("id", "")
            if not eid:
                continue
            fpath = str(entry.get("path", ""))
            meta = dict(entry["metadata"])
            meta["file_path"] = fpath
            self.upsert_entry(eid, meta)
            entries_synced += 1

        # Parse and create relations
        desired_edges: set[tuple[str, str, str]] = set()
        for entry in entries:
            rels = _parse_relations(entry)
            for from_id, to_id, rel_type, meta in rels:
                if to_id not in known_ids:
                    continue
                desired_edges.add((from_id, to_id, rel_type))
                self.add_relation(from_id, to_id, rel_type, meta)
                edges_created += 1

        # Remove stale edges
        for eid in known_ids:
            existing = self.get_relations(eid, direction="out")
            for rel in existing:
                key = (eid, rel["target_id"], rel["rel_type"])
                if key not in desired_edges:
                    self.remove_relation(eid, rel["target_id"], rel["rel_type"])
                    edges_removed += 1

        return {
            "entries_synced": entries_synced,
            "edges_created": edges_created,
            "edges_removed": edges_removed,
        }

    def get_stats(self) -> dict:
        """Return counts of entries and edges by type."""
        result = self._db.query("SELECT count() as c FROM entry GROUP ALL;")
        rows = _extract_rows(result)
        entry_count = rows[0].get("c", 0) if rows else 0

        edge_counts: dict[str, int] = {}
        for rt in _REL_TYPES:
            r = self._db.query(f"SELECT count() as c FROM {rt} GROUP ALL;")
            rr = _extract_rows(r)
            edge_counts[rt] = rr[0].get("c", 0) if rr else 0

        return {"entries": entry_count, "edges": edge_counts}


# ── Helpers ─────────────────────────────────────────────────────────


def _extract_rows(result: Any) -> list[dict]:
    """Extract row dicts from SurrealDB query result.

    SurrealDB returns various formats depending on the query. This
    normalizes them into a flat list of dicts.
    """
    if result is None:
        return []
    if isinstance(result, list):
        rows: list[dict] = []
        for item in result:
            if isinstance(item, dict):
                # Could be a result wrapper {"result": [...], "status": "OK"}
                inner = item.get("result", item)
                if isinstance(inner, list):
                    rows.extend(r for r in inner if isinstance(r, dict))
                elif isinstance(inner, dict):
                    rows.append(inner)
            elif isinstance(item, list):
                rows.extend(r for r in item if isinstance(r, dict))
        return rows
    if isinstance(result, dict):
        inner = result.get("result", result)
        if isinstance(inner, list):
            return [r for r in inner if isinstance(r, dict)]
        return [inner] if isinstance(inner, dict) else []
    return []


def _extract_id_from_record(value: Any) -> str:
    """Extract a clean entry_id from a SurrealDB record reference.

    Input may be: 'entry:ke-20260226-xxx', 'entry:`ke-20260226-xxx`',
    {'tb': 'entry', 'id': {'String': 'ke-20260226-xxx'}}, or a plain string.
    """
    if isinstance(value, dict):
        # Record ID object
        rid = value.get("id", value)
        if isinstance(rid, dict):
            return rid.get("String", str(rid))
        return str(rid)
    s = str(value)
    if s.startswith("entry:"):
        s = s[len("entry:"):]
    return s.strip("`").strip("⟨").strip("⟩")


def _flatten(lst: list) -> list:
    """Flatten nested lists."""
    flat: list = []
    for item in lst:
        if isinstance(item, list):
            flat.extend(_flatten(item))
        else:
            flat.append(item)
    return flat


def get_graph_store(config: ProjectConfig | None = None) -> GraphStore:
    """Factory: create a GraphStore from project config."""
    if config is None:
        config = load_config()

    db_path = config.agent.vector_db_path.replace("chroma", "surrealdb")
    if hasattr(config.agent, "graph_db_path"):
        db_path = config.agent.graph_db_path  # type: ignore[attr-defined]

    p = Path(db_path)
    if not p.is_absolute():
        p = config.vault_path / p
    p.mkdir(parents=True, exist_ok=True)

    ns = "knowledge_graph"
    database = "main"

    return GraphStore(str(p), namespace=ns, database=database)

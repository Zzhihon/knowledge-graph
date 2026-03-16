"""Sync engine: reusable sync logic shared by CLI and API.

Extracted from cli.py so both `kg sync` and `POST /api/sync`
can call the same functions and get structured results.
"""

from __future__ import annotations

from typing import Any

from agents.config import ProjectConfig, load_config
from agents.utils import compute_content_hash, load_entries


def prepare_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter valid entries and inject content_hash + file_path."""
    valid: list[dict[str, Any]] = []
    for entry in entries:
        meta = entry["metadata"]
        entry_id = meta.get("id", "")
        if not entry_id:
            continue
        entry_copy = dict(entry)
        entry_copy["metadata"] = dict(meta)
        entry_copy["metadata"]["file_path"] = str(entry["path"])
        entry_copy["metadata"]["content_hash"] = compute_content_hash(entry["path"])
        valid.append(entry_copy)
    return valid


def _entry_to_text(entry: dict[str, Any]) -> str:
    """Convert entry to text for embedding."""
    meta = entry["metadata"]
    title = meta.get("title", "")
    tags = meta.get("tags", [])
    if isinstance(tags, list):
        tags_str = ", ".join(str(t) for t in tags)
    else:
        tags_str = str(tags)
    return f"{title}\n{tags_str}\n\n{entry['content']}"


def full_sync(
    config: ProjectConfig | None = None,
) -> dict[str, Any]:
    """Full rebuild: drop + recreate all indexes. Returns structured result."""
    from agents.embeddings import embed_texts
    from agents.graph_store import get_graph_store
    from agents.vector_store import get_vector_store

    if config is None:
        config = load_config()
    entries = load_entries(config.vault_path)
    valid_entries = prepare_entries(entries)

    if not valid_entries:
        return {"error": "No valid entries found"}

    texts = [_entry_to_text(e) for e in valid_entries]

    # Qdrant
    embeddings = embed_texts(texts)
    with get_vector_store(config) as store:
        store.init_collection()
        vec_count = store.upsert_entries(valid_entries, embeddings)

    # SurrealDB
    with get_graph_store(config) as gs:
        gs.init_schema()
        graph_result = gs.sync_entries_and_relations(valid_entries)

    return {
        "new": len(valid_entries),
        "changed": 0,
        "deleted": 0,
        "unchanged": 0,
        "qdrant_upserted": vec_count,
        "graph_upserted": graph_result["entries_synced"],
        "edges_created": graph_result["edges_created"],
    }


def incremental_sync(
    config: ProjectConfig | None = None,
) -> dict[str, Any]:
    """Incremental sync: only process new/changed/deleted entries."""
    from agents.embeddings import embed_texts
    from agents.graph_store import get_graph_store
    from agents.vector_store import get_vector_store

    if config is None:
        config = load_config()
    entries = load_entries(config.vault_path)
    valid_entries = prepare_entries(entries)

    if not valid_entries:
        return {"error": "No valid entries found"}

    # Build disk state
    disk_map: dict[str, dict[str, Any]] = {}
    for entry in valid_entries:
        eid = entry["metadata"]["id"]
        disk_map[eid] = entry

    with get_vector_store(config) as store:
        existed = store.ensure_collection()

        if not existed:
            store.close()
            return full_sync(config)

        stored_payloads = store.get_all_payloads()

        # Detect old format
        if stored_payloads:
            sample = next(iter(stored_payloads.values()))
            if not sample.get("content_hash"):
                store.close()
                return full_sync(config)

        # Diff
        stored_ids = set(stored_payloads.keys())
        disk_ids = set(disk_map.keys())
        new_ids = disk_ids - stored_ids
        deleted_ids = stored_ids - disk_ids
        common_ids = disk_ids & stored_ids

        changed_ids: set[str] = set()
        unchanged_ids: set[str] = set()
        for eid in common_ids:
            disk_hash = disk_map[eid]["metadata"]["content_hash"]
            stored_hash = stored_payloads[eid].get("content_hash", "")
            if disk_hash != stored_hash:
                changed_ids.add(eid)
            else:
                unchanged_ids.add(eid)

        if not new_ids and not changed_ids and not deleted_ids:
            return {
                "new": 0,
                "changed": 0,
                "deleted": 0,
                "unchanged": len(unchanged_ids),
                "qdrant_upserted": 0,
                "graph_upserted": 0,
                "edges_created": 0,
            }

        # Qdrant upsert
        to_process_ids = new_ids | changed_ids
        to_process = [disk_map[eid] for eid in to_process_ids]
        vec_upserted = 0

        if to_process:
            texts = [_entry_to_text(e) for e in to_process]
            embeddings = embed_texts(texts)
            vec_upserted = store.upsert_entries(to_process, embeddings)

        if deleted_ids:
            store.delete_points(list(deleted_ids))

    # SurrealDB
    changed_entries = [disk_map[eid] for eid in (new_ids | changed_ids)]
    with get_graph_store(config) as gs:
        gs.init_schema()
        graph_result = gs.sync_partial(
            changed_entries=changed_entries,
            deleted_ids=list(deleted_ids),
            all_known_ids=disk_ids,
        )

        # Record diffs
        from agents.diff_store import DiffStore

        ds = DiffStore(gs)
        ds.init_schema()

        for eid in new_ids:
            entry = disk_map[eid]
            ds.record_change(
                eid, "created", "", entry["content"],
                "", entry["metadata"]["content_hash"],
            )

        for eid in changed_ids:
            entry = disk_map[eid]
            old_content = ds.get_latest_content(eid) or ""
            ds.record_change(
                eid, "modified", old_content, entry["content"],
                stored_payloads[eid].get("content_hash", ""),
                entry["metadata"]["content_hash"],
            )

        for eid in deleted_ids:
            old_content = ds.get_latest_content(eid) or ""
            ds.record_change(
                eid, "deleted", old_content, "",
                stored_payloads[eid].get("content_hash", ""),
                "",
            )

    return {
        "new": len(new_ids),
        "changed": len(changed_ids),
        "deleted": len(deleted_ids),
        "unchanged": len(unchanged_ids),
        "qdrant_upserted": vec_upserted,
        "graph_upserted": graph_result["entries_upserted"],
        "edges_created": graph_result["edges_created"],
    }

"""`kg sync` command handler extracted from the legacy CLI module."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

from rich.panel import Panel
from rich.table import Table

from kg.infrastructure.config.loader import load_config

if TYPE_CHECKING:
    from rich.console import Console


def run_sync_command(*, full: bool, console: Console) -> None:
    """Execute the `kg sync` CLI behavior without Click wiring."""
    from agents.utils import load_entries

    try:
        config = load_config()
    except FileNotFoundError as exc:
        console.print(f"[red]配置错误: {exc}[/]")
        sys.exit(1)

    entries = load_entries(config.vault_path)
    if not entries:
        console.print("[yellow]知识库为空，无需同步。[/]")
        return

    valid_entries = _prepare_entries(entries)
    if not valid_entries:
        console.print("[yellow]未找到有效的知识条目。[/]")
        return

    if full:
        _full_sync(config, valid_entries, console)
    else:
        _incremental_sync(config, valid_entries, console)


def _prepare_entries(entries: list[Any]) -> list[dict[str, Any]]:
    """Filter valid entries and inject content_hash + file_path."""
    from agents.utils import compute_content_hash

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
    """Convert entry to embedding text."""
    meta = entry["metadata"]
    title = meta.get("title", "")
    tags = meta.get("tags", [])
    tags_str = ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags)
    return f"{title}\n{tags_str}\n\n{entry['content']}"


def _full_sync(config: Any, valid_entries: list[dict[str, Any]], console: Console) -> None:
    """Full rebuild: drop + recreate all indexes."""
    from agents.embeddings import embed_texts
    from agents.graph_store import get_graph_store
    from agents.vector_store import get_vector_store

    console.print(f"[bold blue]全量同步 {len(valid_entries)} 个条目...[/]\n")

    texts = [_entry_to_text(entry) for entry in valid_entries]

    console.print("[bold]1/2 Weaviate 向量索引[/]")
    console.print("[dim]  正在生成嵌入向量...[/]")
    embeddings = embed_texts(texts)

    with get_vector_store(config) as store:
        store.init_collection()
        vec_count = store.upsert_entries(valid_entries, embeddings)
    console.print(f"[green]  {vec_count} 个条目已写入 Weaviate[/]\n")

    console.print("[bold]2/2 SurrealDB 图数据库[/]")
    with get_graph_store(config) as gs:
        gs.init_schema()
        result = gs.sync_entries_and_relations(valid_entries)
    console.print(
        f"[green]  {result['entries_synced']} 个节点, "
        f"{result['edges_created']} 条边已创建, "
        f"{result['edges_removed']} 条过期边已移除[/]\n",
    )

    summary_table = Table(title="全量同步摘要", show_lines=True)
    summary_table.add_column("引擎", style="bold cyan")
    summary_table.add_column("操作", style="green")
    summary_table.add_column("数量", justify="right")
    summary_table.add_row("Weaviate", "向量入库", str(vec_count))
    summary_table.add_row("SurrealDB", "节点同步", str(result["entries_synced"]))
    summary_table.add_row("SurrealDB", "边创建", str(result["edges_created"]))
    summary_table.add_row("SurrealDB", "过期边移除", str(result["edges_removed"]))
    console.print(summary_table)


def _incremental_sync(
    config: Any,
    valid_entries: list[dict[str, Any]],
    console: Console,
) -> None:
    """Incrementally sync new, changed, and deleted entries."""
    from agents.embeddings import embed_texts
    from agents.graph_store import get_graph_store
    from agents.vector_store import get_vector_store

    disk_map: dict[str, dict[str, Any]] = {}
    for entry in valid_entries:
        eid = entry["metadata"]["id"]
        disk_map[eid] = entry

    with get_vector_store(config) as store:
        existed = store.ensure_collection()

        if not existed:
            console.print("[yellow]首次同步，执行全量构建...[/]\n")
            store.close()
            _full_sync(config, valid_entries, console)
            return

        stored_payloads = store.get_all_payloads()

        if stored_payloads:
            sample = next(iter(stored_payloads.values()))
            if not sample.get("content_hash"):
                console.print(
                    "[yellow]检测到旧格式索引（缺少 content_hash），"
                    "自动执行全量重建...[/]\n",
                )
                store.close()
                _full_sync(config, valid_entries, console)
                return

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
            console.print(
                Panel(
                    f"[green]所有 {len(unchanged_ids)} 个条目均为最新，无需操作。[/]",
                    title="增量同步",
                    border_style="green",
                ),
            )
            return

        console.print("[bold blue]增量同步检测结果:[/]")
        console.print(f"  新增: [green]{len(new_ids)}[/]")
        console.print(f"  修改: [yellow]{len(changed_ids)}[/]")
        console.print(f"  删除: [red]{len(deleted_ids)}[/]")
        console.print(f"  未变: [dim]{len(unchanged_ids)}[/]\n")

        to_process_ids = new_ids | changed_ids
        to_process = [disk_map[eid] for eid in to_process_ids]
        vec_upserted = 0
        vec_deleted = 0

        if to_process:
            console.print("[bold]1/2 Weaviate 向量索引[/]")
            texts = [_entry_to_text(entry) for entry in to_process]
            console.print(f"[dim]  正在生成 {len(texts)} 条嵌入向量...[/]")
            embeddings = embed_texts(texts)
            vec_upserted = store.upsert_entries(to_process, embeddings)
            console.print(f"[green]  {vec_upserted} 个条目已更新[/]")

        if deleted_ids:
            vec_deleted = store.delete_points(list(deleted_ids))
            console.print(f"[green]  {vec_deleted} 个条目已删除[/]")

        if not to_process and not deleted_ids:
            console.print("[bold]1/2 Weaviate 向量索引[/]")
            console.print("[dim]  无需更新[/]")

        console.print()

    console.print("[bold]2/2 SurrealDB 图数据库[/]")
    changed_entries = [disk_map[eid] for eid in (new_ids | changed_ids)]

    diff_count = 0
    with get_graph_store(config) as gs:
        gs.init_schema()
        graph_result = gs.sync_partial(
            changed_entries=changed_entries,
            deleted_ids=list(deleted_ids),
            all_known_ids=disk_ids,
        )

        from agents.diff_store import DiffStore

        ds = DiffStore(gs)
        ds.init_schema()

        for eid in new_ids:
            entry = disk_map[eid]
            ds.record_change(
                eid, "created", "", entry["content"],
                "", entry["metadata"]["content_hash"],
            )
            diff_count += 1

        for eid in changed_ids:
            entry = disk_map[eid]
            old_content = ds.get_latest_content(eid) or ""
            ds.record_change(
                eid, "modified", old_content, entry["content"],
                stored_payloads[eid].get("content_hash", ""),
                entry["metadata"]["content_hash"],
            )
            diff_count += 1

        for eid in deleted_ids:
            old_content = ds.get_latest_content(eid) or ""
            ds.record_change(
                eid, "deleted", old_content, "",
                stored_payloads[eid].get("content_hash", ""),
                "",
            )
            diff_count += 1

    console.print(
        f"[green]  {graph_result['entries_upserted']} 个节点更新, "
        f"{graph_result['entries_deleted']} 个节点删除, "
        f"{graph_result['edges_created']} 条边创建[/]",
    )
    if diff_count:
        console.print(f"[green]  {diff_count} 条变更记录已保存[/]")
    console.print()

    summary_table = Table(title="增量同步摘要", show_lines=True)
    summary_table.add_column("类别", style="bold cyan")
    summary_table.add_column("操作", style="green")
    summary_table.add_column("数量", justify="right")
    summary_table.add_row("变更检测", "新增", str(len(new_ids)))
    summary_table.add_row("变更检测", "修改", str(len(changed_ids)))
    summary_table.add_row("变更检测", "删除", str(len(deleted_ids)))
    summary_table.add_row("变更检测", "未变", str(len(unchanged_ids)))
    summary_table.add_row("Weaviate", "向量更新", str(vec_upserted))
    summary_table.add_row("Weaviate", "向量删除", str(vec_deleted))
    summary_table.add_row("SurrealDB", "节点更新", str(graph_result["entries_upserted"]))
    summary_table.add_row("SurrealDB", "节点删除", str(graph_result["entries_deleted"]))
    summary_table.add_row("SurrealDB", "边创建", str(graph_result["edges_created"]))
    summary_table.add_row("演化追踪", "变更记录", str(diff_count))
    console.print(summary_table)

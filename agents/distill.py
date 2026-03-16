"""Knowledge distillation — merge similar entries into authoritative ones.

Discovers candidate groups via vector similarity, synthesises them
through Claude into a single canonical entry, and cleans up the old
entries from disk, Qdrant, and SurrealDB.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
import frontmatter
import httpx
from rich.console import Console
from rich.table import Table

from agents.config import ProjectConfig, load_config
from agents.ingest import _build_entry_markdown
from agents.json_utils import parse_json_robust, strip_code_fence
from agents.utils import generate_id, get_entry_dir, load_entries

logger = logging.getLogger(__name__)
console = Console()


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DistillGroup:
    group_id: int
    entry_ids: list[str]
    titles: list[str]
    domains: list[str]
    avg_similarity: float


@dataclass
class DistillResult:
    new_entry_id: str
    new_entry_title: str
    new_entry_path: str
    superseded_ids: list[str]
    deleted_count: int


# ---------------------------------------------------------------------------
# Union-Find for clustering pairs into groups
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self._parent.setdefault(x, x)
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def groups(self) -> dict[str, list[str]]:
        clusters: dict[str, list[str]] = {}
        for item in self._parent:
            root = self.find(item)
            clusters.setdefault(root, []).append(item)
        return clusters


# ---------------------------------------------------------------------------
# Distillation prompt
# ---------------------------------------------------------------------------

_DISTILL_PROMPT = """\
你是一个知识蒸馏助手。以下是 {n} 个高度相似的知识条目，请将它们合并为一个权威的规范条目。

要求：
1. 保留所有独特的洞察和信息，去除重复内容
2. 选择最准确、最全面的表述
3. 合并 tags、related_topics（去重）
4. title 应概括所有条目的核心主题
5. analysis 应整合所有条目的分析内容，保留代码示例
6. key_insights 应合并去重，保留最有价值的 3-7 条

返回单个 JSON 对象（与知识条目格式一致）：
{{
  "title": "合并后的标题",
  "question": "这个知识点回答了什么问题？",
  "domain": "知识域",
  "sub_domain": "子域",
  "entry_type": "条目类型",
  "depth": "深度层级",
  "tags": ["标签列表"],
  "analysis": "整合后的分析内容（markdown）",
  "key_insights": ["关键洞察列表"],
  "related_topics": ["相关主题列表"]
}}

只返回 JSON，不要有其他文字。

---
待合并的条目：

{entries_text}
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover_candidates(
    threshold: float = 0.80,
    min_group: int = 2,
    max_group: int = 5,
    config: ProjectConfig | None = None,
) -> list[DistillGroup]:
    """Discover groups of similar entries that are candidates for distillation.

    Iterates all entries, queries vector similarity, and clusters pairs
    above *threshold* using Union-Find.

    Returns groups sorted by avg_similarity descending.
    """
    from agents.vector_store import get_vector_store

    if config is None:
        config = load_config()

    entries = load_entries(config.vault_path)
    id_to_meta: dict[str, dict[str, Any]] = {}
    for e in entries:
        eid = e["metadata"].get("id", "")
        if eid:
            id_to_meta[eid] = e["metadata"]

    uf = _UnionFind()
    pair_scores: dict[tuple[str, str], float] = {}

    with get_vector_store(config) as store:
        store.ensure_collection()
        for eid in id_to_meta:
            similar = store.search_similar_to(eid, top_k=10)
            for hit in similar:
                hid = hit["entry_id"]
                score = hit["score"]
                if score < threshold or hid not in id_to_meta:
                    continue
                pair = tuple(sorted([eid, hid]))
                if pair not in pair_scores:
                    pair_scores[pair] = score  # type: ignore[index]
                uf.union(eid, hid)

    # Build groups
    raw_groups = uf.groups()
    results: list[DistillGroup] = []
    gid = 0
    for members in raw_groups.values():
        if not (min_group <= len(members) <= max_group):
            continue
        # Compute average similarity across pairs in this group
        scores: list[float] = []
        for a, b in pair_scores:
            if a in members and b in members:
                scores.append(pair_scores[(a, b)])
        avg_sim = sum(scores) / len(scores) if scores else 0.0

        titles = [id_to_meta[m].get("title", m) for m in members]
        domains = list({str(id_to_meta[m].get("domain", "")) for m in members})
        results.append(DistillGroup(
            group_id=gid,
            entry_ids=members,
            titles=titles,
            domains=domains,
            avg_similarity=round(avg_sim, 4),
        ))
        gid += 1

    results.sort(key=lambda g: g.avg_similarity, reverse=True)
    return results


def execute_distill(
    entry_ids: list[str],
    config: ProjectConfig | None = None,
    dry_run: bool = False,
) -> DistillResult:
    """Distill multiple entries into a single canonical entry.

    Steps: load entries → Claude synthesis → write new file →
    delete old files + Qdrant points + SurrealDB nodes/edges →
    upsert new entry into Qdrant + SurrealDB.
    """
    from agents.embeddings import embed_texts
    from agents.graph_store import get_graph_store
    from agents.vector_store import get_vector_store

    if config is None:
        config = load_config()

    # Load the source entries from disk
    all_entries = load_entries(config.vault_path)
    id_to_entry: dict[str, dict[str, Any]] = {}
    for e in all_entries:
        eid = e["metadata"].get("id", "")
        if eid:
            id_to_entry[eid] = e

    source_entries: list[dict[str, Any]] = []
    for eid in entry_ids:
        if eid not in id_to_entry:
            raise ValueError(f"条目不存在: {eid}")
        source_entries.append(id_to_entry[eid])

    # Call Claude to synthesise
    distilled = _call_claude_distill(source_entries, config)

    new_title = distilled.get("title", "Distilled Entry")
    new_id = generate_id(new_title)
    entry_type = distilled.get("entry_type", "research")

    try:
        target_dir_name = get_entry_dir(entry_type)
    except ValueError:
        target_dir_name = "05-Research"

    target_dir = config.vault_path / target_dir_name
    target_file = target_dir / f"{new_id}.md"

    # Add supersedes to frontmatter
    distilled["_supersedes"] = entry_ids

    markdown = _build_distill_markdown(distilled, new_id, entry_ids)

    if dry_run:
        console.print(f"[yellow]预览模式 — 将创建:[/] {target_file}")
        console.print(f"[yellow]将删除 {len(entry_ids)} 个旧条目[/]")
        return DistillResult(
            new_entry_id=new_id,
            new_entry_title=new_title,
            new_entry_path=str(target_file),
            superseded_ids=entry_ids,
            deleted_count=0,
        )

    # --- Crash-safe: write new FIRST, then delete old ---
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file.write_text(markdown, encoding="utf-8")
    console.print(f"[green]已创建蒸馏条目:[/] {target_file}")

    # Delete old files from disk
    deleted = 0
    for eid in entry_ids:
        entry = id_to_entry[eid]
        old_path = Path(entry["path"])
        if old_path.is_file():
            old_path.unlink()
            deleted += 1
            console.print(f"  [red]已删除:[/] {old_path}")

    # Update Qdrant: delete old, insert new
    with get_vector_store(config) as store:
        store.ensure_collection()
        store.delete_points(entry_ids)
        # Embed and upsert the new entry
        embed_text = f"{new_title} {distilled.get('question', '')} {' '.join(distilled.get('key_insights', []))}"
        vectors = embed_texts([embed_text])
        new_meta = {
            "id": new_id,
            "title": new_title,
            "domain": distilled.get("domain", ""),
            "tags": distilled.get("tags", []),
            "type": entry_type,
            "depth": distilled.get("depth", "intermediate"),
            "status": "active",
            "confidence": 0.8,
            "file_path": str(target_file),
        }
        store.upsert_entries(
            [{"metadata": new_meta, "content": markdown}],
            vectors,
        )

    # Update SurrealDB: delete old nodes/edges, insert new
    with get_graph_store(config) as gs:
        for eid in entry_ids:
            gs.delete_entry_edges(eid)
            gs.delete_entry(eid)
        gs.upsert_entry(new_id, {
            "title": new_title,
            "domain": distilled.get("domain", ""),
            "tags": distilled.get("tags", []),
            "type": entry_type,
            "depth": distilled.get("depth", "intermediate"),
            "status": "active",
            "confidence": 0.8,
            "file_path": str(target_file),
            "created": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        # Add supersedes edges
        for eid in entry_ids:
            gs.add_relation(new_id, eid, "supersedes", {"source": "distill"})

    return DistillResult(
        new_entry_id=new_id,
        new_entry_title=new_title,
        new_entry_path=str(target_file),
        superseded_ids=entry_ids,
        deleted_count=deleted,
    )


def _call_claude_distill(
    entries: list[dict[str, Any]],
    config: ProjectConfig,
) -> dict[str, Any]:
    """Call Claude to synthesise multiple entries into one."""
    client = anthropic.Anthropic(
        http_client=httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)),
    )

    # Build entries text for the prompt
    parts: list[str] = []
    for i, e in enumerate(entries, 1):
        meta = e["metadata"]
        insights = meta.get("key_insights", []) or []
        if isinstance(insights, str):
            insights = [insights]
        parts.append(
            f"--- 条目 {i}: {meta.get('title', '?')} ---\n"
            f"domain: {meta.get('domain', '')}\n"
            f"type: {meta.get('type', '')}\n"
            f"tags: {', '.join(str(t) for t in (meta.get('tags') or []))}\n"
            f"key_insights:\n" + "\n".join(f"  - {ins}" for ins in insights) + "\n"
            f"content:\n{e.get('content', '')}"
        )
    entries_text = "\n\n".join(parts)

    prompt = _DISTILL_PROMPT.format(n=len(entries), entries_text=entries_text)

    try:
        message = client.messages.create(
            model=config.agent.model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        raise RuntimeError(f"Claude API 调用失败: {exc}") from exc

    response_text = ""
    for block in message.content:
        if block.type == "text":
            response_text += block.text

    response_text = strip_code_fence(response_text)
    result = parse_json_robust(response_text)
    if isinstance(result, list):
        result = result[0]
    return result


def _build_distill_markdown(
    entry: dict[str, Any],
    entry_id: str,
    superseded_ids: list[str],
) -> str:
    """Build markdown with supersedes frontmatter field."""
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    metadata: dict[str, Any] = {
        "id": entry_id,
        "title": entry.get("title", "Untitled"),
        "domain": entry.get("domain", ""),
        "sub_domain": entry.get("sub_domain", ""),
        "type": entry.get("entry_type", "research"),
        "depth": entry.get("depth", "intermediate"),
        "confidence": 0.8,
        "status": "active",
        "tags": entry.get("tags", []),
        "created": now,
        "updated": now,
        "related_topics": entry.get("related_topics", []),
        "supersedes": superseded_ids,
    }

    body_parts: list[str] = []

    question = entry.get("question", "")
    if question:
        body_parts.append(f"## 问题\n\n{question}\n")

    analysis = entry.get("analysis", "")
    if analysis:
        body_parts.append(f"## 分析\n\n{analysis}\n")

    insights = entry.get("key_insights", [])
    if insights:
        items = "\n".join(f"- {insight}" for insight in insights)
        body_parts.append(f"## 关键洞察\n\n{items}\n")

    related = entry.get("related_topics", [])
    if related:
        links = "\n".join(f"- [[{topic}]]" for topic in related)
        body_parts.append(f"## 相关主题\n\n{links}\n")

    body = "\n".join(body_parts)
    post = frontmatter.Post(body, **metadata)
    return frontmatter.dumps(post)


def print_candidates(groups: list[DistillGroup]) -> None:
    """Print candidate groups as a Rich table."""
    if not groups:
        console.print("[yellow]未发现可合并的候选组。请尝试降低 --threshold 阈值。[/]")
        return

    table = Table(title=f"知识蒸馏候选组 (共 {len(groups)} 组)", show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("条目数", width=6)
    table.add_column("平均相似度", width=10)
    table.add_column("域", width=16)
    table.add_column("条目标题")
    table.add_column("条目 ID", style="dim")

    for g in groups:
        titles_str = "\n".join(g.titles)
        ids_str = "\n".join(g.entry_ids)
        domains_str = ", ".join(g.domains)
        table.add_row(
            str(g.group_id),
            str(len(g.entry_ids)),
            f"{g.avg_similarity:.3f}",
            domains_str,
            titles_str,
            ids_str,
        )

    console.print(table)

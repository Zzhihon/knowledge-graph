"""Query agent for semantic search across the knowledge base.

Builds and queries a Qdrant vector index from vault entries,
supporting filtered search by domain, type, depth, and status.
Includes query expansion for vague queries and domain-aware boosting.
"""

from __future__ import annotations

import logging
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agents.config import ProjectConfig, load_config
from agents.utils import load_entries

console = Console()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 方案 B: Domain keyword mapping — zero-cost domain detection
# ---------------------------------------------------------------------------
_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "golang": ["go", "golang", "goroutine", "channel", "gmp", "gc垃圾回收", "pprof"],
    "cloud-native": ["k8s", "kubernetes", "docker", "cilium", "ebpf", "helm", "istio", "容器"],
    "distributed-systems": ["分布式", "raft", "paxos", "rpc", "grpc", "一致性", "cap"],
    "databases": ["mysql", "redis", "postgresql", "mongodb", "数据库", "索引", "事务", "mvcc"],
    "networking": ["tcp", "http", "tls", "网络", "dns", "quic"],
    "frontend": ["react", "vue", "typescript", "前端", "css", "vite"],
    "ai-agent": ["agent", "langchain", "rag", "检索增强", "多智能体", "tool use"],
    "ai-infra": ["llm", "大模型", "微调", "fine-tuning", "sft", "rlhf", "moe", "训练",
                  "推理", "scaling law", "vllm", "lora", "prompt"],
    "algorithm": ["算法", "leetcode", "排序", "二叉树", "动态规划", "dfs", "bfs", "滑动窗口"],
}


def _detect_domains(query: str) -> list[str]:
    """Detect domain keys from query keywords (case-insensitive).

    Returns a list of matched domain keys, or empty if no match.
    """
    q_lower = query.lower()
    matched: list[str] = []
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in q_lower:
                matched.append(domain)
                break
    return matched


# ---------------------------------------------------------------------------
# 方案 A: Query expansion — Claude-based for vague queries
# ---------------------------------------------------------------------------
def _is_vague_query(query: str) -> bool:
    """Heuristic: a query is vague if it's short and contains
    meta-words like '相关', '知识', '有哪些', '介绍', '总结' etc.
    """
    vague_markers = ["相关", "知识", "有哪些", "介绍一下", "总结", "概述", "告诉我", "了解"]
    if len(query) < 25 and any(m in query for m in vague_markers):
        return True
    # Very short queries (< 8 chars) are almost always vague
    if len(query) < 8:
        return True
    return False


def _expand_query(query: str, config: ProjectConfig) -> list[str]:
    """Use Claude to expand a vague query into 3-5 specific sub-queries.

    Only called for vague queries to avoid wasting API calls on precise ones.
    Returns the original query + expanded sub-queries.
    """
    import json

    import anthropic

    prompt = f"""\
用户在知识库中搜索: "{query}"

这是一个比较宽泛的查询。请将其扩展为 3-5 个更具体的搜索子查询，
覆盖该主题下最重要的子方向。每个子查询应包含具体的技术关键词。

以 JSON 数组格式返回，每个元素是一个搜索字符串。只返回 JSON 数组。
示例: ["大模型微调 fine-tuning LoRA", "RAG 检索增强生成 向量数据库"]"""

    try:
        client = anthropic.Anthropic()
        message = client.messages.create(
            model=config.agent.model,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in message.content if b.type == "text").strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines)
        sub_queries = json.loads(text)
        if isinstance(sub_queries, list):
            logger.info("查询扩展: %s → %s", query, sub_queries)
            return [query] + [str(q) for q in sub_queries[:5]]
    except Exception:
        logger.warning("查询扩展失败，使用原始查询", exc_info=True)

    return [query]


def build_index(config: ProjectConfig | None = None) -> int:
    """Rebuild the Qdrant vector index from all vault entries.

    Scans all markdown files in 01-06 directories, generates
    embeddings, and upserts into Qdrant.

    Args:
        config: Project configuration. Auto-loaded if None.

    Returns:
        Number of entries indexed.
    """
    from agents.embeddings import embed_texts
    from agents.vector_store import get_vector_store

    if config is None:
        config = load_config()

    console.print("[bold blue]正在构建向量索引 (Qdrant)...[/]")

    entries = load_entries(config.vault_path)
    if not entries:
        console.print("[yellow]未找到任何知识条目。[/]")
        return 0

    # Build document texts for embedding
    texts: list[str] = []
    valid_entries: list[dict[str, Any]] = []
    for entry in entries:
        meta = entry["metadata"]
        entry_id = meta.get("id", "")
        if not entry_id:
            continue
        title = meta.get("title", "")
        tags = meta.get("tags", [])
        if isinstance(tags, list):
            tags_str = ", ".join(str(t) for t in tags)
        else:
            tags_str = str(tags)
        doc_text = f"{title}\n{tags_str}\n\n{entry['content']}"
        texts.append(doc_text)
        # Attach file_path into metadata for storage
        entry_copy = dict(entry)
        entry_copy["metadata"] = dict(meta)
        entry_copy["metadata"]["file_path"] = str(entry["path"])
        valid_entries.append(entry_copy)

    if not valid_entries:
        console.print("[yellow]未找到有效的知识条目。[/]")
        return 0

    console.print(f"[dim]正在生成 {len(texts)} 个条目的嵌入向量...[/]")
    embeddings = embed_texts(texts)

    with get_vector_store(config) as store:
        store.init_collection()
        count = store.upsert_entries(valid_entries, embeddings)

    console.print(f"[green]索引构建完成: {count} 个条目已入库 (Qdrant)[/]")
    return count


def search(
    query: str,
    filters: dict[str, str] | None = None,
    top_k: int = 5,
    config: ProjectConfig | None = None,
) -> list[dict[str, Any]]:
    """Perform semantic search across the knowledge base.

    Enhanced with:
      - Domain-aware boost: auto-detect domain from query keywords
      - Query expansion: expand vague queries into multiple sub-queries

    Args:
        query: Natural language query string.
        filters: Optional metadata filters (domain, type, depth, status).
        top_k: Maximum number of results to return.
        config: Project configuration. Auto-loaded if None.

    Returns:
        List of result dicts containing metadata, content snippet,
        and relevance score.
    """
    if config is None:
        config = load_config()

    # --- 方案 B: Domain-aware boost ---
    # If no domain filter explicitly provided, detect from query keywords
    if filters is None:
        filters = {}
    if not filters.get("domain"):
        detected = _detect_domains(query)
        if len(detected) == 1:
            # Single domain detected → apply as filter for precision
            filters["domain"] = detected[0]
            logger.info("域检测: '%s' → domain=%s", query, detected[0])

    # --- 方案 A: Query expansion ---
    # For vague queries, expand into multiple sub-queries
    if _is_vague_query(query):
        queries = _expand_query(query, config)
    else:
        queries = [query]

    # Execute search for each query, merge results
    all_results: dict[str, dict[str, Any]] = {}  # entry_id → best result
    for q in queries:
        partial = _search_single(q, filters, top_k, config)
        for r in partial:
            eid = r.get("entry_id", "")
            if not eid:
                continue
            existing = all_results.get(eid)
            if existing is None or r.get("score", 0) > existing.get("score", 0):
                all_results[eid] = r

    # Sort by score, take top_k
    results = sorted(all_results.values(), key=lambda x: x.get("score", 0), reverse=True)
    results = results[:top_k]

    # Transform into output format
    output: list[dict[str, Any]] = []
    for result in results:
        metadata = result.get("metadata", {})
        title = result.get("title", "")
        file_path = metadata.get("file_path", "")

        output.append({
            "id": result.get("entry_id", ""),
            "score": round(result.get("score", 0.0), 4),
            "title": title,
            "domain": str(metadata.get("domain", "")),
            "type": metadata.get("type", ""),
            "depth": metadata.get("depth", ""),
            "file_path": file_path,
            "snippet": title,
            "metadata": metadata,
        })

    return output


def _search_single(
    query: str,
    filters: dict[str, str] | None,
    top_k: int,
    config: ProjectConfig,
) -> list[dict[str, Any]]:
    """Execute a single search query (vector + BM25 hybrid)."""
    from agents.embeddings import embed_single
    from agents.vector_store import get_vector_store

    alpha = config.agent.search_alpha
    has_vector = True

    try:
        store = get_vector_store(config)
    except Exception as exc:
        if alpha < 1.0:
            has_vector = False
            store = None  # type: ignore[assignment]
        else:
            console.print(f"[red]无法连接向量数据库: {exc}[/]")
            return []

    if has_vector:
        try:
            stats = store.get_stats()
            if not stats.get("exists") or stats.get("points_count", 0) == 0:
                if alpha < 1.0:
                    has_vector = False
                else:
                    console.print(
                        "[yellow]索引尚未构建或为空。请先运行 `kg sync` 构建索引。[/]"
                    )
                    store.close()
                    return []
        except Exception:
            if alpha < 1.0:
                has_vector = False
            else:
                store.close()
                return []

    if alpha < 1.0:
        from agents.bm25_store import BM25Retriever

        vec_results: list[dict[str, Any]] = []
        if has_vector:
            try:
                query_embedding = embed_single(query)
                vec_results = store.search(
                    query_embedding=query_embedding,
                    top_k=top_k * 3,
                    filters=filters,
                )
            except Exception as exc:
                console.print(f"[yellow]向量搜索失败，仅使用 BM25: {exc}[/]")
            finally:
                if store is not None:
                    store.close()

        all_entries = load_entries(config.vault_path, filters=filters)
        bm25 = BM25Retriever()
        bm25.build(all_entries)
        bm25_results = bm25.query(query, top_k=top_k * 3)

        effective_alpha = alpha if has_vector else 0.0
        results = _merge_hybrid(vec_results, bm25_results, effective_alpha, top_k)
    else:
        try:
            query_embedding = embed_single(query)
            results = store.search(
                query_embedding=query_embedding,
                top_k=top_k,
                filters=filters,
            )
        except Exception as exc:
            console.print(f"[red]查询失败: {exc}[/]")
            store.close()
            return []
        store.close()

    return results


def _merge_hybrid(
    vec_results: list[dict[str, Any]],
    bm25_results: list[dict[str, float]],
    alpha: float,
    top_k: int,
) -> list[dict[str, Any]]:
    """Merge vector and BM25 results with weighted scoring.

    ``final = alpha * vec_score + (1 - alpha) * bm25_norm``

    BM25 raw scores are min-max normalized to [0, 1] before merging.

    Args:
        vec_results: Results from VectorStore.search().
        bm25_results: Results from BM25Retriever.query().
        alpha: Vector weight (0.0 = pure BM25, 1.0 = pure vector).
        top_k: Maximum number of results to return.

    Returns:
        Merged results in the same format as VectorStore.search().
    """
    # Index vector results by entry_id
    vec_by_id: dict[str, dict[str, Any]] = {}
    for r in vec_results:
        eid = r.get("entry_id", "")
        if eid:
            vec_by_id[eid] = r

    # Normalize BM25 scores to [0, 1]
    bm25_scores: dict[str, float] = {}
    if bm25_results:
        raw_scores = [r["score"] for r in bm25_results]
        min_s = min(raw_scores)
        max_s = max(raw_scores)
        span = max_s - min_s
        for r in bm25_results:
            eid = r["entry_id"]
            bm25_scores[eid] = (
                (r["score"] - min_s) / span if span > 0 else 1.0
            )

    # Union of all entry_ids
    all_ids = set(vec_by_id.keys()) | set(bm25_scores.keys())

    merged: list[dict[str, Any]] = []
    for eid in all_ids:
        vec_score = vec_by_id[eid]["score"] if eid in vec_by_id else 0.0
        bm25_score = bm25_scores.get(eid, 0.0)
        combined = alpha * vec_score + (1 - alpha) * bm25_score

        if eid in vec_by_id:
            entry = dict(vec_by_id[eid])
            entry["score"] = combined
        else:
            # BM25-only hit: minimal metadata
            entry = {
                "entry_id": eid,
                "title": eid,
                "score": combined,
                "metadata": {},
            }
        merged.append(entry)

    merged.sort(key=lambda x: x["score"], reverse=True)
    return merged[:top_k]


def print_results(results: list[dict[str, Any]], query: str) -> None:
    """Display search results using rich formatting.

    Args:
        results: List of result dicts from search().
        query: The original query string for display.
    """
    if not results:
        console.print(Panel(
            "[yellow]未找到匹配的知识条目。[/]\n"
            "建议:\n"
            "  - 尝试不同的关键词\n"
            "  - 检查索引是否已构建 (kg sync)\n"
            "  - 放宽筛选条件",
            title=f"查询: {query}",
            border_style="yellow",
        ))
        return

    table = Table(
        title=f"查询结果: \"{query}\" ({len(results)} 条匹配)",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("标题", style="bold cyan", max_width=40)
    table.add_column("域", style="green", width=16)
    table.add_column("类型", style="magenta", width=12)
    table.add_column("分数", style="yellow", width=8, justify="right")
    table.add_column("条目ID", max_width=40)

    for idx, result in enumerate(results, 1):
        table.add_row(
            str(idx),
            result["title"],
            result["domain"],
            result["type"],
            str(result["score"]),
            result["id"],
        )

    console.print(table)

    # Print content details for each result
    for idx, result in enumerate(results, 1):
        file_path = result.get("file_path", "")
        if not file_path:
            continue
        question, insights = _extract_content(file_path)
        if not question and not insights:
            continue
        parts: list[str] = []
        if question:
            parts.append(f"[bold]Question:[/] {question}")
        if insights:
            parts.append(f"[bold]Key Insights:[/]\n{insights}")
        console.print(Panel(
            "\n".join(parts),
            title=f"[bold cyan]{idx}. {result['title']}[/]",
            subtitle=f"[dim]{file_path}[/]",
            border_style="blue",
            padding=(0, 1),
        ))


def _extract_content(file_path: str) -> tuple[str, str]:
    """Extract Question and Key Insights sections from a knowledge entry.

    Returns:
        (question_text, insights_text) — either may be empty.
    """
    from pathlib import Path

    path = Path(file_path)
    if not path.exists():
        return ("", "")

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ("", "")

    question = _extract_section(text, "Question")
    insights = _extract_section(text, "Key Insights")
    return (question, insights)


def _extract_section(text: str, heading: str) -> str:
    """Extract content between ## heading and the next ## heading.

    Strips Obsidian callout markers (> [!xxx] ...) and leading '> '.
    """
    marker = f"## {heading}"
    start = text.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    # Find next ## heading
    end = text.find("\n## ", start)
    if end == -1:
        section = text[start:]
    else:
        section = text[start:end]

    # Clean up lines
    lines: list[str] = []
    for line in section.strip().splitlines():
        stripped = line.strip()
        # Skip callout type markers like "> [!question] ..."
        if stripped.startswith("> [!"):
            continue
        # Remove leading "> " (blockquote)
        if stripped.startswith("> "):
            stripped = stripped[2:]
        elif stripped == ">":
            stripped = ""
        if stripped:
            lines.append(stripped)

    return "\n".join(lines)

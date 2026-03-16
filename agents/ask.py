"""RAG question-answering over the knowledge base.

Retrieves top-k entries via hybrid search, augments with 1-hop graph
context, and streams a synthesized answer from Claude with source
citations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anthropic
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agents.config import ProjectConfig, load_config
from agents.query import search

console = Console()

_SYSTEM_PROMPT = """\
你是知识库问答助手。基于以下检索到的知识条目回答问题。
引用来源时使用 [条目ID] 格式。如果知识库中没有相关信息，明确说明。
回答应准确、简洁，并整合多个条目的信息给出综合性回答。"""


def _load_entry_content(file_path: str) -> str:
    """Read full markdown content from a knowledge entry file."""
    p = Path(file_path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _get_graph_neighbors(entry_id: str, config: ProjectConfig) -> list[str]:
    """Get 1-hop neighbor titles from the graph store.

    Returns a list of neighbor titles. If the graph is unavailable,
    returns an empty list (graceful degradation).
    """
    try:
        from agents.graph_store import get_graph_store

        gs = get_graph_store(config)
        gs.connect()
        try:
            relations = gs.get_relations(entry_id, direction="both")
            titles: list[str] = []
            for rel in relations:
                target_id = rel["target_id"]
                entry = gs.get_entry(target_id)
                if entry:
                    titles.append(entry.get("title", target_id))
                else:
                    titles.append(target_id)
            return titles
        finally:
            gs.close()
    except Exception:
        return []


def _build_context(
    results: list[dict[str, Any]],
    config: ProjectConfig,
    use_graph: bool = True,
) -> str:
    """Assemble context from top-k search results + graph neighbors.

    For each result, loads full entry content and optionally fetches
    1-hop graph neighbor titles as supplementary context.
    """
    blocks: list[str] = []

    for result in results:
        entry_id = result.get("id", "")
        title = result.get("title", "")
        domain = result.get("domain", "")
        file_path = result.get("file_path", "")

        content = _load_entry_content(file_path) if file_path else ""

        # Graph context: 1-hop neighbor titles
        neighbors_str = ""
        if use_graph and entry_id:
            neighbors = _get_graph_neighbors(entry_id, config)
            if neighbors:
                neighbors_str = ", ".join(neighbors)

        block_parts = [
            f"--- 条目: {entry_id} ---",
            f"标题: {title}",
            f"域: {domain}",
        ]
        if neighbors_str:
            block_parts.append(f"相关条目: {neighbors_str}")
        block_parts.append("")
        if content:
            block_parts.append(content)
        else:
            block_parts.append("(内容不可用)")

        blocks.append("\n".join(block_parts))

    return "\n\n".join(blocks)


def _stream_answer(
    question: str,
    context: str,
    model: str,
) -> None:
    """Stream a Claude answer to the console."""
    from agents.api_client import get_anthropic_client
    client, model = get_anthropic_client()

    messages = [
        {
            "role": "user",
            "content": f"知识库上下文:\n\n{context}\n\n---\n问题: {question}",
        },
    ]

    console.print()
    with client.messages.stream(
        model=model,
        max_tokens=4096,
        temperature=0,
        system=_SYSTEM_PROMPT,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            console.print(text, end="", highlight=False)
    console.print("\n")


def _print_sources(results: list[dict[str, Any]]) -> None:
    """Print a source reference table after the answer."""
    table = Table(title="来源条目", show_lines=True)
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("条目ID", style="cyan", max_width=45)
    table.add_column("标题", style="bold", max_width=40)
    table.add_column("域", style="green", width=16)
    table.add_column("分数", style="yellow", width=8, justify="right")

    for idx, result in enumerate(results, 1):
        table.add_row(
            str(idx),
            result.get("id", ""),
            result.get("title", ""),
            result.get("domain", ""),
            str(result.get("score", "")),
        )

    console.print(table)


def ask(
    question: str,
    top_k: int = 5,
    domain: str | None = None,
    use_graph: bool = True,
    config: ProjectConfig | None = None,
) -> None:
    """RAG question-answering: retrieve + graph context + Claude streaming.

    Args:
        question: The user's natural language question.
        top_k: Number of entries to retrieve.
        domain: Optional domain filter for retrieval.
        use_graph: Whether to include 1-hop graph neighbor context.
        config: Project configuration. Auto-loaded if None.
    """
    if config is None:
        config = load_config()

    # Step 1: Retrieve top-k entries
    filters: dict[str, str] = {}
    if domain:
        filters["domain"] = domain

    console.print(f"[dim]正在检索相关条目 (top-{top_k})...[/]")
    results = search(
        query=question,
        filters=filters if filters else None,
        top_k=top_k,
        config=config,
    )

    if not results:
        console.print(Panel(
            "[yellow]未找到相关知识条目。[/]\n"
            "建议:\n"
            "  - 尝试不同的关键词\n"
            "  - 检查索引是否已构建 (kg sync)\n"
            "  - 放宽筛选条件",
            title=f"问题: {question}",
            border_style="yellow",
        ))
        return

    console.print(f"[dim]已检索到 {len(results)} 个相关条目，正在构建上下文...[/]")

    # Step 2: Build context with graph augmentation
    context = _build_context(results, config, use_graph=use_graph)

    # Step 3: Stream answer from Claude
    console.print(Panel(f"[bold]{question}[/]", title="问题", border_style="blue"))
    _stream_answer(question, context, config.agent.model)

    # Step 4: Print source references
    _print_sources(results)


def ask_stream(
    question: str,
    top_k: int = 5,
    domain: str | None = None,
    use_graph: bool = True,
    config: ProjectConfig | None = None,
) -> Any:
    """Streaming RAG generator: yields dicts for SSE consumption.

    Yields:
        {"type": "token", "data": {"text": "..."}}
        {"type": "sources", "data": {"sources": [...]}}
        {"type": "done", "data": {}}
    """
    if config is None:
        config = load_config()

    filters: dict[str, str] = {}
    if domain:
        filters["domain"] = domain

    results = search(
        query=question,
        filters=filters if filters else None,
        top_k=top_k,
        config=config,
    )

    if not results:
        yield {"type": "token", "data": {"text": "未找到相关知识条目，请尝试不同的关键词。"}}
        yield {"type": "sources", "data": {"sources": []}}
        yield {"type": "done", "data": {}}
        return

    context = _build_context(results, config, use_graph=use_graph)

    from agents.api_client import get_anthropic_client
    client, model = get_anthropic_client()
    messages = [
        {
            "role": "user",
            "content": f"知识库上下文:\n\n{context}\n\n---\n问题: {question}",
        },
    ]

    with client.messages.stream(
        model=config.agent.model,
        max_tokens=4096,
        temperature=0,
        system=_SYSTEM_PROMPT,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield {"type": "token", "data": {"text": text}}

    sources = [
        {
            "id": r.get("id", ""),
            "title": r.get("title", ""),
            "domain": r.get("domain", ""),
            "score": r.get("score", 0),
        }
        for r in results
    ]
    yield {"type": "sources", "data": {"sources": sources}}
    yield {"type": "done", "data": {}}
